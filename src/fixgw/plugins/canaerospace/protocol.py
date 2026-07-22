#  Copyright (c) 2026 MakerPlane contributors
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
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

# Pure CANaerospace V1.7 frame decoding.  No fixgw imports, stdlib only,
# so this module can be unit-tested against hex vectors and reused by the
# canas_sim tool.
#
# Authoritative source: CANaerospace Interface Specification V1.7
# (canas_17.pdf, Stock Flight Systems).  Both tables below (data types and
# identifier distribution) were cross-verified line-by-line against the
# CanasStandardDataTypeID and CanasMessageTypeID enums in Pavel Kirienko's
# reference C implementation (canaerospace/message.h), which transcribes
# the same tables from the PDF.

import struct
from dataclasses import dataclass
from enum import Enum


class CanasDecodeError(ValueError):
    """The frame is not a decodable CANaerospace message."""


class CanasUnsupportedType(CanasDecodeError):
    """The frame's Data Type is legal CANaerospace but not implemented."""


# Highest standard 11-bit CANaerospace identifier (Node Service Low ends
# at 2031; 2032-2047 are outside the V1.7 distribution).
MAX_CANID = 2031


class Channel(Enum):
    EED = "Emergency Event Data"
    NSH = "Node Service High"
    UDH = "User-Defined High"
    NOD = "Normal Operation Data"
    UDL = "User-Defined Low"
    DSD = "Debug Service Data"
    NSL = "Node Service Low"


# CANaerospace V1.7 standard identifier distribution (11-bit).
# (canid_low, canid_high, channel)
CHANNEL_RANGES = (
    (0, 127, Channel.EED),
    (128, 199, Channel.NSH),
    (200, 299, Channel.UDH),
    (300, 1799, Channel.NOD),
    (1800, 1899, Channel.UDL),
    (1900, 1999, Channel.DSD),
    (2000, 2031, Channel.NSL),
)


def channel_for(canid):
    for low, high, channel in CHANNEL_RANGES:
        if low <= canid <= high:
            return channel
    raise CanasDecodeError(
        "CAN id {} outside the CANaerospace 11-bit distribution (0-{})".format(
            canid, MAX_CANID
        )
    )


# CANaerospace V1.7 standard data types (message byte 1).  Codes are
# sequential from 0 in the order the specification lists them.
NODATA = 0
ERROR = 1
FLOAT = 2
LONG = 3
ULONG = 4
BLONG = 5
SHORT = 6
USHORT = 7
BSHORT = 8
CHAR = 9
UCHAR = 10
BCHAR = 11
SHORT2 = 12
USHORT2 = 13
BSHORT2 = 14
CHAR4 = 15
UCHAR4 = 16
BCHAR4 = 17
CHAR2 = 18
UCHAR2 = 19
BCHAR2 = 20
MEMID = 21
CHKSUM = 22
ACHAR = 23
ACHAR2 = 24
ACHAR4 = 25
CHAR3 = 26
UCHAR3 = 27
BCHAR3 = 28
ACHAR3 = 29
DOUBLEH = 30
DOUBLEL = 31

# Implemented (decodable) types: code -> (name, big-endian struct format).
# All CANaerospace data is big-endian.  Bitfield types (B*) are delivered
# as unsigned ints; multi-element types unpack to multiple values addressed
# by the mapfile's 'element'.
_DECODERS = {
    NODATA: ("NODATA", ""),
    ERROR: ("ERROR", ">I"),
    FLOAT: ("FLOAT", ">f"),
    LONG: ("LONG", ">i"),
    ULONG: ("ULONG", ">I"),
    BLONG: ("BLONG", ">I"),
    SHORT: ("SHORT", ">h"),
    USHORT: ("USHORT", ">H"),
    BSHORT: ("BSHORT", ">H"),
    CHAR: ("CHAR", ">b"),
    UCHAR: ("UCHAR", ">B"),
    BCHAR: ("BCHAR", ">B"),
    SHORT2: ("SHORT2", ">hh"),
    USHORT2: ("USHORT2", ">HH"),
    BSHORT2: ("BSHORT2", ">HH"),
    CHAR4: ("CHAR4", ">bbbb"),
    UCHAR4: ("UCHAR4", ">BBBB"),
    BCHAR4: ("BCHAR4", ">BBBB"),
    CHAR2: ("CHAR2", ">bb"),
    UCHAR2: ("UCHAR2", ">BB"),
    BCHAR2: ("BCHAR2", ">BB"),
    CHAR3: ("CHAR3", ">bbb"),
    UCHAR3: ("UCHAR3", ">BBB"),
    BCHAR3: ("BCHAR3", ">BBB"),
}

# Legal-but-unimplemented codes, for error messages and status readability.
_UNSUPPORTED_NAMES = {
    MEMID: "MEMID",
    CHKSUM: "CHKSUM",
    ACHAR: "ACHAR",
    ACHAR2: "ACHAR2",
    ACHAR4: "ACHAR4",
    ACHAR3: "ACHAR3",
    DOUBLEH: "DOUBLEH",
    DOUBLEL: "DOUBLEL",
}

# Mapfile 'type:' vocabulary: name -> code, implemented types only.
TYPE_CODES = {name: code for code, (name, _fmt) in _DECODERS.items()}


def type_name(code):
    if code in _DECODERS:
        return _DECODERS[code][0]
    if code in _UNSUPPORTED_NAMES:
        return _UNSUPPORTED_NAMES[code]
    if 100 <= code <= 255:
        return "UDEF{}".format(code)
    return "RESERVED{}".format(code)


@dataclass(frozen=True)
class CanasMessage:
    canid: int
    node_id: int
    data_type: int
    service_code: int
    message_code: int
    values: tuple
    channel: Channel


def parse(arbitration_id, data):
    """Decode one CANaerospace frame into a CanasMessage.

    Raises CanasUnsupportedType for legal-but-unimplemented data types and
    CanasDecodeError for anything that is not a well-formed message.
    """
    if arbitration_id < 0 or arbitration_id > MAX_CANID:
        raise CanasDecodeError(
            "CAN id {} outside CANaerospace range 0-{}".format(
                arbitration_id, MAX_CANID
            )
        )
    data = bytes(data)
    # Every CANaerospace message carries the 4-byte header (Node-ID,
    # Data Type, Service Code, Message Code) plus 0-4 data bytes.
    if len(data) < 4 or len(data) > 8:
        raise CanasDecodeError(
            "DLC {} invalid; CANaerospace messages are 4-8 bytes".format(len(data))
        )
    node_id = data[0]
    data_type = data[1]
    service_code = data[2]
    message_code = data[3]
    payload = data[4:]

    try:
        name, fmt = _DECODERS[data_type]
    except KeyError:
        raise CanasUnsupportedType(
            "unsupported data type {} ({})".format(data_type, type_name(data_type))
        )
    expected = struct.calcsize(fmt) if fmt else 0
    if len(payload) != expected:
        raise CanasDecodeError(
            "{} payload is {} bytes, expected {}".format(name, len(payload), expected)
        )
    values = struct.unpack(fmt, payload) if fmt else ()
    return CanasMessage(
        canid=arbitration_id,
        node_id=node_id,
        data_type=data_type,
        service_code=service_code,
        message_code=message_code,
        values=values,
        channel=channel_for(arbitration_id),
    )


def build(msg):
    """Inverse of parse(): return (arbitration_id, data bytes).

    Used by the canas_sim tool and the round-trip decode tests.
    """
    if msg.canid < 0 or msg.canid > MAX_CANID:
        raise CanasDecodeError(
            "CAN id {} outside CANaerospace range 0-{}".format(msg.canid, MAX_CANID)
        )
    try:
        name, fmt = _DECODERS[msg.data_type]
    except KeyError:
        raise CanasUnsupportedType(
            "unsupported data type {} ({})".format(
                msg.data_type, type_name(msg.data_type)
            )
        )
    header = bytes(
        (msg.node_id & 0xFF, msg.data_type, msg.service_code, msg.message_code & 0xFF)
    )
    try:
        payload = struct.pack(fmt, *msg.values) if fmt else b""
    except struct.error as e:
        raise CanasDecodeError(
            "cannot encode {} values {}: {}".format(name, msg.values, e)
        )
    return msg.canid, header + payload
