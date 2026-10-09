#  Copyright (c) 2026 Bill Mallard
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
#  USA.

"""Sim Innovations Knobster USB wire protocol (no hardware, no pyusb).

Recovered 2026-10-07 by intercepting the vendor library's WinUSB traffic; see
maos-workspace makerplane/captures/knobster/README.md for the capture and the
reference reader (knobster_open.py) this module is copied from.

USB packet = [count][count stream bytes][stale padding], 16 bytes each way.
Stream     = frames ``ff LEN payload[LEN]``; a frame may straddle two packets.
"""

VID = 0x16D0
PID = 0x0E8A
INTERFACE = 0
EP_OUT = 0x04
EP_IN = 0x83
PACKET_SIZE = 16

# Frame payloads (the leading ff and the LEN byte are added by frames_to_packets)
HELLO = b"\x01"
START = b"\x16"


def _hex(*frames):
    return [bytes.fromhex(h) for h in frames]


# One-time startup frames, in the vendor library's order, sent after START.
# The last one (04 01 ...) configures encoder 01, the outer ring. It is NOT in
# the repeating cycle, and without it the outer ring reports nothing.
INIT = _hex(
    "03 01 04 01 02",
    "03 00 04 01 02",
    "03 02 04 01 02",
    "03 03 04 01 02",
    "03 04 05 01 32",
    "04 00 01 02 01 00 02",
    "03 00 04 01 02",
    "04 01 01 02 02 03 02",
)

# Repeating cycle; with hello + START only the device sends zero events.
CONFIG = _hex(
    "03 01 04 01 02",
    "03 00 04 01 02",
    "03 02 04 01 02",
    "03 03 04 01 02",
    "03 04 05 01 32",
    "04 00 01 02 01 00 02",
    "04 00 01 02 01 00 02",
)
CONFIG_INTERVAL = 0.9  # seconds, matches the vendor library's cycle

# Device -> host
INFO_TYPE = 0x02  # reply to HELLO: 02 07 00 07 05 05 00 02 03

OP_CW = 0x0B
OP_CCW = 0x0C
OP_PRESS = 0x0D
OP_RELEASE = 0x0E

ID_INNER = 0x00  # "minor" ring
ID_OUTER = 0x01  # "major" ring
ID_BUTTON = 0x04


def frames_to_packets(payloads):
    """Wrap each payload as ``ff LEN payload`` and split the stream into
    16-byte OUT packets of up to 15 stream bytes, zero padded."""
    stream = b"".join(b"\xff" + bytes([len(p)]) + p for p in payloads)
    packets = []
    for i in range(0, len(stream), PACKET_SIZE - 1):
        chunk = stream[i : i + PACKET_SIZE - 1]
        packets.append(bytes([len(chunk)]) + chunk.ljust(PACKET_SIZE - 1, b"\0"))
    return packets


class Deframer:
    """Reassembles frame payloads from IN packets, discarding stale padding."""

    def __init__(self):
        self.buf = b""

    def reset(self):
        self.buf = b""

    def feed(self, pkt):
        pkt = bytes(pkt)
        if not pkt:
            return []
        self.buf += pkt[1 : 1 + pkt[0]]
        out = []
        while True:
            i = self.buf.find(b"\xff")
            if i < 0:
                self.buf = b""
                break
            self.buf = self.buf[i:]
            if len(self.buf) < 2 or len(self.buf) < 2 + self.buf[1]:
                break
            n = self.buf[1]
            out.append(self.buf[2 : 2 + n])
            self.buf = self.buf[2 + n :]
        return out


def is_info(payload):
    return len(payload) > 2 and payload[0] == INFO_TYPE


def parse_event(payload):
    """Return ``(op, id)`` for a two-byte event frame, else None."""
    if len(payload) == 2 and payload[0] in (OP_CW, OP_CCW, OP_PRESS, OP_RELEASE):
        return payload[0], payload[1]
    return None
