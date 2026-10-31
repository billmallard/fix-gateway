"""NULLWP1: the active-waypoint position keys are flagged on whether there IS
an active waypoint.

With no plan the engine publishes WPLAT/WPLON (and the FROM anchor,
FPLFRLAT/FPLFRLON) as 0.0. Unflagged, the shipped compute chain turned that
into a valid bearing to 0N 0E (bearing -> GPSBRGT -> wrap360 -> GPSBRG ->
select -> BRG1), and the HSI drew pointer 1 aimed at Null Island. The flag
depends on the waypoint, not on ownship position: every case here has a good
GPS position.
"""

import time

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine
from fixgw import cfg

from .test_plugin import make_config, write_route

COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"

KSBA = (34.42621, -119.84037)
KSMX = (34.89892, -120.45758)
KVBG = (34.73730, -120.58430)

WP_KEYS = ("WPLAT", "WPLON")
FR_KEYS = ("FPLFRLAT", "FPLFRLON")


def _good_position(database):
    database.write("GPS_FIX_TYPE", 3)
    database.write("LAT", KSBA[0])
    database.write("LONG", KSBA[1])
    database.write("GS", 120.0)
    database.write("MAGVAR", -14.0)


def _route():
    return [engine.Waypoint("KSBA", *KSBA), engine.Waypoint("KSMX", *KSMX)]


def _fail(database, key):
    return database.get_raw_item(key).fail


def test_no_plan_publishes_waypoint_positions_failed(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    _good_position(database)

    pl.thread._run_update_cycle()

    assert database.read("FPLSTATE")[0] == engine.STATE_NONE
    assert not _fail(database, "LAT") and not _fail(database, "LONG")   # ownship is fine
    for key in WP_KEYS + FR_KEYS:
        assert database.read(key)[0] == 0.0
        assert _fail(database, key), key


def test_active_leg_publishes_waypoint_positions_valid(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    _good_position(database)
    write_route(database, _route(), seq=1)
    database.write("FPLCMD", "1 ACT 2")

    pl.thread._run_update_cycle()

    assert database.read("FPLSTATE")[0] == engine.STATE_LEG
    assert database.read("WPLAT")[0] == pytest.approx(KSMX[0])
    assert database.read("WPLON")[0] == pytest.approx(KSMX[1])
    assert database.read("FPLFRLAT")[0] == pytest.approx(KSBA[0])
    for key in WP_KEYS + FR_KEYS:
        assert not _fail(database, key), key


def test_direct_to_publishes_waypoint_positions_valid(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    _good_position(database)
    write_route(database, _route(), seq=1)
    database.write("FPLCMD", "1 DTO 2")

    pl.thread._run_update_cycle()

    assert database.read("FPLSTATE")[0] == engine.STATE_DIRECT
    assert database.read("WPLAT")[0] == pytest.approx(KSMX[0])
    for key in WP_KEYS + FR_KEYS:
        assert not _fail(database, key), key


def test_replacing_the_plan_fails_them_again(database, tmp_path):
    # A new route resets activation: there is no active waypoint until the
    # pilot activates a leg of it.
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    _good_position(database)
    write_route(database, _route(), seq=1)
    database.write("FPLCMD", "1 ACT 2")
    pl.thread._run_update_cycle()
    assert not _fail(database, "WPLAT")

    write_route(database, [engine.Waypoint("KSBA", *KSBA), engine.Waypoint("KVBG", *KVBG)], seq=2)
    pl.thread._run_update_cycle()

    assert database.read("FPLSTATE")[0] == engine.STATE_NONE
    for key in WP_KEYS + FR_KEYS:
        assert _fail(database, key), key


def test_position_failure_still_fails_them(database, tmp_path):
    # Unchanged behaviour, now flagged: on a position failure the engine
    # blanks the waypoint fields, and a blanked 0.0 is not a waypoint either.
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    _good_position(database)
    write_route(database, _route(), seq=1)
    database.write("FPLCMD", "1 ACT 2")
    pl.thread._run_update_cycle()
    assert not _fail(database, "WPLAT")

    database.write("GPS_FIX_TYPE", 1)   # no fix
    pl.thread._run_update_cycle()

    for key in WP_KEYS + FR_KEYS:
        assert database.read(key)[0] == 0.0
        assert _fail(database, key), key


@pytest.fixture
def compute(database):
    import fixgw.plugins.compute

    with open(COMPUTE_YAML) as f:
        cc, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = fixgw.plugins.compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    time.sleep(0.1)
    yield database
    pl.shutdown()


def test_bearing_pointer_fails_through_the_shipped_compute_chain(compute, tmp_path):
    # The Beelink case end to end: KSBA position, MAGVAR -14, pointer 1 on GPS.
    database = compute
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    database.write("BRG1SRC", 2.0)
    _good_position(database)

    # Active leg first, so BRG1 is proved valid before it is proved failed.
    write_route(database, _route(), seq=1)
    database.write("FPLCMD", "1 ACT 2")
    pl.thread._run_update_cycle()
    expected = geo.wrap360(geo.bearing_deg(KSBA[0], KSBA[1], KSMX[0], KSMX[1]) - 14.0)
    assert database.read("BRG1")[0] == pytest.approx(expected, abs=0.2)
    for key in ("GPSBRGT", "GPSBRG", "BRG1"):
        assert not _fail(database, key), key

    # Plan replaced, nothing activated: no waypoint, so no pointer.
    write_route(database, _route(), seq=2)
    pl.thread._run_update_cycle()
    assert database.read("FPLSTATE")[0] == engine.STATE_NONE
    for key in ("GPSBRGT", "GPSBRG", "BRG1"):
        assert _fail(database, key), key
    assert not _fail(database, "LAT")
