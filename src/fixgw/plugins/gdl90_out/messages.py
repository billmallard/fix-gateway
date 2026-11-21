# GDL-90 message encoders for the gdl90_out plugin (AER-6142).
#
# Pure functions of plain values: no database, no socket.  Each returns the
# clear message (ID + data); gdl90.encode() frames it.  Citations are to the
# GDL 90 Data Interface Specification, 560-1058-00 Rev A, written G§, and to
# ForeFlight's GDL 90 Extended Specification, written FF.  The framing, CRC
# and the decoder used as the test oracle are the stratux plugin's.

import math
import struct

from fixgw.plugins.stratux import gdl90

FOREFLIGHT = 0x65
FF_ID = 0x00
FF_AHRS = 0x01

ALT_INVALID = 0xFFF
GS_INVALID = 0xFFF
GS_MAX = 0xFFE
VS_INVALID = 0x800
VS_MAX = 0x1FD  # 32,576 ft/min
VS_HOLD = 0x1FE  # "> 32,576 climb"; -0x1FE is 0xE02, "> 32,576 descend"
VFOM_NA = 0x7FFF
VFOM_MAX = 0x7FFE

# Misc field "m" (G§3.5.1.5, Table 9).
TT_INVALID = 0
TT_TRUE_TRACK = 1
AIRBORNE = 0x08

# FF AHRS special values.
AHRS_ANGLE_INVALID = 0x7FFF
AHRS_HEADING_INVALID = 0xFFFF
AHRS_SPEED_INVALID = 0xFFFF
HEADING_MAGNETIC = 0x8000


def heartbeat(gps_valid, seconds_since_midnight, utc_ok=False):
    # G§3.1.  Bit 0 of status 1, "UAT Initialized", is set in every
    # heartbeat.  The 17-bit time stamp: bit 16 is status 2 bit 7, bits
    # 15-0 follow LS byte first.  Message counts are 0: nothing is received.
    ts = int(seconds_since_midnight) % 86400
    status1 = 0x01 | (0x80 if gps_valid else 0)
    status2 = (0x80 if ts & 0x10000 else 0) | (0x01 if utc_ok else 0)
    return bytes([gdl90.HEARTBEAT, status1, status2, ts & 0xFF, (ts >> 8) & 0xFF, 0, 0])


# 24-bit semicircle (G§3.5.1.3), truncated toward zero as the ICD's worked
# example (Table 12) is: 44.90708 is 0x1FEF15 there, where rounding gives
# 0x1FEF16.  +180 is not representable; it wraps to -180, the same meridian.
def semicircle(deg):
    return int(deg * (1 << 23) / 180.0) & 0xFFFFFF


def altitude_code(ft):
    # G§3.5.1.4: (ddd * 25) - 1,000 ft; 0xFFF is invalid, so a valid
    # altitude holds at 0xFFE (+101,350 ft) and at 0x000 (-1,000 ft).
    if ft is None:
        return ALT_INVALID
    return min(max(int(round((ft + 1000.0) / 25.0)), 0), 0xFFE)


def gs_code(kt):
    # G§3.5.1.7: 1 kt; holds at 0xFFE, 0xFFF is "no data".
    if kt is None:
        return GS_INVALID
    return min(max(int(round(kt)), 0), GS_MAX)


def vs_code(fpm):
    # G§3.5.1.8: signed 12-bit, 64 ft/min.  Beyond +-32,576 the value holds
    # at the +-32,640 code; 0x800 is "no data".
    if fpm is None:
        return VS_INVALID
    v = int(round(fpm / 64.0))
    if v > VS_MAX:
        v = VS_HOLD
    elif v < -VS_MAX:
        v = -VS_HOLD
    return v & 0xFFF


def track_code(deg):
    # G§3.5.1.9: 360/256 deg, nearest.
    return int(round((deg % 360.0) * 256.0 / 360.0)) % 256


def callsign_bytes(text):
    # G§3.5.1.11: '0'-'9', 'A'-'Z', space-padded to 8.  Anything else is
    # dropped rather than sent.
    clean = "".join(c for c in str(text or "").upper() if c.isascii() and c.isalnum())
    return clean[:8].ljust(8).encode("ascii")


def report(
    msg_id,
    *,
    lat=None,
    lon=None,
    nic=0,
    nacp=0,
    alt=None,
    gs=None,
    vs=None,
    track=None,
    track_type=TT_TRUE_TRACK,
    airborne=True,
    extrapolated=False,
    alert=0,
    address_type=0,
    address=0,
    emitter=0,
    callsign="",
    emergency=0,
):
    """An Ownship (0x0A) or Traffic (0x14) Report, G§3.5.1.

    None for lat/lon is "no valid position": lat, lon, NIC and NACp all 0
    (G§3.5.1.3).  None for alt, gs or vs sends that field's invalid code;
    None for track sends tt 0 with the "not valid" track type.
    """
    if lat is None or lon is None:
        ll = nn = 0
        nic = nacp = 0
    else:
        ll, nn = semicircle(lat), semicircle(lon)
    ddd = altitude_code(alt)
    misc = (AIRBORNE if airborne else 0) | (0x04 if extrapolated else 0)
    if track is None:
        tt = 0
        misc |= TT_INVALID
    else:
        tt = track_code(track)
        misc |= track_type & 0x03
    hhh = gs_code(gs)
    vvv = vs_code(vs)
    out = bytearray([msg_id, ((alert & 0x0F) << 4) | (address_type & 0x0F)])
    out += (address & 0xFFFFFF).to_bytes(3, "big")
    out += ll.to_bytes(3, "big") + nn.to_bytes(3, "big")
    out += bytes([ddd >> 4, ((ddd & 0x0F) << 4) | misc])
    out += bytes([((nic & 0x0F) << 4) | (nacp & 0x0F)])
    out += bytes([hhh >> 4, ((hhh & 0x0F) << 4) | (vvv >> 8), vvv & 0xFF])
    out += bytes([tt, emitter & 0xFF])
    out += callsign_bytes(callsign)
    out += bytes([(emergency & 0x0F) << 4])
    return bytes(out)


# Table 10 (G§3.5.1.6), as upper bounds in metres, best code first.  A code
# applies when the uncertainty is strictly below its bound.
NACP_BOUNDS_M = sorted(gdl90.NACP_EPU_M.items(), key=lambda kv: kv[1])


def nacp_code(epu_m):
    if epu_m is None or not math.isfinite(epu_m) or epu_m < 0:
        return 0
    for code, bound in NACP_BOUNDS_M:
        if epu_m < bound:
            return code
    return 0


def geo_altitude(ft, vfom_m=None, vwarn=False):
    # G§3.8: signed 16-bit, 5 ft.  Vertical metrics: warning in the MSB,
    # VFOM in metres in the rest, 0x7FFF "not available", holding at 0x7FFE.
    dddd = min(max(int(round(ft / 5.0)), -32768), 32767)
    if vfom_m is None:
        vfom = VFOM_NA
    else:
        vfom = min(max(int(round(vfom_m)), 0), VFOM_MAX)
    metrics = (0x8000 if vwarn else 0) | vfom
    return struct.pack(">BhH", gdl90.GEO_ALTITUDE, dddd, metrics)


def _utf8_field(text, size):
    # Fixed-size UTF-8 field, space-padded, never cutting a character.
    raw = str(text).encode("utf-8")
    while len(raw) > size:
        text = text[:-1]
        raw = str(text).encode("utf-8")
    return raw.ljust(size, b" ")


def foreflight_id(name, long_name, msl_datum=False, internet_policy=0, serial=None):
    # FF "ID Message": version 1; serial 0xFF.. when invalid; 8-byte name,
    # 16-byte long name; capability bit 0 is the 0x0B datum (1 = MSL), bits
    # 1-2 the internet policy, the rest 0.
    caps = (1 if msl_datum else 0) | ((internet_policy & 0x03) << 1)
    serial_bytes = b"\xff" * 8 if serial is None else (serial & (2**64 - 1)).to_bytes(8, "big")
    return (
        bytes([FOREFLIGHT, FF_ID, 1])
        + serial_bytes
        + _utf8_field(name, 8)
        + _utf8_field(long_name, 16)
        + caps.to_bytes(4, "big")
    )


def _tenths(deg, limit):
    if deg is None:
        return AHRS_ANGLE_INVALID
    v = int(round(deg * 10.0))
    if not -limit <= v <= limit:
        return AHRS_ANGLE_INVALID
    return v & 0xFFFF


def _speed(kt):
    if kt is None:
        return AHRS_SPEED_INVALID
    return min(max(int(round(kt)), 0), 0xFFFE)


def foreflight_ahrs(roll=None, pitch=None, heading_mag=None, ias=None, tas=None):
    # FF "AHRS Message": roll and pitch in 0.1 deg, +right wing down and
    # +nose up, 0x7FFF invalid, refused outside +-1800.  Heading: bit 15 set
    # for magnetic, bits 14-0 in 0.1 deg, 0xFFFF invalid.  IAS and TAS in
    # knots, 0xFFFF invalid.
    if heading_mag is None:
        hdg = AHRS_HEADING_INVALID
    else:
        hdg = HEADING_MAGNETIC | (int(round((heading_mag % 360.0) * 10.0)) % 3600)
    return bytes([FOREFLIGHT, FF_AHRS]) + struct.pack(
        ">HHHHH",
        _tenths(roll, 1800),
        _tenths(pitch, 1800),
        hdg,
        _speed(ias),
        _speed(tas),
    )
