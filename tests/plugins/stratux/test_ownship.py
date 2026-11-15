"""GDL90 ownship keys (GDL1, AER-5650): the Ownship Report (0x0A) and the
Ownship Geometric Altitude message (0x0B) into the STX block.

Vectors come from the GDL 90 Data Interface Specification (560-1058-00 Rev A),
cited G§, and from what Stratux v1.6r1-eu023 (the bench FlightBox's build)
encodes, ported from its main/gen_gdl90.go makeOwnshipReport() and
makeOwnshipGeometricAltitudeReport().  Every frame goes through the real
plugin on a UDP socket into the real database.
"""

import json
from pathlib import Path

import pytest

import fixgw.plugins.stratux as stratux
from fixgw.plugins.stratux import gdl90
from fixgw.plugins.stratux.ownship import KEYS

from tests.plugins.stratux.test_traffic import (
    EXAMPLE_FRAMED,
    Gateway,
    latlon_raw,
    ownship,
)

UNCERTAINTY = {"STXRC": 37040.0, "STXEPU": 18520.0, "STXVFOM": 32766.0}

# Recorded from the bench FlightBox, with its own /getSituation beside each
# datagram (data/README.md).
CAPTURE = [
    json.loads(line)
    for line in (Path(__file__).parent / "data" / "gdl90_eu023_bench_ownship.jsonl")
    .read_text()
    .splitlines()
]
CAPTURED_0A = [c for c in CAPTURE if c["ids"] == ["0xa"]]
CAPTURED_0B = [c for c in CAPTURE if c["ids"] == ["0xb"]]


def captured_msg(c):
    (msg,) = gdl90.decode_datagram(bytes.fromhex(c["datagram"]))
    return msg


def raw_fields(msg):
    """The G§3.5.1 raw field values of a 0x0A, as ownship() takes them."""
    b = msg
    return dict(
        s=b[1] >> 4,
        t=b[1] & 0xF,
        addr=int.from_bytes(b[2:5], "big"),
        lat_raw=int.from_bytes(b[5:8], "big"),
        lon_raw=int.from_bytes(b[8:11], "big"),
        ddd=(b[11] << 4) | (b[12] >> 4),
        misc=b[12] & 0xF,
        nic=b[13] >> 4,
        nacp=b[13] & 0xF,
        hhh=(b[14] << 4) | (b[15] >> 4),
        vvv=((b[15] & 0xF) << 8) | b[16],
        tt=b[17],
        ee=b[18],
        cs=b[19:27].decode("ascii").rstrip("\x00 "),
        p=b[27] >> 4,
    )


@pytest.fixture
def gw(database):
    g = Gateway(database)
    yield g
    g.stop()


def geo(alt_raw=0, metrics=0x000A):
    """A clear 0x0B message from its raw G§3.8 fields."""
    return bytes(
        [gdl90.GEO_ALTITUDE, (alt_raw >> 8) & 0xFF, alt_raw & 0xFF]
        + [metrics >> 8, metrics & 0xFF]
    )


def stratux_ownship(lat, lon, alt_ft, gs_kt, track, nacp):
    """What Stratux v1.6r1-eu023 sends from its own GPS (no ADS-B Out seen).

    NIC is always 8, vertical velocity always 0x800, the emitter category
    always 1.  alt_ft is the baro pressure altitude, or GPS MSL when the unit
    has no pressure sensor, or None with no altitude at all.  track None is
    an invalid ground track: m drops "airborne + true track" and the speed
    field is 0, not 0xFFF.
    """
    ddd = 0xFFF if alt_ft is None else int((alt_ft + 1000) / 25) & 0xFFF
    if track is None:
        misc, hhh, tt = 0x0, 0, 0
    else:
        misc, hhh = 0x9, int(gs_kt + 0.5)
        tt = int(((track + 360.0 / 256 / 2) % 360) / (360.0 / 256))
    return ownship(
        t=1,
        addr=0xF00000,
        lat=lat,
        lon=lon,
        ddd=ddd,
        misc=misc,
        nic=8,
        nacp=nacp,
        hhh=hhh,
        vvv=0x800,
        tt=tt,
        ee=1,
        cs="Stratux",
    )


# --- Every 0x0A field ---------------------------------------------------------


def test_icd_worked_example_as_ownship(gw):
    # G§3.5.2's report, sent as the Ownship Report (G§3.4: same layout).
    msg = gdl90.decode_datagram(EXAMPLE_FRAMED)[0]
    gw.send(bytes([gdl90.OWNSHIP]) + msg[1:])
    assert gw.val("STXLAT") == pytest.approx(44.90707, abs=1e-5)
    assert gw.val("STXLONG") == pytest.approx(-122.99486, abs=1e-5)
    assert gw.val("STXPALT") == 5000
    assert gw.val("STXGS") == 123
    assert gw.val("STXTRACK") == 45.0
    assert gw.val("STXVS") == 64
    assert gw.val("STXAIR") is True
    assert gw.val("STXNIC") == 10
    assert gw.val("STXNACP") == 9
    assert gw.val("STXRC") == 25.0  # HPL < 25 m
    assert gw.val("STXEPU") == 30.0  # HFOM < 30 m
    for key in KEYS[:11]:
        assert not gw.flags(key)["bad"], key


def test_stratux_eu023_ownship(gw):
    gw.send(stratux_ownship(36.0986, -115.1739, 2175.0, 97.4, 271.0, 9))
    assert gw.val("STXLAT") == pytest.approx(36.0986, abs=2.2e-5)
    assert gw.val("STXLONG") == pytest.approx(-115.1739, abs=2.2e-5)
    assert gw.val("STXPALT") == 2175
    assert gw.val("STXGS") == 97
    assert gw.val("STXTRACK") == pytest.approx(271.0, abs=360 / 256)
    assert gw.val("STXAIR") is True
    # The constant NIC 8 decodes as sent: < 0.1 NM.
    assert (gw.val("STXNIC"), gw.val("STXRC")) == (8, 185.2)
    assert (gw.val("STXNACP"), gw.val("STXEPU")) == (9, 30.0)
    # Stratux never fills vertical velocity.
    assert gw.flags("STXVS")["bad"]


def test_southern_and_western_hemisphere(gw):
    # G§3.5.1.3 examples: 0xE00000 is 45 S, 0x800000 is -180.
    gw.send(ownship(t=1, lat_raw=0xE00000, lon_raw=0x800000))
    assert (gw.val("STXLAT"), gw.val("STXLONG")) == (-45.0, -180.0)


def test_on_ground_and_descending(gw):
    gw.send(ownship(t=1, misc=0x1, vvv=0xE03))  # G§3.5.1.8: 32,576 descend
    assert gw.val("STXAIR") is False
    assert gw.val("STXVS") == -32576


@pytest.mark.parametrize("code", range(16))
def test_nic_and_nacp_table(gw, code):
    # G§3.5.1.6 Table 10, every code.
    gw.send(ownship(t=1, nic=code, nacp=code))
    rc, epu = gdl90.NIC_RC_M.get(code), gdl90.NACP_EPU_M.get(code)
    if 1 <= code <= 11:
        assert (gw.val("STXRC"), gw.flags("STXRC")["bad"]) == (rc, False)
        assert (gw.val("STXEPU"), gw.flags("STXEPU")["bad"]) == (epu, False)
    else:
        assert rc is None and epu is None
        assert gw.flags("STXRC")["bad"] and gw.flags("STXEPU")["bad"]
    assert gw.flags("STXNIC")["bad"] == gw.flags("STXNACP")["bad"] == (code > 11)


def test_table_values_in_metres():
    nm = 1852.0
    assert gdl90.NIC_RC_M[1] == 20 * nm
    assert gdl90.NIC_RC_M[8] == pytest.approx(0.1 * nm)
    assert gdl90.NACP_EPU_M[1] == 10 * nm
    assert gdl90.NACP_EPU_M[8] == pytest.approx(0.05 * nm)
    # The thresholds Stratux derives NACp from (gps.go calculateNACp).
    assert [gdl90.NACP_EPU_M[c] for c in (11, 10, 9, 8, 7, 6)] == [
        3.0, 10.0, 30.0, 92.6, 185.2, 555.6
    ]


# --- Each invalid encoding is bad, never a number ----------------------------


def assert_bad(gw, key):
    assert gw.flags(key)["bad"], key
    assert gw.val(key) == UNCERTAINTY.get(key, 0), key


INVALID = [
    # (case, message fields, keys that go bad)
    ("no position: lat = lon = NIC = 0", dict(lat_raw=0, lon_raw=0, nic=0),
     ["STXLAT", "STXLONG", "STXRC"]),
    ("altitude 0xFFF", dict(ddd=0xFFF), ["STXPALT"]),
    ("horizontal velocity 0xFFF", dict(hhh=0xFFF), ["STXGS"]),
    ("vertical velocity 0x800", dict(vvv=0x800), ["STXVS"]),
    ("vertical velocity unused 0x1FF", dict(vvv=0x1FF), ["STXVS"]),
    ("vertical velocity unused 0xE01", dict(vvv=0xE01), ["STXVS"]),
    ("tt not valid", dict(misc=0x8), ["STXTRACK"]),
    ("tt is magnetic heading", dict(misc=0xA), ["STXTRACK"]),
    ("tt is true heading", dict(misc=0xB), ["STXTRACK"]),
    ("NIC 0 unknown", dict(nic=0), ["STXRC"]),
    ("NIC 12 unused", dict(nic=12), ["STXNIC", "STXRC"]),
    ("NACp 0 unknown", dict(nacp=0), ["STXEPU"]),
    ("NACp 15 unused", dict(nacp=15), ["STXNACP", "STXEPU"]),
]


@pytest.fixture
def captured_base():
    """The first captured 0x0A as raw fields, with vertical velocity set to
    64 fpm: Stratux never fills it, and the base must be valid everywhere."""
    msg = captured_msg(CAPTURED_0A[0])
    fields = raw_fields(msg)
    rebuilt = ownship(**fields)
    # The field split is exact, but for the call sign's NUL padding, which
    # ownship() pads with spaces instead.
    assert rebuilt[:19] == msg[:19] and rebuilt[27:] == msg[27:]
    return dict(fields, vvv=0x001)


@pytest.mark.parametrize(
    "fields,bad", [c[1:] for c in INVALID], ids=[c[0] for c in INVALID]
)
def test_invalid_encoding_is_bad(gw, captured_base, fields, bad):
    # Each invalid encoding patched into a frame the FlightBox sent.
    gw.send(ownship(**captured_base))
    for key in KEYS[:11]:
        assert not gw.flags(key)["bad"], key
    gw.send(ownship(**dict(captured_base, **fields)))
    for key in KEYS[:11]:
        if key in bad:
            assert_bad(gw, key)
        else:
            assert not gw.flags(key)["bad"], key
    # The next valid report clears it.
    gw.send(ownship(**captured_base))
    for key in bad:
        assert not gw.flags(key)["bad"], key


def test_stratux_eu023_no_fix_fields(gw):
    # No altitude and no ground track, as Stratux encodes them.
    gw.send(stratux_ownship(36.0986, -115.1739, None, 0, None, 0))
    for key in ("STXPALT", "STXTRACK", "STXVS", "STXEPU"):
        assert_bad(gw, key)
    # Stratux sends speed 0, not 0xFFF, with no track: by the ICD that is a
    # valid 0 kt, and it is decoded as one.
    assert (gw.val("STXGS"), gw.flags("STXGS")["bad"]) == (0, False)


# --- 0x0B Ownship Geometric Altitude -----------------------------------------


@pytest.mark.parametrize(
    "raw,feet", [(0xFF38, -1000), (0x0000, 0), (0x00C8, 1000), (0x7FFF, 163835)]
)
def test_geo_altitude_icd_examples(gw, raw, feet):
    gw.send(geo(alt_raw=raw))
    assert gw.val("STXGALT") == feet
    assert not gw.flags("STXGALT")["bad"]


@pytest.mark.parametrize(
    "metrics,vwarn,vfom,bad",
    [
        (0xFFFF, True, 32766.0, True),  # warning, VFOM not available
        (0x7FFE, False, 32766.0, False),  # VFOM >= 32766 m
        (0x000A, False, 10.0, False),  # VFOM 10 m (what Stratux always sends)
        (0x8032, True, 50.0, False),  # warning, VFOM 50 m
        (0x7FFF, False, 32766.0, True),  # VFOM not available
    ],
)
def test_geo_altitude_vertical_metrics(gw, metrics, vwarn, vfom, bad):
    gw.send(geo(alt_raw=0x00C8, metrics=metrics))
    assert gw.val("STXVWARN") is vwarn
    assert gw.val("STXVFOM") == vfom
    assert gw.flags("STXVFOM")["bad"] is bad


def test_stratux_eu023_geo_altitude(gw):
    # int16(GPSalt / 5), metrics 0x000A.
    gw.send(geo(alt_raw=int(2263.0 / 5), metrics=0x000A))
    assert gw.val("STXGALT") == 2260
    assert (gw.val("STXVFOM"), gw.val("STXVWARN")) == (10.0, False)


def test_short_geo_altitude_is_dropped(gw):
    gw.send(geo(alt_raw=0x00C8)[:4])
    assert not gw.db.get_raw_item("STXGALT").written


def test_geometric_altitude_never_writes_alt(database):
    # Baro and GPS altitude are different quantities: neither message may
    # touch the altimeter's keys.  The real dispatch, without the Gateway's
    # sync marker, which is an AHRS message and writes ALT itself.
    plugin = stratux.Plugin("stratux", {"host": "127.0.0.1", "port": 0}, {})
    try:
        for msg in (geo(alt_raw=0x00C8), stratux_ownship(36.1, -115.2, 2175.0, 97, 271, 9)):
            plugin.thread.dispatch(msg)
        assert database.read("STXGALT")[0] == 1000
        assert database.read("STXPALT")[0] == 2175
        # And every frame recorded from the bench FlightBox.
        for c in CAPTURE:
            plugin.thread.dispatch(captured_msg(c))
        assert database.read("STXGALT")[0] != 1000
        for key in ("ALT", "PALT", "TALT"):
            assert not database.get_raw_item(key).written, key
            assert key not in database.writers(), key
    finally:
        plugin.thread.s.close()


# --- Frames recorded from the bench FlightBox --------------------------------


def test_capture_is_what_the_fixture_says():
    assert len(CAPTURED_0A) == 6 and len(CAPTURED_0B) == 6
    for c in CAPTURE:
        assert c["from"] == "10.110.10.169"
        assert abs(c["situation_dt"]) < 1.0
        assert c["situation"]["GPSFixQuality"] >= 1


def test_captured_ownship_exact_decode(gw):
    # The first recorded 0x0A, every field, pinned.
    c = CAPTURED_0A[0]
    assert captured_msg(c)[0] == gdl90.OWNSHIP
    gw.send_raw(bytes.fromhex(c["datagram"]))
    gw.settle()
    r = gdl90.parse_report(captured_msg(c))
    assert (r.atype, r.address, r.callsign) == (1, 0xF00000, "Stratux")
    assert gw.val("STXLAT") == pytest.approx(r.lat) and not gw.flags("STXLAT")["bad"]
    assert gw.val("STXLONG") == pytest.approx(r.lon) and not gw.flags("STXLONG")["bad"]
    assert gw.val("STXPALT") == r.alt
    assert gw.val("STXGS") == r.gs
    assert gw.val("STXTRACK") == pytest.approx(r.trk)
    assert (gw.val("STXNIC"), gw.val("STXRC")) == (8, 185.2)
    assert (gw.val("STXNACP"), gw.val("STXEPU")) == (10, 10.0)
    assert gw.val("STXAIR") is True  # on a shelf: "track valid", not airborne
    # The one invalid encoding a Stratux sends in every report: 0x800.
    assert_bad(gw, "STXVS")


@pytest.mark.parametrize("c", CAPTURED_0A, ids=lambda c: "t=%.0fs" % c["t"])
def test_captured_ownship_against_flightbox_json(gw, c):
    # Every 0x0A field against the FlightBox's own web-UI data at the same
    # moment.  Tolerances are the wire's resolution plus a stationary fix
    # wandering in the time between the datagram and the JSON poll.
    sit = c["situation"]
    gw.send_raw(bytes.fromhex(c["datagram"]))
    gw.settle()
    assert gw.val("STXLAT") == pytest.approx(sit["GPSLatitude"], abs=1e-4)
    assert gw.val("STXLONG") == pytest.approx(sit["GPSLongitude"], abs=1e-4)
    # Pressure altitude, truncated to 25 ft by Stratux, so 0-25 ft low.
    assert -5 <= sit["BaroPressureAltitude"] - gw.val("STXPALT") < 30
    assert gw.val("STXGS") == pytest.approx(sit["GPSGroundSpeed"], abs=1.5)
    assert gw.val("STXTRACK") == pytest.approx(sit["GPSTrueCourse"], abs=360 / 256 + 1)
    assert gw.val("STXNACP") == sit["GPSNACp"]
    assert sit["GPSHorizontalAccuracy"] < gw.val("STXEPU")
    assert gw.val("STXNIC") == 8
    for key in KEYS[:11]:
        assert gw.flags(key)["bad"] is (key == "STXVS"), key


@pytest.mark.parametrize("c", CAPTURED_0B, ids=lambda c: "t=%.0fs" % c["t"])
def test_captured_geo_altitude_against_flightbox_json(gw, c):
    # GDL90MSLAlt_Enabled is on at the bench box, so 0x0B carries MSL, at 5 ft
    # resolution, and not the ellipsoid height (82 ft lower here).
    sit = c["situation"]
    gw.send_raw(bytes.fromhex(c["datagram"]))
    gw.settle()
    assert gw.val("STXGALT") == pytest.approx(sit["GPSAltitudeMSL"], abs=10)
    assert abs(gw.val("STXGALT") - sit["GPSHeightAboveEllipsoid"]) > 50
    assert (gw.val("STXVFOM"), gw.val("STXVWARN")) == (10.0, False)
    assert not gw.flags("STXVFOM")["bad"]


# --- One writer, and an older database ---------------------------------------


def test_stratux_is_the_only_writer_of_every_stx_key(gw):
    gw.send(ownship(t=1), geo(alt_raw=0x00C8))
    writers = gw.db.writers()
    for key in KEYS:
        assert writers.get(key) == ["stratux"], key


def test_ownship_false_writes_no_stx_key(database):
    g = Gateway(database, ownship=False)
    try:
        g.send(ownship(t=1, hhh=88), geo(alt_raw=0x00C8))
        assert g.plugin.thread.ownship is None
        for key in KEYS:
            assert not database.get_raw_item(key).written, key
        assert g.val("GS") == 88  # the existing GS write is unchanged
    finally:
        g.stop()


def test_database_without_stx_block_logs_once_and_keeps_gs(
    database, monkeypatch, caplog
):
    # An edited older database has no STX keys: one error, no exception in
    # the receive loop, and the plugin's other outputs carry on.
    real = database.get_raw_item

    def get_raw_item(key):
        if key.startswith("STX"):
            raise KeyError(key)
        return real(key)

    monkeypatch.setattr(database, "get_raw_item", get_raw_item)
    g = Gateway(database)
    try:
        g.send(ownship(t=1, hhh=88), geo(alt_raw=0x00C8))
        assert g.plugin.thread.ownship is None
        assert g.val("GS") == 88
    finally:
        g.stop()
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert errors == [
        "ownship: true but the database has no 'STXLAT' "
        "(database/stratux.yaml); not writing ownship keys"
    ]


def test_short_ownship_still_writes_gs_and_no_stx(gw):
    # A 22-byte 0x0A (too short for the G§3.5.1 layout) keeps the old GS path
    # and writes no STX key.
    msg = bytearray(22)
    msg[0] = gdl90.OWNSHIP
    msg[14], msg[15] = 0x07, 0x80  # 120 kt
    gw.send(bytes(msg))
    assert gw.val("GS") == 120
    for key in KEYS:
        assert not gw.db.get_raw_item(key).written, key


def test_latlon_helper_matches_icd():
    assert latlon_raw(45.0) == 0x200000 and latlon_raw(-45.0) == 0xE00000
