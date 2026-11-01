# GDL90 framing and message decode.
#
# Citations are to the GDL 90 Data Interface Specification, 560-1058-00 Rev A
# (2007-06-05), written G§; the FIX side of the contract is pyEfis
# docs/fix_dynamic_list_spec.md section 4 (AER-4579).
#
# A datagram may carry several messages back to back (G§2.2.1: each message
# has its own opening and closing flag, so they meet as 0x7E 0x7E).  Only
# 0x7D is an escape; a bare 0x7E is always a frame boundary.  The decoder this
# replaces treated 0x7E as an escape too and assumed one message per datagram
# (spec F4).

from collections import namedtuple

FLAG = 0x7E
ESC = 0x7D

HEARTBEAT = 0x00
OWNSHIP = 0x0A
TRAFFIC = 0x14


def build_crc_table():
    crc16table = []
    for i in range(256):
        crc = i << 8
        for bitctr in range(8):
            crc = (crc << 1) ^ (0x1021 if (crc & 0x8000) else 0)
        crc16table.append(crc & 0xFFFF)
    return crc16table


# CRC-CCITT, polynomial 0x1021, initial value 0, table driven as in G§2.2.3.
def calc_crc(msg: bytes):
    crc = 0
    for i in range(len(msg)):
        crc = CRC16TABLE[crc >> 8] ^ (crc << 8) ^ msg[i]
        crc = crc & 0xFFFF
    return crc


def stuff(data: bytes):
    out = bytearray()
    for b in data:
        if b in (FLAG, ESC):
            out += bytes([ESC, b ^ 0x20])
        else:
            out.append(b)
    return bytes(out)


# Frame a clear message (ID + data): the FCS is computed before stuffing and
# appended LSB first (G§2.2.3), then the FCS is stuffed with the rest.
def encode(msg: bytes):
    crc = calc_crc(msg)
    return bytes([FLAG]) + stuff(msg + bytes([crc & 0xFF, crc >> 8])) + bytes([FLAG])


# The clear message of one frame's contents (between its flags), or None if it
# does not survive reception: a dangling escape, too short for an ID and FCS,
# an FCS mismatch (G§2.2.1, reception step 4), or an ID byte with the MSB set
# (G§2.2.2).
def decode_frame(raw: bytes):
    clear = bytearray()
    esc = False
    for b in raw:
        if esc:
            clear.append(b ^ 0x20)
            esc = False
        elif b == ESC:
            esc = True
        else:
            clear.append(b)
    if esc or len(clear) < 3:
        return None
    msg = bytes(clear[:-2])
    if calc_crc(msg) != clear[-2] | (clear[-1] << 8):
        return None
    if msg[0] & 0x80:
        return None
    return msg


# Every valid message in a datagram, in order.  Bytes before the first flag or
# after the last belong to no complete frame and are discarded.
def decode_datagram(datagram: bytes):
    parts = bytes(datagram).split(bytes([FLAG]))
    out = []
    for raw in parts[1:-1]:
        if raw:
            msg = decode_frame(raw)
            if msg is not None:
                out.append(msg)
    return out


# A Traffic Report (0x14) or Ownship Report (0x0A), which share a layout
# (G§3.4, G§3.5.1).  Values are decoded to FIX units; the *_bad fields carry
# the GDL90 special values as flags (spec section 4.5, D2).
Report = namedtuple(
    "Report",
    [
        "alert",  # s
        "atype",  # t
        "address",  # aaaaaa
        "lat",
        "lon",
        "pos_bad",  # lat = lon = NIC = 0
        "alt",
        "alt_bad",  # ddd = 0xFFF
        "ttype",  # m bits 1-0
        "extrapolated",  # m bit 2
        "airborne",  # m bit 3
        "nic",
        "nacp",
        "gs",
        "gs_bad",  # hhh = 0xFFF
        "vs",
        "vs_bad",  # vvv = 0x800, or an unused code
        "trk",
        "trk_bad",  # m bits 1-0 = 00
        "cat",
        "callsign",
        "emergency",
    ],
)

REPORT_LEN = 28


def _signed(value, bits):
    if value & (1 << (bits - 1)):
        value -= 1 << bits
    return value


# Decode a 28-byte 0x0A/0x14 message (ID included).  None if it is short.
def parse_report(msg: bytes):
    if len(msg) < REPORT_LEN:
        return None
    b = msg
    alert = b[1] >> 4
    atype = b[1] & 0x0F
    address = (b[2] << 16) | (b[3] << 8) | b[4]
    lat_raw = _signed((b[5] << 16) | (b[6] << 8) | b[7], 24)
    lon_raw = _signed((b[8] << 16) | (b[9] << 8) | b[10], 24)
    ddd = (b[11] << 4) | (b[12] >> 4)
    misc = b[12] & 0x0F
    nic = b[13] >> 4
    nacp = b[13] & 0x0F
    hhh = (b[14] << 4) | (b[15] >> 4)
    vvv = ((b[15] & 0x0F) << 8) | b[16]
    tt = b[17]
    cat = b[18]
    callsign = b[19:27].decode("latin-1").rstrip(" ")
    emergency = b[27] >> 4

    # G§3.5.1.8: 0x800 is "no data"; 0x1FF-0x7FF and 0x801-0xE01 are unused
    # codes, which would otherwise decode beyond +-32,640 fpm and be clamped
    # into a plausible value (spec section 2.10).
    vs_bad = 0x1FF <= vvv <= 0xE01
    ttype = misc & 0x03
    return Report(
        alert=alert,
        atype=atype,
        address=address,
        lat=lat_raw * 180.0 / (1 << 23),
        lon=lon_raw * 180.0 / (1 << 23),
        pos_bad=lat_raw == 0 and lon_raw == 0 and nic == 0,
        alt=ddd * 25 - 1000,
        alt_bad=ddd == 0xFFF,
        ttype=ttype,
        extrapolated=bool(misc & 0x04),
        airborne=bool(misc & 0x08),
        nic=nic,
        nacp=nacp,
        gs=hhh,
        gs_bad=hhh == 0xFFF,
        vs=0 if vs_bad else _signed(vvv, 12) * 64,
        vs_bad=vs_bad,
        trk=tt * 360.0 / 256,
        trk_bad=ttype == 0,
        cat=cat,
        callsign=_netfix_safe(callsign),
        emergency=emergency,
    )


# GDL90 allows 0-9, A-Z, space and (before SW Mod D) '-' in the call sign
# (G§3.5.1.11).  Anything that is not printable ASCII, or is the netfix field
# separator, would corrupt the KEY;VALUE;FLAGS line protocol, so it becomes
# '?'.  A conforming source never sends one.
def _netfix_safe(s):
    return "".join(c if " " <= c <= "~" and c != ";" else "?" for c in s)


CRC16TABLE = build_crc_table()
