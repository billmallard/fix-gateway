"""GDL-90 encoders of the gdl90_out plugin (AER-6142, BACKLOG EFB1).

Two kinds of oracle, so the encoder and the decoder cannot be wrong together:
the stratux plugin's decoder (plugins/stratux/gdl90.py), which every message
round-trips through, and byte values worked out in the specifications
themselves -- the GDL 90 Data Interface Specification 560-1058-00 Rev A
(cited G§) and ForeFlight's GDL 90 Extended Specification (cited FF).
"""

import struct

import pytest

from fixgw.plugins.gdl90_out import messages as m
from fixgw.plugins.stratux import gdl90

LSB = 180.0 / (1 << 23)


def roundtrip(msg):
    (back,) = gdl90.decode_datagram(gdl90.encode(msg))
    assert back == msg
    return back


# --- G§2.2.4: the one complete frame the ICD gives --------------------------

ICD_HEARTBEAT_FRAME = bytes(
    [0x7E, 0x00, 0x81, 0x41, 0xDB, 0xD0, 0x08, 0x02, 0xB3, 0x8B, 0x7E]
)


def test_icd_heartbeat_frame_is_what_the_shared_framer_builds():
    clear = ICD_HEARTBEAT_FRAME[1:-3]
    assert gdl90.encode(clear) == ICD_HEARTBEAT_FRAME


def test_icd_heartbeat_decodes():
    (msg,) = gdl90.decode_datagram(ICD_HEARTBEAT_FRAME)
    hb = gdl90.parse_heartbeat(msg)
    assert hb.gps_valid and hb.initialized and hb.utc_ok
    assert not hb.maintenance
    assert hb.timestamp == 0xD0DB


def test_heartbeat_matches_the_icd_example_in_the_bits_we_set():
    # The example also has CSA Requested (status 2 bit 6) and message counts,
    # which an output-only box never sets.
    msg = m.heartbeat(True, 0xD0DB, utc_ok=True)
    assert msg == bytes([0x00, 0x81, 0x01, 0xDB, 0xD0, 0x00, 0x00])


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.parametrize("ts", [0, 1, 0xD0DB, 0xFFFF, 0x10000, 86399])
def test_heartbeat_round_trips(valid, ts):
    hb = gdl90.parse_heartbeat(roundtrip(m.heartbeat(valid, ts)))
    assert hb.gps_valid is valid
    assert hb.initialized
    assert hb.utc_ok is False  # host clock, not a GPS time reference
    assert hb.timestamp == ts


def test_heartbeat_time_stamp_bit_16_is_status_2_bit_7():
    assert m.heartbeat(False, 0x10000)[2] & 0x80
    assert not m.heartbeat(False, 0xFFFF)[2] & 0x80


# --- G§3.5.2, Table 12: the worked Traffic Report ----------------------------

TABLE_12 = bytes.fromhex(
    "14 00 AB 45 49 1F EF 15 A8 89 78 0F 09 A9 07 B0 01 20 01"
    " 4E 38 32 35 56 20 20 20 00"
)


def test_table_12_traffic_report_byte_for_byte():
    msg = m.report(
        gdl90.TRAFFIC,
        lat=44.90708,
        lon=-122.99488,
        nic=10,
        nacp=9,
        alt=5000,
        gs=123,
        vs=64,
        track=45,
        track_type=m.TT_TRUE_TRACK,
        airborne=True,
        alert=0,
        address_type=0,
        address=0o52642511,
        emitter=1,
        callsign="N825V",
    )
    assert msg == TABLE_12


def test_table_12_round_trips_through_the_decoder():
    r = gdl90.parse_report(roundtrip(TABLE_12))
    assert r.lat == pytest.approx(44.90708, abs=LSB)
    assert r.lon == pytest.approx(-122.99488, abs=LSB)
    assert (r.alt, r.gs, r.vs, r.trk) == (5000, 123, 64, 45.0)
    assert (r.nic, r.nacp, r.cat, r.callsign) == (10, 9, 1, "N825V")
    assert r.airborne and not r.pos_bad


# --- field scaling against the ICD's own examples ----------------------------


@pytest.mark.parametrize(
    "deg, raw",
    [
        # G§3.5.1.3 examples
        (0.0, 0x000000),
        (LSB, 0x000001),
        (-LSB, 0xFFFFFF),
        (45.0, 0x200000),
        (-45.0, 0xE00000),
        (90.0, 0x400000),
        (-180.0, 0x800000),
        (180.0 - LSB, 0x7FFFFF),
    ],
)
def test_lat_long_are_180_over_2_23_deg(deg, raw):
    assert m.semicircle(deg) == raw
    msg = m.report(gdl90.OWNSHIP, lat=deg / 2, lon=deg, nic=8, nacp=9)
    assert int.from_bytes(msg[8:11], "big") == raw
    assert gdl90.parse_report(roundtrip(msg)).lon == pytest.approx(deg, abs=1e-9)


@pytest.mark.parametrize(
    "ft, ddd",
    # G§3.5.1.4 examples; the ends hold rather than wrap into "invalid"
    [(-1000, 0x000), (0, 0x028), (1000, 0x050), (101350, 0xFFE),
     (-5000, 0x000), (200000, 0xFFE), (None, 0xFFF)],
)
def test_altitude_is_25_ft_offset_minus_1000(ft, ddd):
    msg = m.report(gdl90.OWNSHIP, alt=ft)
    assert (msg[11] << 4) | (msg[12] >> 4) == ddd
    r = gdl90.parse_report(roundtrip(msg))
    assert r.alt_bad is (ft is None)


@pytest.mark.parametrize("deg, tt", [(0, 0), (45, 32), (180, 128), (359.9, 0),
                                      (360 / 256, 1), (270, 192)])
def test_track_is_360_over_256(deg, tt):
    msg = m.report(gdl90.OWNSHIP, track=deg)
    assert msg[17] == tt
    assert msg[12] & 0x03 == m.TT_TRUE_TRACK
    r = gdl90.parse_report(roundtrip(msg))
    assert r.trk == pytest.approx(tt * 360 / 256)
    assert not r.trk_bad


def test_no_track_is_type_not_valid():
    r = gdl90.parse_report(roundtrip(m.report(gdl90.OWNSHIP, track=None)))
    assert r.trk_bad and r.ttype == 0


@pytest.mark.parametrize(
    "fpm, vvv",
    # G§3.5.1.8 examples
    [(0, 0x000), (64, 0x001), (-64, 0xFFF), (32576, 0x1FD), (40000, 0x1FE),
     (-32576, 0xE03), (-40000, 0xE02), (None, 0x800)],
)
def test_vertical_velocity_is_64_fpm(fpm, vvv):
    msg = m.report(gdl90.OWNSHIP, vs=fpm)
    assert ((msg[15] & 0x0F) << 8) | msg[16] == vvv
    r = gdl90.parse_report(roundtrip(msg))
    assert r.vs_bad is (fpm is None)


@pytest.mark.parametrize("kt, hhh", [(0, 0), (123, 123), (123.6, 124),
                                      (5000, 0xFFE), (None, 0xFFF)])
def test_ground_speed_is_1_kt(kt, hhh):
    msg = m.report(gdl90.OWNSHIP, gs=kt)
    assert (msg[14] << 4) | (msg[15] >> 4) == hhh
    assert gdl90.parse_report(roundtrip(msg)).gs_bad is (kt is None)


def test_no_position_is_lat_lon_nic_nacp_zero():
    msg = m.report(gdl90.OWNSHIP, lat=None, lon=None, nic=8, nacp=9)
    assert msg[5:11] == bytes(6) and msg[13] == 0
    assert gdl90.parse_report(roundtrip(msg)).pos_bad


@pytest.mark.parametrize(
    "epu_m, code",
    # Table 10, strict "<" at each bound
    [(2.9, 11), (3.0, 10), (9.9, 10), (10.0, 9), (29.9, 9), (30.0, 8),
     (92.6, 7), (18519.0, 1), (18520.0, 0), (None, 0), (-1.0, 0),
     (float("nan"), 0)],
)
def test_nacp_bins(epu_m, code):
    assert m.nacp_code(epu_m) == code


def test_callsign_is_upper_alnum_space_padded():
    msg = m.report(gdl90.OWNSHIP, callsign="n-825v;x\x00longername")
    assert msg[19:27] == b"N825VXLO"
    assert m.report(gdl90.OWNSHIP)[19:27] == b" " * 8


# --- G§3.8 Ownship Geometric Altitude -----------------------------------------


@pytest.mark.parametrize("ft, raw", [(-1000, 0xFF38), (0, 0x0000), (1000, 0x00C8)])
def test_geo_altitude_icd_examples(ft, raw):
    msg = m.geo_altitude(ft, 10)
    assert msg[1:3] == raw.to_bytes(2, "big")
    g = gdl90.parse_geo_altitude(roundtrip(msg))
    assert g.alt == ft


@pytest.mark.parametrize(
    "vfom, metrics",
    # G§3.8 vertical metrics examples (no warning)
    [(10, 0x000A), (40000, 0x7FFE), (None, 0x7FFF)],
)
def test_geo_altitude_vertical_metrics(vfom, metrics):
    msg = m.geo_altitude(0, vfom)
    assert msg[3:5] == metrics.to_bytes(2, "big")
    g = gdl90.parse_geo_altitude(roundtrip(msg))
    assert g.vfom_bad is (vfom is None)
    assert not g.vwarn


# --- FF ID and AHRS messages ---------------------------------------------------


def test_foreflight_id_layout():
    msg = m.foreflight_id("MkrPlane", "MkrPlane 2.3.0", msl_datum=True, internet_policy=2)
    assert len(msg) == 39  # FF table: bytes 1-39
    assert msg[:3] == bytes([0x65, 0x00, 0x01])
    assert msg[3:11] == b"\xff" * 8  # serial invalid
    assert msg[11:19] == b"MkrPlane"
    assert msg[19:35] == b"MkrPlane 2.3.0  "
    assert msg[35:39] == bytes([0, 0, 0, 0b101])  # MSL datum, policy 2
    assert roundtrip(msg) == msg


def test_foreflight_id_ellipsoid_datum_bit_clear():
    msg = m.foreflight_id("MkrPlane", "x")
    assert msg[35:39] == bytes(4)


def test_foreflight_ahrs_layout():
    msg = m.foreflight_ahrs(roll=-12.3, pitch=4.5, heading_mag=271.4, ias=98, tas=105.4)
    assert len(msg) == 12  # FF table: bytes 1-12
    assert msg[:2] == bytes([0x65, 0x01])
    roll, pitch, hdg, ias, tas = struct.unpack(">hhHHH", msg[2:])
    assert (roll, pitch) == (-123, 45)
    assert hdg == 0x8000 | 2714  # magnetic, 0.1 deg
    assert (ias, tas) == (98, 105)
    assert roundtrip(msg) == msg


def test_foreflight_ahrs_invalid_values():
    msg = m.foreflight_ahrs()
    assert msg[2:] == bytes.fromhex("7FFF 7FFF FFFF FFFF FFFF")


def test_foreflight_ahrs_out_of_range_angle_is_invalid():
    # FF rejects the message outright outside +-1800
    assert m.foreflight_ahrs(roll=180.04)[2:4] == bytes.fromhex("0708")
    assert m.foreflight_ahrs(roll=180.06)[2:4] == bytes.fromhex("7FFF")


def test_heading_360_wraps_to_0():
    msg = m.foreflight_ahrs(heading_mag=359.97)
    assert struct.unpack(">H", msg[6:8])[0] == 0x8000
