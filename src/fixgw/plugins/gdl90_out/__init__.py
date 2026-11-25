# GDL-90 output to EFB apps over Wi-Fi UDP (AER-6142, BACKLOG EFB1).
#
# Reads FIX keys and writes none.  The design, the key-to-field mapping and
# the validity rules are in doc/plugins/gdl90_out.md.  The only network input
# is ForeFlight's discovery broadcast, which decides where to send and nothing
# else: no byte from a tablet reaches the database.

import ipaddress
import json
import re
import select
import socket
import threading
import time
from collections import OrderedDict

import fixgw
import fixgw.plugin as plugin
from fixgw.plugins.gdl90_out import messages
from fixgw.plugins.stratux import gdl90
from fixgw.plugins.stratux.traffic import resolve_ownship_address

DISCOVERY_PORT = 63093  # FF "ForeFlight Broadcast"
GDL90_PORT = 4000
DISCOVERY_MAX = 512  # bytes; ForeFlight's announcement is about 50
DATAGRAM_MAX = 1400  # FF: stay under the 1,500-byte MTU, headers included
SECOND_S = 1.0
AHRS_S = 0.2  # FF: AHRS at 5 Hz
DEVICE_NAME = "MkrPlane"


def _unicast(addr):
    return not (
        addr.is_multicast
        or addr.is_unspecified
        or addr == ipaddress.IPv4Address("255.255.255.255")
    )


def parse_allow(value, log):
    """The networks a discovered client may be on, or None for any.

    An entry that does not parse must not open the filter: the result then
    matches nobody, and the error is logged (the stratux plugin's `source`
    rule).
    """
    if value is None or value == "" or value == []:
        return None
    entries = value if isinstance(value, list) else [value]
    nets = []
    for entry in entries:
        try:
            nets.append(ipaddress.IPv4Network(str(entry).strip(), strict=False))
        except ValueError:
            log.error(
                "discovery_allow: %r is not an IPv4 network; accepting no "
                "discovered client" % (entry,)
            )
            return []
    return nets


def parse_client(entry):
    """(ip, port) from "ip" or "ip:port", or None if it is not a unicast
    IPv4 address and a port in 1-65535."""
    text = str(entry).strip()
    host, _, port = text.partition(":")
    try:
        addr = ipaddress.IPv4Address(host)
        port = int(port) if port else GDL90_PORT
    except ValueError:
        return None
    if not _unicast(addr) or not 1 <= port <= 65535:
        return None
    return str(addr), port


def parse_discovery(datagram):
    """The GDL-90 port a ForeFlight announcement asks for, or None.

    Strict, because the sender is anyone on the network: bounded size, UTF-8,
    a JSON object whose App is exactly "ForeFlight" and whose GDL90.port is an
    integer in 1-65535 (FF "ForeFlight Broadcast").
    """
    if not datagram or len(datagram) > DISCOVERY_MAX:
        return None
    try:
        doc = json.loads(datagram.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(doc, dict) or doc.get("App") != "ForeFlight":
        return None
    gdl = doc.get("GDL90")
    if not isinstance(gdl, dict):
        return None
    port = gdl.get("port")
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    return port


class Clients(object):
    """Where to send: fixed clients, plus discovered ones that expire."""

    def __init__(self, log, fixed=(), allow=None, timeout_s=60.0, limit=4):
        self.log = log
        self.fixed = list(fixed)
        self.allow = allow
        self.timeout_s = float(timeout_s)
        self.limit = int(limit)
        self.discovered = OrderedDict()  # (ip, port) -> expiry, monotonic
        self.accepted = 0
        self.malformed = 0
        self.refused = 0
        self._refused_logged = set()

    def announce(self, datagram, sender_ip, now):
        """Handle one datagram on the discovery port.  True if it added or
        refreshed a client."""
        port = parse_discovery(datagram)
        if port is None:
            self.malformed += 1
            return False
        try:
            addr = ipaddress.IPv4Address(sender_ip)
        except ValueError:
            return self._refuse(sender_ip, "not an IPv4 address")
        if not _unicast(addr):
            return self._refuse(sender_ip, "not a unicast address")
        if self.allow is not None and not any(addr in n for n in self.allow):
            return self._refuse(sender_ip, "not in discovery_allow")
        key = (str(addr), port)
        self.expire(now)
        if key not in self.discovered and len(self.discovered) >= self.limit:
            return self._refuse(sender_ip, "max_clients (%d) reached" % self.limit)
        if key not in self.discovered:
            self.log.info("gdl90_out: client %s:%d discovered" % key)
        self.discovered[key] = now + self.timeout_s
        self.accepted += 1
        return True

    def _refuse(self, sender_ip, why):
        self.refused += 1
        if sender_ip not in self._refused_logged and len(self._refused_logged) < 16:
            self._refused_logged.add(sender_ip)
            self.log.warning("gdl90_out: discovery from %s refused: %s" % (sender_ip, why))
        return False

    def expire(self, now):
        for key, until in list(self.discovered.items()):
            if now >= until:
                del self.discovered[key]
                self.log.info("gdl90_out: client %s:%d timed out" % key)

    def targets(self, now):
        self.expire(now)
        out = list(self.fixed)
        for key in self.discovered:
            if key not in out:
                out.append(key)
        return out


class FixReader(object):
    """Reads keys with the plugin's one validity rule: the database holds the
    key, a writer has written it, and it is not old, bad or failed."""

    def __init__(self, get_item, log):
        self.get_item = get_item
        self.log = log
        self.items = {}
        self.missing = set()

    def item(self, key):
        if key in self.items:
            return self.items[key]
        try:
            it = self.get_item(key)
        except KeyError:
            it = None
            if key not in self.missing:
                self.missing.add(key)
                self.log.warning("gdl90_out: no %s in the database; sending it as invalid" % key)
        self.items[key] = it
        return it

    def written(self, key):
        it = self.item(key)
        return it is not None and it.written

    def valid(self, key):
        it = self.item(key)
        if it is None or not it.written:
            return None
        value, _annunc, old, bad, fail, _secfail = it.value
        if old or bad or fail:
            return None
        return value

    def position(self):
        """(lat, lon) when the panel has a valid position, else None.  A
        GPS_FIX_TYPE that a source writes must also be valid and 2D or
        better; one nothing writes is not consulted."""
        lat, lon = self.valid("LAT"), self.valid("LONG")
        if lat is None or lon is None:
            return None
        if self.written("GPS_FIX_TYPE"):
            fix = self.valid("GPS_FIX_TYPE")
            if fix is None or fix < 2:
                return None
        return lat, lon


FT_PER_M = 1 / 0.3048
# The geometric MSL height every position source writes (GALT1/TALTSRC1).
FALLBACK_GEO_KEY = "TALT"
TRAFFIC_KEY = re.compile(r"^TFC(\d+)TGT$")


class Encoder(object):
    """The messages for one second and for one AHRS tick, from the bus."""

    def __init__(self, reader, config, preferences, log, keys=()):
        self.r = reader
        self.log = log
        self.ahrs_on = bool(config.get("ahrs", True))
        self.traffic_on = bool(config.get("traffic", False))
        self.geo_key = config.get("geo_altitude_key", "GPS_ELLIPSOID_ALT") or None
        datum = str(config.get("geo_altitude_datum", "ellipsoid")).strip().lower()
        if datum not in ("ellipsoid", "msl"):
            log.error("geo_altitude_datum %r is not ellipsoid or msl; using ellipsoid" % datum)
            datum = "ellipsoid"
        self.msl = datum == "msl"
        self.geo_fallback_logged = False
        self.callsign = config.get("callsign", "") or ""
        self.emitter = int(config.get("emitter_category", 1))
        self.internet_policy = int(config.get("internet_policy", 0)) & 0x03
        address, _source = resolve_ownship_address(config, preferences, log)
        self.address = address or 0
        self.long_name = "%s %s" % (DEVICE_NAME, fixgw.__version__)
        self.slots = []
        if self.traffic_on:
            self.slots = sorted(
                int(m.group(1))
                for m in (TRAFFIC_KEY.match(k) for k in keys)
                if m
            )
            if not self.slots:
                log.error("traffic: true but the database has no TFC block; sending no traffic")

    def second(self, now_utc):
        pos = self.r.position()
        out = [messages.heartbeat(pos is not None, now_utc % 86400)]
        out.append(self.ownship(pos))
        geo_key, msl = self.geo_source()
        geo = self.geo_altitude(geo_key)
        if geo is not None:
            out.append(geo)
        out.append(
            messages.foreflight_id(
                DEVICE_NAME,
                self.long_name,
                msl_datum=msl,
                internet_policy=self.internet_policy,
            )
        )
        if self.traffic_on:
            out.extend(self.traffic())
        return out

    def ownship(self, pos):
        v = self.r.valid
        lat, lon = pos if pos is not None else (None, None)
        nacp = 0
        epu_ft = v("GPS_ACCURACY_HORIZ")
        if pos is not None and epu_ft is not None:
            nacp = messages.nacp_code(epu_ft / FT_PER_M)
        return messages.report(
            gdl90.OWNSHIP,
            lat=lat,
            lon=lon,
            nic=0,
            nacp=nacp,
            alt=v("PALT"),
            gs=v("GS"),
            vs=v("VS"),
            track=v("TRACK"),
            airborne=True,
            address=self.address,
            emitter=self.emitter,
            callsign=self.callsign,
        )

    def geo_source(self):
        """(key, msl) the geo altitude and the ID message's datum come from.

        The configured key once any writer has written it. Until then TALT,
        datum MSL, when TALT has been written: the GNX, gpsd and navigator
        moved their MSL height off GPS_ELLIPSOID_ALT to TALT (GALT1/TALTSRC1,
        AER-6458), and installs keep their copied config naming
        GPS_ELLIPSOID_ALT, which only mavlink writes now."""
        if self.geo_key is None or self.r.written(self.geo_key):
            return self.geo_key, self.msl
        if self.geo_key != FALLBACK_GEO_KEY and self.r.written(FALLBACK_GEO_KEY):
            if not self.geo_fallback_logged:
                self.geo_fallback_logged = True
                self.log.info("gdl90_out: %s has never been written; sending %s as MSL geo altitude"
                              % (self.geo_key, FALLBACK_GEO_KEY))
            return FALLBACK_GEO_KEY, True
        return self.geo_key, self.msl

    def geo_altitude(self, key=None):
        key = self.geo_key if key is None else key
        if key is None:
            return None
        alt = self.r.valid(key)
        if alt is None:
            return None
        vfom_ft = self.r.valid("GPS_ACCURACY_VERTICAL")
        vfom_m = None if vfom_ft is None else vfom_ft / FT_PER_M
        return messages.geo_altitude(alt, vfom_m)

    def ahrs(self):
        v = self.r.valid
        return messages.foreflight_ahrs(
            roll=v("ROLL"),
            pitch=v("PITCH"),
            heading_mag=v("HEAD"),
            ias=v("IAS"),
            tas=v("TAS"),
        )

    def traffic(self):
        out = []
        for n in self.slots:
            msg = self.target(n)
            if msg is not None:
                out.append(msg)
        return out

    def target(self, n):
        # A slot holds a target when TFCkTGT != -1 and is neither old nor
        # failed (database/traffic.yaml).  A bad field goes out as the ICD's
        # "not available" code.
        p = "TFC%d" % n
        tgt = self.r.valid(p + "TGT")
        if tgt is None or tgt == -1:
            return None
        v = self.r.valid
        lat, lon = v(p + "LAT"), v(p + "LON")
        track = v(p + "TRK")
        ttype = v(p + "TTYP")
        if track is not None and not ttype:
            track = None
        return messages.report(
            gdl90.TRAFFIC,
            lat=lat,
            lon=lon,
            nic=v(p + "NIC") or 0,
            nacp=v(p + "NACP") or 0,
            alt=v(p + "ALT"),
            gs=v(p + "GS"),
            vs=v(p + "VS"),
            track=track,
            track_type=ttype or 0,
            airborne=bool(v(p + "AIR")),
            extrapolated=bool(v(p + "EXTR")),
            alert=v(p + "ALRT") or 0,
            address_type=v(p + "ATYP") or 0,
            address=v(p + "ADDR") or 0,
            emitter=v(p + "CAT") or 0,
            callsign=v(p + "CS") or "",
            emergency=v(p + "EMRG") or 0,
        )


def pack(frames, limit=DATAGRAM_MAX):
    """Framed messages into datagrams of at most `limit` bytes, in order."""
    out, cur = [], b""
    for f in frames:
        if cur and len(cur) + len(f) > limit:
            out.append(cur)
            cur = b""
        cur += f
    if cur:
        out.append(cur)
    return out


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__(daemon=True)
        self.parent = parent
        self.log = parent.log
        self.getout = False
        config = getattr(parent, "config", None) or {}
        self.status = parent.status

        fixed = []
        for entry in config.get("clients", None) or []:
            c = parse_client(entry)
            if c is None:
                self.log.error("clients: %r is not a unicast IPv4 address[:port]; skipped" % (entry,))
            else:
                fixed.append(c)
        self.clients = Clients(
            self.log,
            fixed=fixed,
            allow=parse_allow(config.get("discovery_allow"), self.log),
            timeout_s=config.get("client_timeout_s", 60),
            limit=config.get("max_clients", 4),
        )
        self.encoder = Encoder(
            FixReader(parent.db_get_item, self.log),
            config,
            plugin.preferences,
            self.log,
            keys=parent.db_list(),
        )

        # No SO_BROADCAST: the kernel refuses a send to a broadcast address.
        self.tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx = None
        if config.get("discovery", True):
            self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.rx.bind(
                (
                    config.get("discovery_host", ""),
                    int(config.get("discovery_port", DISCOVERY_PORT)),
                )
            )
        self.sent = 0
        self.send_errors = 0
        self._error_logged = set()
        self.monotonic = time.monotonic
        self.wall_clock = time.time

    def run(self):
        next_second = next_ahrs = self.monotonic()
        while not self.getout:
            now = self.monotonic()
            wait = max(0.0, min(next_second, next_ahrs) - now)
            self.listen(min(wait, 0.2))
            now = self.monotonic()
            if now >= next_second:
                next_second = max(next_second + SECOND_S, now)
                self.tick_second(now)
            if self.encoder.ahrs_on and now >= next_ahrs:
                next_ahrs = max(next_ahrs + AHRS_S, now)
                self.send(self.encoder.ahrs, now)
            elif not self.encoder.ahrs_on:
                next_ahrs = next_second
        self.tx.close()
        if self.rx is not None:
            self.rx.close()

    def listen(self, timeout):
        if self.rx is None:
            if timeout > 0:
                time.sleep(timeout)
            return
        ready, _, _ = select.select([self.rx], [], [], timeout)
        if not ready:
            return
        try:
            datagram, adr = self.rx.recvfrom(DISCOVERY_MAX + 1)
        except OSError:
            return
        self.clients.announce(datagram, adr[0], self.monotonic())

    def tick_second(self, now):
        self.send(lambda: self.encoder.second(self.wall_clock()), now)
        self.status["Clients"] = ", ".join("%s:%d" % c for c in self.clients.targets(now)) or "none"
        self.status["Datagrams sent"] = self.sent
        self.status["Send errors"] = self.send_errors
        self.status["Discovery accepted"] = self.clients.accepted
        self.status["Discovery malformed"] = self.clients.malformed
        self.status["Discovery refused"] = self.clients.refused

    # build() makes the clear messages; it is not called with no client, so
    # the bus is not read and nothing is sent.
    def send(self, build, now):
        targets = self.clients.targets(now)
        if not targets:
            return
        msgs = build()
        if isinstance(msgs, bytes):
            msgs = [msgs]
        for dgram in pack([gdl90.encode(m) for m in msgs]):
            for target in targets:
                try:
                    self.tx.sendto(dgram, target)
                    self.sent += 1
                except OSError as e:
                    self.send_errors += 1
                    if target not in self._error_logged:
                        self._error_logged.add(target)
                        self.log.warning("gdl90_out: send to %s:%d failed: %s" % (target + (e,)))

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.status = OrderedDict()
        self.thread = MainThread(self)

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(1.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return self.status
