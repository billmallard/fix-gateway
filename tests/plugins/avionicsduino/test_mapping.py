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

# The shipped AvionicsDuino mapfile and the mapping loader: every mapped
# conversion, the ID 46 altitude gate, the BARO refusal, airspeed
# smoothing and staleness.  Conversions run through the SHIPPED mapfile,
# from literal wire bytes, so a test fails if a mapfile row is wrong.

import math
import time

import pytest

import fixgw.plugins.avionicsduino.mapping as ad_mapping
from fixgw.plugins.avionicsduino import protocol

SHIPPED_MAP = "src/fixgw/config/avionicsduino/ahrs21_rm3_rdam12.yaml"


class FakeLog:
    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, message):
        self.debugs.append(message)

    def warning(self, message):
        self.warnings.append(message)


def shipped(options=None, smoothing_tau=0.0):
    return ad_mapping.Mapping(SHIPPED_MAP, FakeLog(), options, smoothing_tau)


def make_mapping(tmp_path, text, options=None, smoothing_tau=0.0, log=None):
    p = tmp_path / "map.yaml"
    p.write_text(text)
    return ad_mapping.Mapping(str(p), log or FakeLog(), options, smoothing_tau)


def feed(m, canid, data, now=None):
    m.inputMap(canid, protocol.decode(canid, data), time.monotonic() if now is None else now)


def f32(*values):
    return b"".join(protocol.struct.pack("<f", v) for v in values)


def i16(*values):
    return b"".join(protocol.struct.pack("<h", v) for v in values)


def value(database, key):
    return database.read(key)[0]


# --- the shipped mapfile ------------------------------------------------


def test_shipped_mapfile_loads(database):
    m = shipped()
    assert m.write_count == 0
    # ID 46's two rows are off by default
    assert m.disabled_count == 2
    assert 46 not in m.interesting


def test_shipped_rows_map_existing_keys_only(database):
    # Mint no keys: every row, live or gated, names a key the default
    # database already has.
    m = shipped({"rdam_altitude": True, "rdam_vertical_speed": True})
    keys = {e["fixid"] for entries in m.input_map.values() for e in entries}
    assert keys <= set(database.listkeys())
    assert "BARO" not in keys


def test_shipped_mapfile_records_source_shas():
    with open(SHIPPED_MAP) as f:
        header = f.read().split("ignore_fixid_missing")[0]
    for repo in (
        "bfrmd/EFIS-AHRS-AvionicsDuino",
        "bfrmd/EFIS-Remote-Module-AvionicsDuino",
        "bfrmd/ESP32-EFIS-EMS-Avionicsduino",
    ):
        assert repo in header
    for name in (
        "AHRS_AvionicsDuino_V2.1.ino",
        "EFIS_Remote_Module_AvionicsDuino_V3.ino",
        "RDAMcanBus.cpp",
        "RDAMsensors.cpp",
        "RDAMglobals.h",
    ):
        line = [ln for ln in header.splitlines() if name in ln][0]
        assert len(line.split()[-1]) == 40, line


def test_every_live_key_ages_within_twice_its_source_period(database):
    # Staleness rides on each key's database tol, like canaerospace.  A key
    # with tol 0 would never go old; one with tol under two frame periods
    # would flicker old on a healthy bus.
    m = shipped({"rdam_altitude": True, "rdam_vertical_speed": True})
    for canid, entries in m.input_map.items():
        period_ms = 1000.0 / protocol.LAYOUTS[canid].rate_hz
        for e in entries:
            tol = e["item"].tol
            assert tol > 0, e["fixid"]
            assert tol >= 2 * period_ms, (e["fixid"], tol, period_ms)


def test_roll_radians_to_degrees_pitch_as_is(database):
    m = shipped()
    feed(m, 20, f32(math.radians(-25.0), 3.5))
    assert value(database, "ROLL") == pytest.approx(-25.0, abs=1e-4)
    assert value(database, "PITCH") == pytest.approx(3.5)


def test_accelerations_to_g_with_anorm_plus_one_in_level_flight(database):
    m = shipped()
    # Level flight: accZ (body z, down) reads -g.  A right-positive
    # specific force of 0.1 g on y.
    feed(m, 22, f32(0.980665, -9.80665))
    assert value(database, "ALAT") == pytest.approx(0.1, abs=1e-6)
    assert value(database, "ANORM") == pytest.approx(1.0, abs=1e-6)
    # A 2 g pull-up
    feed(m, 22, f32(0.0, -2 * 9.80665))
    assert value(database, "ANORM") == pytest.approx(2.0, abs=1e-6)


@pytest.mark.parametrize(
    "wire, expected", [(-170.0, 190.0), (-0.5, 359.5), (0.0, 0.0), (179.5, 179.5)]
)
def test_track_plus_minus_180_to_0_360(database, wire, expected):
    m = shipped()
    feed(m, 24, f32(0.0, wire))
    assert value(database, "TRACK") == pytest.approx(expected, abs=1e-4)


def test_ground_speed_kmh_to_knots(database):
    m = shipped()
    feed(m, 30, f32(4500.0, 185.2))
    assert value(database, "GS") == pytest.approx(100.0, abs=1e-3)


def test_utc_time_and_satellites(database):
    m = shipped()
    feed(m, 34, bytes.fromhex("ea070b0a0f1e2d0e"))
    assert value(database, "TIMEZH") == 15
    assert value(database, "TIMEZM") == 30
    assert value(database, "TIMEZS") == 45
    assert value(database, "GPS_SATS_VISIBLE") == 14


def test_latitude_longitude_doubles(database):
    m = shipped()
    feed(m, 36, protocol.struct.pack("<d", -23.5505199))
    feed(m, 37, protocol.struct.pack("<d", -46.6333094))
    assert value(database, "LAT") == pytest.approx(-23.5505199, abs=1e-9)
    assert value(database, "LONG") == pytest.approx(-46.6333094, abs=1e-9)


def test_heading_as_is_and_wrapped(database):
    m = shipped()
    feed(m, 28, f32(271.25))
    assert value(database, "HEAD") == pytest.approx(271.25, abs=1e-4)
    # Their RDAM comment says -180..+180; wrap360 handles either.
    feed(m, 28, f32(-90.0))
    assert value(database, "HEAD") == pytest.approx(270.0, abs=1e-4)


def test_oat_degc(database):
    m = shipped()
    feed(m, 42, f32(-12.5))
    assert value(database, "OAT") == pytest.approx(-12.5)


def test_raw_magnetometer_microtesla_to_gauss(database):
    m = shipped()
    feed(m, 43, f32(21.5, -4.0))
    feed(m, 45, f32(44.0))
    assert value(database, "MAGX") == pytest.approx(0.215, abs=1e-6)
    assert value(database, "MAGY") == pytest.approx(-0.04, abs=1e-6)
    assert value(database, "MAGZ") == pytest.approx(0.44, abs=1e-6)


def test_ias_tas_kmh_to_knots_unsmoothed_by_default(database):
    m = shipped()
    feed(m, 48, i16(185, 200, 20, 270))
    assert value(database, "IAS") == pytest.approx(185 / 1.852, abs=1e-4)
    assert value(database, "TAS") == pytest.approx(200 / 1.852, abs=1e-4)
    # The very next step lands whole: no smoothing unless asked for.
    feed(m, 48, i16(186, 201, 20, 270))
    assert value(database, "IAS") == pytest.approx(186 / 1.852, abs=1e-4)


def test_wind_is_not_mapped(database):
    m = shipped()
    before = (value(database, "WINDSPD"), value(database, "WINDDIR"))
    feed(m, 48, i16(185, 200, 37, 123))
    assert (value(database, "WINDSPD"), value(database, "WINDDIR")) == before


def test_bus_current_amps(database):
    m = shipped()
    feed(m, 60, f32(12.25, -3.5))
    assert value(database, "CURRNT") == pytest.approx(12.25)


def test_unmapped_ids_write_nothing(database):
    m = shipped()
    for canid in (10, 26, 32, 33, 38, 44, 50, 52, 54, 56, 58):
        assert canid not in m.interesting


# --- ID 46: altitude and vertical speed off by default, BARO never ---------


def test_id46_writes_nothing_by_default(database):
    m = shipped()
    alt, vs, baro = value(database, "ALT"), value(database, "VS"), value(database, "BARO")
    feed(m, 46, i16(3012, 4500, 5200, -500))
    assert value(database, "ALT") == alt
    assert value(database, "VS") == vs
    assert value(database, "BARO") == baro


def test_id46_altitude_option(database):
    m = shipped({"rdam_altitude": True})
    vs = value(database, "VS")
    feed(m, 46, i16(3012, 4500, 5200, -500))
    assert value(database, "ALT") == pytest.approx(4500)
    assert value(database, "VS") == vs


def test_id46_vertical_speed_option(database):
    m = shipped({"rdam_vertical_speed": True})
    alt = value(database, "ALT")
    feed(m, 46, i16(3012, 4500, 5200, -500))
    assert value(database, "VS") == pytest.approx(-500)
    assert value(database, "ALT") == alt


def test_id46_never_writes_baro_even_with_both_options(database):
    m = shipped({"rdam_altitude": True, "rdam_vertical_speed": True})
    baro = value(database, "BARO")
    feed(m, 46, i16(3012, 4500, 5200, -500))
    assert value(database, "BARO") == baro
    assert value(database, "BARO") != pytest.approx(30.12)


def test_a_baro_row_is_refused_at_load(database, tmp_path):
    with pytest.raises(ValueError, match="never writes BARO"):
        make_mapping(
            tmp_path,
            "inputs:\n  - { canid: 46, field: qnh_cinhg, fixid: BARO, scale: 0.01 }\n",
        )


def test_unknown_option_is_refused(database):
    with pytest.raises(ValueError):
        shipped({"rdam_baro": True})


# --- smoothing ----------------------------------------------------------


def test_smoothing_is_a_first_order_low_pass(database):
    tau = 0.5
    m = shipped(smoothing_tau=tau)
    t0 = 1000.0
    feed(m, 48, i16(180, 190, 0, 0), now=t0)
    assert value(database, "IAS") == pytest.approx(180 / 1.852, abs=1e-6)
    # A 10 km/h step, sampled 0.2 s later (5 Hz)
    feed(m, 48, i16(190, 200, 0, 0), now=t0 + 0.2)
    alpha = 1 - math.exp(-0.2 / tau)
    assert value(database, "IAS") == pytest.approx((180 + 10 * alpha) / 1.852, abs=1e-6)
    assert value(database, "TAS") == pytest.approx((190 + 10 * alpha) / 1.852, abs=1e-6)
    # Holding the new value converges on it
    for k in range(2, 40):
        feed(m, 48, i16(190, 200, 0, 0), now=t0 + 0.2 * k)
    assert value(database, "IAS") == pytest.approx(190 / 1.852, abs=1e-3)


def test_smoothing_takes_out_the_whole_kmh_steps(database):
    # 5 Hz whole-km/h samples of a slowly rising airspeed: the raw tape
    # moves in 0.54 kt jumps, the smoothed one in under half that.
    def steps(m):
        out, last = [], None
        for k in range(50):
            feed(m, 48, i16(int(170 + 0.3 * k), 180, 0, 0), now=500.0 + 0.2 * k)
            v = value(database, "IAS")
            if last is not None:
                out.append(abs(v - last))
            last = v
        return max(out)

    raw = steps(shipped())
    assert raw == pytest.approx(1 / 1.852, abs=1e-6)
    assert steps(shipped(smoothing_tau=1.0)) < 0.5 * raw


def test_smoothing_restarts_after_a_gap(database):
    m = shipped(smoothing_tau=0.5)
    feed(m, 48, i16(100, 110, 0, 0), now=10.0)
    feed(m, 48, i16(200, 210, 0, 0), now=10.0 + ad_mapping.SMOOTH_RESET_S + 0.1)
    assert value(database, "IAS") == pytest.approx(200 / 1.852, abs=1e-6)


def test_smoothing_applies_only_to_smooth_rows(database):
    m = shipped(smoothing_tau=0.5)
    feed(m, 30, f32(0.0, 100.0), now=1.0)
    feed(m, 30, f32(0.0, 200.0), now=1.2)
    assert value(database, "GS") == pytest.approx(200 / 1.852, abs=1e-6)


def test_smooth_with_wrap360_is_refused(database, tmp_path):
    with pytest.raises(ValueError, match="wrap360"):
        make_mapping(
            tmp_path,
            "inputs:\n"
            "  - { canid: 28, field: heading_deg, fixid: HEAD, converter: wrap360,"
            " smooth: true }\n",
        )


def test_negative_smoothing_tau_is_refused(database):
    with pytest.raises(ValueError):
        shipped(smoothing_tau=-1.0)


# --- quality flags ------------------------------------------------------


def test_a_quiet_node_goes_old_within_its_tol_and_recovers(database):
    m = shipped()
    feed(m, 20, f32(0.1, 1.0))
    roll = database.get_raw_item("ROLL")
    database.update()
    assert not roll.old
    assert not database.read("ROLL")[2]
    time.sleep((roll.tol + 100) / 1000.0)
    database.update()
    assert roll.old
    assert database.read("ROLL")[2]  # (value, annunciate, old, bad, fail, ...)
    # The next frame clears it
    feed(m, 20, f32(0.1, 1.0))
    assert not roll.old
    assert not database.read("ROLL")[2]


def test_a_quiet_node_takes_only_its_own_keys_old(database):
    m = shipped()
    roll = database.get_raw_item("ROLL")
    head = database.get_raw_item("HEAD")
    feed(m, 20, f32(0.1, 1.0))
    feed(m, 28, f32(90.0))
    deadline = time.time() + (roll.tol + 100) / 1000.0
    # The remote module keeps sending at 10 Hz; the AHRS has gone quiet.
    while time.time() < deadline:
        feed(m, 28, f32(90.0))
        time.sleep(0.1)
    database.update()
    assert roll.old
    assert not head.old


def test_non_finite_values_are_not_written(database):
    m = shipped()
    feed(m, 20, f32(0.2, 4.0))
    feed(m, 20, bytes.fromhex("0000c07f") + f32(float("inf")))
    assert value(database, "ROLL") == pytest.approx(math.degrees(0.2), abs=1e-4)
    assert value(database, "PITCH") == pytest.approx(4.0)
    assert m.nonfinite_count == 2


def test_a_good_value_clears_fail(database):
    m = shipped()
    roll = database.get_raw_item("ROLL")
    roll.fail = True
    feed(m, 20, f32(0.1, 1.0))
    assert not roll.fail


# --- loader validation --------------------------------------------------


@pytest.mark.parametrize(
    "row, message",
    [
        ("{ canid: 11, field: x, fixid: ROLL }", "no AvionicsDuino layout"),
        ("{ canid: 20, field: roll, fixid: ROLL }", "has no field"),
        ("{ canid: 20, field: roll_rad, fixid: NOSUCHKEY }", "not a valid fixid"),
        ("{ canid: 20, field: roll_rad, fixid: ROLL, converter: x }", "unknown"),
        ("{ canid: 20, field: roll_rad, fixid: ROLL, requires: x }", "requires"),
        ("{ canid: 20, field: roll_rad, fixid: ROLL, scale: a }", "number"),
        ("{ canid: 20, field: roll_rad, fixid: ROLL, smooth: 1 }", "true or false"),
        ("{ canid: 20, field: roll_rad, fixid: ROLL, nodes: [1] }", "Unknown key"),
        ("{ canid: 20, fixid: ROLL }", "missing"),
    ],
)
def test_bad_rows_are_refused(database, tmp_path, row, message):
    with pytest.raises(ValueError, match=message):
        make_mapping(tmp_path, "inputs:\n  - " + row + "\n")


def test_duplicate_row_is_refused(database, tmp_path):
    row = "  - { canid: 20, field: roll_rad, fixid: ROLL }\n"
    with pytest.raises(ValueError, match="duplicate"):
        make_mapping(tmp_path, "inputs:\n" + row + row)


def test_missing_fixid_can_be_ignored(database, tmp_path):
    m = make_mapping(
        tmp_path,
        "ignore_fixid_missing: true\n"
        "inputs:\n  - { canid: 20, field: roll_rad, fixid: NOSUCHKEY }\n",
    )
    assert m.interesting == set()


def test_missing_mapfile_raises(database):
    with pytest.raises(ValueError):
        ad_mapping.Mapping("no_such_map.yaml")
