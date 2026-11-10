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

# AvionicsDuino layout decode.  Every vector below is written as literal
# bytes, little-endian IEEE-754 / two's complement, the way the senders'
# raw memory copies put them on the wire, so a bug shared by encode() and
# decode() cannot pass.

import struct

import pytest

from fixgw.plugins.avionicsduino import protocol

# IEEE-754 single, little-endian
F_0_5 = bytes.fromhex("0000003f")  # 0x3F000000
F_1_5 = bytes.fromhex("0000c03f")  # 0x3FC00000
F_2_0 = bytes.fromhex("00000040")  # 0x40000000
F_M170 = bytes.fromhex("00002ac3")  # 0xC32A0000 = -170.0
F_NAN = bytes.fromhex("0000c07f")  # 0x7FC00000
# IEEE-754 double, little-endian
D_45 = bytes.fromhex("0000000000804640")  # 0x4046800000000000
D_M93_25 = bytes.fromhex("00000000005057c0")  # 0xC057500000000000

# The message dictionary in briefs/avionicsduino_compatibility.md.
BRIEF_IDS = {10, 20, 22, 24, 28, 30, 34, 36, 37, 38, 42, 43, 44, 45, 46, 48}
BRIEF_IDS |= {50, 52, 54, 56, 58, 60}


def test_every_brief_id_has_a_layout():
    assert BRIEF_IDS <= set(protocol.LAYOUTS)


@pytest.mark.parametrize("canid", sorted(protocol.LAYOUTS))
def test_layout_is_consistent(canid):
    layout = protocol.LAYOUTS[canid]
    assert layout.fmt.startswith("<")
    n = len(struct.unpack(layout.fmt, bytes(struct.calcsize(layout.fmt))))
    assert n == len(layout.fields)
    assert struct.calcsize(layout.fmt) <= 8
    assert layout.node in ("ahrs", "remote", "rdam", "ems")


def test_id10_counter_uint64():
    assert protocol.decode(10, bytes.fromhex("0201000000000000")) == {"count": 258}
    big = protocol.decode(10, bytes.fromhex("ffffffffffffff7f"))["count"]
    assert big == 2**63 - 1


def test_id20_roll_radians_pitch_degrees():
    v = protocol.decode(20, F_0_5 + F_2_0)
    assert v == {"roll_rad": 0.5, "pitch_deg": 2.0}


def test_id22_acc():
    v = protocol.decode(22, F_1_5 + F_M170)
    assert v == {"acc_y": 1.5, "acc_z": -170.0}


def test_id24_vz_track():
    v = protocol.decode(24, F_0_5 + F_M170)
    assert v == {"vz_mps": 0.5, "track_deg": -170.0}


def test_id28_heading_is_a_four_byte_frame():
    # The remote module sends 28, 42, 44 and 45 with DLC 4.
    assert protocol.decode(28, F_2_0) == {"heading_deg": 2.0}


def test_id30_gnss_alt_gs():
    v = protocol.decode(30, F_2_0 + F_1_5)
    assert v == {"alt_msl_ft": 2.0, "gs_kmh": 1.5}


def test_id34_date_time_sats():
    # uint16 2026 = 0x07EA, then month, day, hour, minute, second, sats
    v = protocol.decode(34, bytes.fromhex("ea070b0a0f1e2d0e"))
    assert v == {
        "year": 2026,
        "month": 11,
        "day": 10,
        "hour": 15,
        "minute": 30,
        "second": 45,
        "sats": 14,
    }


def test_id36_37_doubles():
    assert protocol.decode(36, D_45) == {"lat_deg": 45.0}
    assert protocol.decode(37, D_M93_25) == {"lon_deg": -93.25}


def test_id38_signed_nanoseconds_unsigned_accuracy():
    v = protocol.decode(38, bytes.fromhex("ffffffff19000000"))
    assert v == {"nanosec": -1, "time_acc_ns": 25}


def test_remote_module_ids():
    assert protocol.decode(42, F_1_5) == {"oat_c": 1.5}
    assert protocol.decode(43, F_0_5 + F_M170) == {"magx_ut": 0.5, "magy_ut": -170.0}
    assert protocol.decode(44, F_2_0) == {"rh_pct": 2.0}
    assert protocol.decode(45, F_M170) == {"magz_ut": -170.0}


def test_id46_int16s():
    # 2992, 4500, -120, -350
    v = protocol.decode(46, bytes.fromhex("b00b941188ffa2fe"))
    assert v == {
        "qnh_cinhg": 2992,
        "qnh_alt_ft": 4500,
        "density_alt_ft": -120,
        "baro_vs_fpm": -350,
    }


def test_id48_int16s():
    # 170, 182, 20, 270
    v = protocol.decode(48, bytes.fromhex("aa00b60014000e01"))
    assert v == {"ias_kmh": 170, "tas_kmh": 182, "wind_kmh": 20, "wind_dir_deg": 270}


def test_rdam_engine_ids():
    assert protocol.decode(50, F_2_0) == {"map": 2.0}
    assert protocol.decode(52, F_0_5 + F_1_5) == {"cht2": 0.5, "cht3": 1.5}
    assert protocol.decode(54, F_0_5 + F_1_5) == {"oil_temp": 0.5, "oil_press": 1.5}
    assert protocol.decode(56, F_0_5 + F_1_5) == {"egt3": 0.5, "egt4": 1.5}
    assert protocol.decode(60, F_0_5 + F_M170) == {
        "bus_current_a": 0.5,
        "battery_current_a": -170.0,
    }


def test_id58_eight_byte_frame_reads_bytes_0_to_3():
    # The RDAM sends DLC 8 and never fills bytes 4-7.
    assert protocol.decode(58, F_1_5 + bytes.fromhex("deadbeef")) == {"afr": 1.5}


def test_ems_ids_as_the_rdam_reads_them():
    # 26: uint8, uint8, uint16 rpm, float vBat
    v = protocol.decode(26, bytes.fromhex("0102501400006041"))
    assert v == {"lower_limit": 1, "upper_limit": 2, "rpm": 5200, "vbat": 14.0}
    # 33: uint16 seconds, then a float at offset 2 (no padding)
    v = protocol.decode(33, bytes.fromhex("1027") + F_1_5)
    assert v == {"total_seconds": 10000, "fuel_used": 1.5}


def test_nan_decodes_as_nan():
    v = protocol.decode(20, F_NAN + F_2_0)
    assert v["roll_rad"] != v["roll_rad"]


def test_short_payload_is_a_decode_error():
    with pytest.raises(protocol.DecodeError):
        protocol.decode(20, F_0_5)
    with pytest.raises(protocol.DecodeError):
        protocol.decode(36, bytes(7))


def test_unknown_id_is_a_key_error():
    with pytest.raises(KeyError):
        protocol.decode(11, bytes(8))


@pytest.mark.parametrize("canid", sorted(protocol.LAYOUTS))
def test_encode_matches_the_wire_size(canid):
    layout = protocol.LAYOUTS[canid]
    data = protocol.encode(canid, {f: 0 for f in layout.fields})
    assert len(data) == (8 if canid == 58 else struct.calcsize(layout.fmt))
