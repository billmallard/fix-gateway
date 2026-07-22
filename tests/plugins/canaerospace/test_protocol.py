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

# Pure decode tests for the CANaerospace protocol module: hex vectors in,
# expected CanasMessage fields out.  Data-type codes and identifier ranges
# follow the CANaerospace V1.7 tables (cross-verified against the
# CanasStandardDataTypeID / CanasMessageTypeID enums in Pavel Kirienko's
# reference C implementation).

import struct

import pytest

from fixgw.plugins.canaerospace import protocol


def string2data(s):
    s = s.replace(" ", "")
    b = bytearray()
    for x in range(0, len(s), 2):
        b.append(int(s[x : x + 2], 16))  # noqa: E203
    return b


# (arbitration_id, frame hex, expected data_type, expected values)
# Header in every vector: node 0x10 (16), service code 0x00, message code 0x07.
DECODE_VECTORS = [
    (300, "10 00 00 07", protocol.NODATA, ()),
    (300, "10 01 00 07 DEADBEEF", protocol.ERROR, (0xDEADBEEF,)),
    (300, "10 02 00 07 436A8000", protocol.FLOAT, (234.5,)),
    (300, "10 02 00 07 BFC00000", protocol.FLOAT, (-1.5,)),
    (300, "10 03 00 07 80000000", protocol.LONG, (-2147483648,)),
    (300, "10 03 00 07 7FFFFFFF", protocol.LONG, (2147483647,)),
    (300, "10 04 00 07 80000000", protocol.ULONG, (2147483648,)),
    (300, "10 05 00 07 A5A5A5A5", protocol.BLONG, (0xA5A5A5A5,)),
    (300, "10 06 00 07 FFF6", protocol.SHORT, (-10,)),
    (300, "10 06 00 07 8000", protocol.SHORT, (-32768,)),
    (300, "10 07 00 07 FFF6", protocol.USHORT, (65526,)),
    (300, "10 08 00 07 8001", protocol.BSHORT, (0x8001,)),
    (300, "10 09 00 07 80", protocol.CHAR, (-128,)),
    (300, "10 0A 00 07 FF", protocol.UCHAR, (255,)),
    (300, "10 0B 00 07 AA", protocol.BCHAR, (0xAA,)),
    (300, "10 0C 00 07 FFFF0001", protocol.SHORT2, (-1, 1)),
    (300, "10 0D 00 07 FFFF0001", protocol.USHORT2, (65535, 1)),
    (300, "10 0E 00 07 12345678", protocol.BSHORT2, (0x1234, 0x5678)),
    (300, "10 0F 00 07 807FFF00", protocol.CHAR4, (-128, 127, -1, 0)),
    (300, "10 10 00 07 807FFF00", protocol.UCHAR4, (128, 127, 255, 0)),
    (300, "10 11 00 07 01020304", protocol.BCHAR4, (1, 2, 3, 4)),
    (300, "10 12 00 07 FE02", protocol.CHAR2, (-2, 2)),
    (300, "10 13 00 07 FE02", protocol.UCHAR2, (254, 2)),
    (300, "10 14 00 07 ABCD", protocol.BCHAR2, (0xAB, 0xCD)),
    (300, "10 1A 00 07 FF0001", protocol.CHAR3, (-1, 0, 1)),
    (300, "10 1B 00 07 FF0001", protocol.UCHAR3, (255, 0, 1)),
    (300, "10 1C 00 07 010203", protocol.BCHAR3, (1, 2, 3)),
]


@pytest.mark.parametrize("canid,hexstr,dtype,values", DECODE_VECTORS)
def test_decode_vectors(canid, hexstr, dtype, values):
    msg = protocol.parse(canid, string2data(hexstr))
    assert msg.canid == canid
    assert msg.node_id == 0x10
    assert msg.data_type == dtype
    assert msg.service_code == 0x00
    assert msg.message_code == 0x07
    assert msg.channel is protocol.Channel.NOD
    assert len(msg.values) == len(values)
    for got, want in zip(msg.values, values):
        assert got == pytest.approx(want)


@pytest.mark.parametrize("canid,hexstr,dtype,values", DECODE_VECTORS)
def test_build_round_trip(canid, hexstr, dtype, values):
    msg = protocol.parse(canid, string2data(hexstr))
    out_id, out_data = protocol.build(msg)
    assert out_id == canid
    assert out_data == bytes(string2data(hexstr))


def test_float_matches_struct():
    # Independent anchor: the FLOAT wire format is IEEE-754 single,
    # big-endian, exactly struct's '>f'.
    payload = struct.pack(">f", 42.42)
    msg = protocol.parse(300, b"\x10\x02\x00\x07" + payload)
    assert msg.values[0] == pytest.approx(42.42, abs=1e-4)


@pytest.mark.parametrize(
    "canid,channel",
    [
        (0, protocol.Channel.EED),
        (127, protocol.Channel.EED),
        (128, protocol.Channel.NSH),
        (199, protocol.Channel.NSH),
        (200, protocol.Channel.UDH),
        (299, protocol.Channel.UDH),
        (300, protocol.Channel.NOD),
        (1799, protocol.Channel.NOD),
        (1800, protocol.Channel.UDL),
        (1899, protocol.Channel.UDL),
        (1900, protocol.Channel.DSD),
        (1999, protocol.Channel.DSD),
        (2000, protocol.Channel.NSL),
        (2031, protocol.Channel.NSL),
    ],
)
def test_channel_distribution(canid, channel):
    assert protocol.channel_for(canid) is channel


def test_channel_out_of_range():
    with pytest.raises(protocol.CanasDecodeError):
        protocol.channel_for(2032)


@pytest.mark.parametrize(
    "canid,hexstr",
    [
        (300, "10 02 00"),  # DLC < 4
        (300, "10 02 00 07 43 6A 80 00 00"),  # DLC > 8
        (2032, "10 02 00 07 436A8000"),  # canid past NSL
        (300, "10 02 00 07 436A"),  # FLOAT payload too short
        (300, "10 06 00 07 FFF60000"),  # SHORT payload too long
        (300, "10 00 00 07 01"),  # NODATA with payload
    ],
)
def test_decode_errors(canid, hexstr):
    with pytest.raises(protocol.CanasDecodeError):
        protocol.parse(canid, string2data(hexstr))


@pytest.mark.parametrize(
    "dtype",
    [
        protocol.MEMID,
        protocol.CHKSUM,
        protocol.ACHAR,
        protocol.ACHAR2,
        protocol.ACHAR4,
        protocol.ACHAR3,
        protocol.DOUBLEH,
        protocol.DOUBLEL,
        100,  # user-defined
        255,
        32,  # reserved
    ],
)
def test_unsupported_types(dtype):
    frame = bytes((0x10, dtype, 0x00, 0x07)) + b"\x00\x00\x00\x00"
    with pytest.raises(protocol.CanasUnsupportedType):
        protocol.parse(300, frame)
    # Unsupported is a subclass of the general decode error so callers may
    # catch either.
    assert issubclass(protocol.CanasUnsupportedType, protocol.CanasDecodeError)


def test_build_rejects_unsupported_type():
    msg = protocol.CanasMessage(
        canid=300,
        node_id=1,
        data_type=protocol.MEMID,
        service_code=0,
        message_code=0,
        values=(0,),
        channel=protocol.Channel.NOD,
    )
    with pytest.raises(protocol.CanasUnsupportedType):
        protocol.build(msg)


def test_build_rejects_unencodable_values():
    msg = protocol.CanasMessage(
        canid=300,
        node_id=1,
        data_type=protocol.USHORT,
        service_code=0,
        message_code=0,
        values=(70000,),  # does not fit uint16
        channel=protocol.Channel.NOD,
    )
    with pytest.raises(protocol.CanasDecodeError):
        protocol.build(msg)


def test_type_names():
    assert protocol.type_name(protocol.FLOAT) == "FLOAT"
    assert protocol.type_name(protocol.MEMID) == "MEMID"
    assert protocol.type_name(150) == "UDEF150"
    assert protocol.type_name(40) == "RESERVED40"
