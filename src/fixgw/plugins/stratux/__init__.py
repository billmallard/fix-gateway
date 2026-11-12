import ipaddress
import threading
import time
from collections import OrderedDict
import fixgw.plugin as plugin
from fixgw.plugins.stratux import gdl90
from fixgw.plugins.stratux.traffic import TrafficWriter, parse_ownship_address
import socket
import struct
import math

# The receive timeout paces the traffic writer's once-a-second tick when no
# datagrams arrive (eviction and liveness must not wait for input).
RECV_TIMEOUT = 0.25

# How often the count of datagrams dropped by the source filter is logged.
DROP_REPORT_S = 60.0


def parse_source(value, log):
    """The sender address the plugin accepts, or None to accept any.

    A value that is set but is not an IPv4 address (the socket is IPv4) must
    not open the filter: it returns a filter that matches no sender, so
    attitude goes stale and the error is in the log.
    """
    if value is None or str(value).strip() == "":
        return None
    try:
        return str(ipaddress.IPv4Address(str(value).strip()))
    except ValueError:
        log.error(
            "source: %r is not an IPv4 address; dropping every datagram" % (value,)
        )
        return ""


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__()
        print("running stratux plugin")
        self.getout = False  # indicator for when to stop
        self.parent = parent  # parent plugin object
        self.log = parent.log  # simplifies logging
        config = getattr(parent, "config", None) or {}

        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind((config.get("host", ""), int(config.get("port", 4000))))
        self.s.settimeout(RECV_TIMEOUT)

        # AHRS over Wi-Fi is a network input to the attitude display: with
        # source set, only that sender is decoded.
        self.source = parse_source(config.get("source"), self.log)
        self.dropped = 0
        self.dropped_reported = 0
        self.drop_senders = set()
        self.drop_report_at = time.monotonic() + DROP_REPORT_S

        self.traffic = None
        if config.get("traffic", True):
            try:
                parent.db_get_item("TFC1TGT")
            except KeyError:
                self.log.error(
                    "traffic: true but the database has no TFC block "
                    "(database/traffic.yaml); not writing traffic"
                )
            else:
                self.traffic = TrafficWriter(
                    parent.db_get_item,
                    self.log,
                    evict_s=config.get("evict_s", 10),
                    ownship_address=parse_ownship_address(
                        config.get("ownship_address"), self.log
                    ),
                )

    def run(self):

        while not self.getout:
            try:
                datagram, adr = self.s.recvfrom(8192)
            except socket.timeout:
                datagram = b""
            else:
                if not self.accept(adr):
                    datagram = b""
            self.report_drops()
            for msg in gdl90.decode_datagram(datagram):
                self.dispatch(msg)
            if self.traffic is not None:
                self.traffic.tick()

        self.s.close()
        self.running = False

    def accept(self, adr):
        if self.source is None or adr[0] == self.source:
            return True
        self.dropped += 1
        self.parent.status["Dropped datagrams"] = self.dropped
        if adr[0] not in self.drop_senders and len(self.drop_senders) < 16:
            self.drop_senders.add(adr[0])
            self.log.warning(
                "dropping GDL90 from %s: source is %s"
                % (adr[0], self.source or "invalid")
            )
        return False

    def report_drops(self):
        now = time.monotonic()
        if now < self.drop_report_at:
            return
        self.drop_report_at = now + DROP_REPORT_S
        if self.dropped != self.dropped_reported:
            self.log.warning(
                "dropped %d GDL90 datagrams not from %s (%d in the last %.0f s)"
                % (
                    self.dropped,
                    self.source or "a valid source",
                    self.dropped - self.dropped_reported,
                    DROP_REPORT_S,
                )
            )
            self.dropped_reported = self.dropped

    def dispatch(self, msg):
        if len(msg) < 1:
            return

        if msg[0] == 0x4C:
            roll = struct.unpack(">h", msg[4:6])[0] / 10.0
            pitch = struct.unpack(">h", msg[6:8])[0] / 10.0
            heading = struct.unpack(">h", msg[8:10])[0] / 10.0
            slipskid = struct.unpack(">h", msg[10:12])[0] / 10.0
            yawrate = struct.unpack(">h", msg[12:14])[0] / 10.0  # noqa: F841
            g = struct.unpack(">h", msg[14:16])[0] / 10.0  # noqa: F841
            ias = struct.unpack(">h", msg[16:18])[0] / 10.0  # noqa: F841
            alt = struct.unpack(">h", msg[18:20])[0] - 5000.5
            vs = struct.unpack(">h", msg[20:22])[0]

            self.parent.db_write("PITCH", pitch)
            self.parent.db_write("ROLL", roll)
            self.parent.db_write("HEAD", heading)

            self.parent.db_write("ALAT", -math.sin(slipskid * math.pi / 180))
            self.parent.db_write("ALT", alt)
            self.parent.db_write("VS", vs)
        elif msg[0] == 0x0A and len(msg) >= 16:
            # ownship report; horizontal velocity is ground speed, not IAS
            tmp = struct.unpack("BB", msg[14:16])
            gnd_speed = (tmp[0] << 4) | (tmp[1] >> 4)
            # 0xFFF is "no horizontal velocity available": skip the write so
            # GS goes stale instead of reading 4095 kt
            if gnd_speed != 0xFFF:
                self.parent.db_write("GS", gnd_speed)

        if self.traffic is not None:
            self.traffic.handle(msg)

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
