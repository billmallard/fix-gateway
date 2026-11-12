#  SPDX-License-Identifier: GPL-2.0-or-later
#
# Demo ADS-B traffic through the real GDL90 path (DEMO7, AER-5462).
#
# The demo does not write the TFC block.  It encodes a few targets as GDL90
# Heartbeat (0x00) and Traffic Report (0x14) frames, once a second, and hands
# each datagram to the stratux plugin's own decoder (gdl90.decode_datagram)
# and TrafficWriter: the code a Stratux on the wire drives, unchanged.  So
# what the map draws was framed, checksummed, decoded and slotted the way
# real traffic is (brief makerplane/briefs/demo_mode_redesign.md section 8).
#
# In process, not over UDP to a stratux plugin: the demo refuses to run
# beside any other source that writes its keys (brief section 5), and stratux
# writes attitude and GS as well as traffic.
#
# The targets orbit a centre -- ownship's position and altitude when the
# first frame is built, or the point given to center_on(), the hold -- at
# fixed radii, so they stay around it for as long as the demo runs.  They use
# GDL90's self-assigned address type (G§3.5.1.2, t = 1), so no target carries
# a real aircraft's ICAO address, and call signs that say DEMO.

import math
import time

from fixgw import geo
from fixgw.plugins.stratux import gdl90
from fixgw.plugins.stratux.traffic import TrafficWriter

SEND_INTERVAL = 1.0  # s, one Heartbeat and one report per target

ADDR_SELF_ASSIGNED = 1
EMITTER_LIGHT = 1  # G§3.5.1.10
MISC_AIRBORNE_TRUE_TRACK = 0x9  # G§3.5.1.5: airborne, updated, true track
NIC = 8
NACP = 9


# One target on a circle about the centre.  Bearing grows clockwise, so a
# clockwise orbit tracks bearing + 90.  Altitude is the centre's plus
# alt_offset_ft, swinging by alt_swing_ft over swing_s when that is set.
class Orbit(object):
    def __init__(
        self,
        address,
        callsign,
        radius_nm,
        gs_kt,
        start_bearing,
        clockwise,
        alt_offset_ft,
        alt_swing_ft=0.0,
        swing_s=1.0,
    ):
        self.address = address
        self.callsign = callsign
        self.radius_nm = radius_nm
        self.gs_kt = gs_kt
        self.start_bearing = start_bearing
        self.clockwise = clockwise
        self.alt_offset_ft = alt_offset_ft
        self.alt_swing_ft = alt_swing_ft
        self.swing_s = swing_s

    # (lat, lon, alt_ft, gs_kt, vs_fpm, track) at t seconds.
    def state(self, t, lat, lon, alt_ft):
        turn = math.degrees(self.gs_kt / self.radius_nm * t / 3600.0)
        sign = 1 if self.clockwise else -1
        bearing = geo.wrap360(self.start_bearing + sign * turn)
        tlat, tlon = geo.destination_point(lat, lon, bearing, self.radius_nm)
        w = 2 * math.pi / self.swing_s
        alt = alt_ft + self.alt_offset_ft + self.alt_swing_ft * math.sin(w * t)
        vs = self.alt_swing_ft * w * math.cos(w * t) * 60.0
        track = geo.wrap360(bearing + sign * 90.0)
        return tlat, tlon, alt, self.gs_kt, vs, track


# Three targets: one inside the hold, one level above it, one wider and
# climbing and descending through the hold's altitude (the trend arrow shows
# at 500 fpm, so its 785 fpm peak shows it).
TARGETS = (
    Orbit(0xDE0001, "DEMO1", 3.0, 100, 0.0, True, 1000),
    Orbit(0xDE0002, "DEMO2", 5.0, 140, 120.0, False, -500, 500, 240.0),
    Orbit(0xDE0003, "DEMO3", 7.0, 120, 240.0, True, 2500),
)


# Truncated toward zero, as the G§3.5.2 worked example is: 44.90708 is sent
# as 0x1FEF15 and -122.99488 as 0xA88978, where rounding gives 0x1FEF16 and
# 0xA88977.  Either is within one LSB (2.1e-5 deg, about 2 m).
def _latlon_raw(deg):
    return int(deg * (1 << 23) / 180.0) & 0xFFFFFF


# A clear 0x14 message (ID included) from FIX-unit values, the inverse of
# gdl90.parse_report for the fields this emitter uses (G§3.5.1).
def encode_report(
    address,
    lat,
    lon,
    alt_ft,
    gs_kt,
    vs_fpm,
    track,
    callsign,
    atype=ADDR_SELF_ASSIGNED,
    misc=MISC_AIRBORNE_TRUE_TRACK,
    nic=NIC,
    nacp=NACP,
    cat=EMITTER_LIGHT,
):
    ddd = min(max(round((alt_ft + 1000) / 25.0), 0), 0xFFE)  # 0xFFF: no data
    hhh = min(max(round(gs_kt), 0), 0xFFE)  # 0xFFF: no data
    vvv = min(max(round(vs_fpm / 64.0), -510), 510) & 0xFFF  # +-32,640 fpm
    tt = round(track * 256 / 360.0) % 256
    b = bytearray(28)
    b[0] = gdl90.TRAFFIC
    b[1] = atype  # alert status 0: no threat logic in the demo either
    b[2:5] = address.to_bytes(3, "big")
    b[5:8] = _latlon_raw(lat).to_bytes(3, "big")
    b[8:11] = _latlon_raw(lon).to_bytes(3, "big")
    b[11] = ddd >> 4
    b[12] = ((ddd & 0xF) << 4) | misc
    b[13] = (nic << 4) | nacp
    b[14] = hhh >> 4
    b[15] = ((hhh & 0xF) << 4) | (vvv >> 8)
    b[16] = vvv & 0xFF
    b[17] = tt
    b[18] = cat
    b[19:27] = callsign.ljust(8)[:8].encode("ascii")
    b[27] = 0  # no emergency
    return bytes(b)


# A clear Heartbeat (G§3.1): GPS position valid, UAT initialized, UTC OK, the
# UTC seconds since midnight, no message counts.
def encode_heartbeat(utc_s):
    ts = int(utc_s) % 86400
    return bytes(
        [gdl90.HEARTBEAT, 0x81, ((ts >> 16) & 1) << 7 | 0x01, ts & 0xFF, (ts >> 8) & 0xFF, 0, 0]
    )


# A database item whose writes are the demo's.  The writer registry
# (database._writer) credits the innermost fixgw.plugins module on the stack,
# which for a call made in TrafficWriter is stratux.  Making the set, and the
# flag assignments, from this module puts the demo innermost, so the registry
# names demo as the TFC block's writer, as it is (brief section 5).
class _DemoItem(object):
    __slots__ = ("_item",)

    def __init__(self, item):
        object.__setattr__(self, "_item", item)

    def set(self, *args, **kwargs):
        return self._item.set(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._item, name)

    def __setattr__(self, name, value):
        setattr(self._item, name, value)


class DemoTraffic(object):
    def __init__(self, get_item, log, clock=None, wall=None, targets=TARGETS):
        self.log = log
        self.clock = clock or time.monotonic
        self.wall = wall or time.time
        self.targets = targets
        self._get = get_item
        self.writer = TrafficWriter(
            lambda key: _DemoItem(get_item(key)), log, clock=self.clock
        )
        self.center = None  # (lat, lon, alt_ft)
        self.t0 = None
        self.next_send = self.clock()

    # Put the targets around a point: the hold the loop flies, say.
    def center_on(self, lat, lon, alt_ft):
        self.center = (lat, lon, alt_ft)

    # Ownship's position and altitude, or None while any of them is not good.
    def _ownship(self):
        out = []
        for key in ("LAT", "LONG", "ALT"):
            v = self._get(key).value
            if v[2] or v[3] or v[4]:  # old, bad, fail
                return None
            out.append(v[0])
        return tuple(out)

    def datagram(self, t):
        lat, lon, alt = self.center
        msgs = [encode_heartbeat(self.wall())]
        for target in self.targets:
            tlat, tlon, talt, gs, vs, trk = target.state(t, lat, lon, alt)
            msgs.append(
                encode_report(target.address, tlat, tlon, talt, gs, vs, trk, target.callsign)
            )
        return b"".join(gdl90.encode(m) for m in msgs)

    # Call often; sends on a monotonic deadline and ticks the writer every
    # time, so eviction and liveness run as they do in the stratux plugin.
    def step(self):
        now = self.clock()
        if now >= self.next_send:
            self.next_send = now + SEND_INTERVAL
            if self.center is None:
                self.center = self._ownship()
            if self.center is not None:
                if self.t0 is None:
                    self.t0 = now
                for msg in gdl90.decode_datagram(self.datagram(now - self.t0)):
                    self.writer.handle(msg)
        self.writer.tick()
