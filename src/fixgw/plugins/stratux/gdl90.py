# GDL90 framing and message decode.
#
# Citations are to the GDL 90 Data Interface Specification, 560-1058-00 Rev A
# (2007-06-05), written G§ (pyEfis docs/fix_dynamic_list_spec.md section 4.2,
# AER-4579).
#
# A datagram may carry several messages back to back (G§2.2.1: each message
# has its own opening and closing flag, so they meet as 0x7E 0x7E).  Only
# 0x7D is an escape; a bare 0x7E is always a frame boundary.  The decoder this
# replaces treated 0x7E as an escape too and assumed one message per datagram
# (spec F4).

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


CRC16TABLE = build_crc_table()
