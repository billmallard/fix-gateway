"""The shipped CHT and EGT low alarms on a cold start (AER-6137, fix-gateway #200).

A cold engine reads below its low CHT and EGT alarms, so before this change
every start annunciated all eight keys until the engine warmed up.  Bill's
ruling (2026-11-20): keep the low alarms, bypass them with the engine stopped
on the ground as the oil items are, and hold them for a warm-up period from
engine start, TACH1.warmup, default 30 s.

Engine start is TACH1 reaching 500 rpm with IAS below IAS.Vgnd.  These tests
run the real plugin on the shipped annunciate.yaml, database and default.ini,
with the plugin's clock stepped by hand.
"""

import time

import pytest

from fixgw import cfg
from fixgw.plugins import annunciate

ANNUNCIATE_YAML = "src/fixgw/config/connections/annunciate.yaml"
DEFAULT_INI = "src/fixgw/config/init_data/default.ini"

# default.ini: CHTec.lowAlarm 80.3, highAlarm 232.2; EGTec 380 and 650 degC
COLD = {"CHT": 20.0, "EGT": 20.0}
WARM = {"CHT": 150.0, "EGT": 500.0}
HOT = {"CHT": 240.0, "EGT": 660.0}


def _value(table, key):
    return table[key[:3]]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    # raising=False lets this file run against a plugin without the clock,
    # to show which tests fail there
    monkeypatch.setattr(annunciate, "_now", c, raising=False)
    return c


def _start(database, ini=True):
    if ini:
        database.load_init_file(DEFAULT_INI)  # the server's loader
    with open(ANNUNCIATE_YAML) as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = annunciate.Plugin("annunciate", conf["annunciate"], meta)
    pl.run()
    return pl


@pytest.fixture
def gw(database, clock):
    pl = _start(database)
    yield database
    pl.stop()


def _keys(db):
    # Every cylinder the database declares (CHTec, EGTec; c: 6 shipped)
    return [f"{k}1{c}" for k in ("CHT", "EGT") for c in range(1, db.variables["c"] + 1)]


def _write(db, table):
    keys = _keys(db)
    for key in keys:
        db.write(key, _value(table, key))
    return {key: db.get_raw_item(key).annunciate for key in keys}


def _all(flag):
    from fixgw import database

    return {key: flag for key in _keys(database)}


def _cold_start(db, clock):
    """Engine stopped on the ground, cold; then the engine starts."""
    db.write("IAS", 0.0)
    db.write("TACH1", 0.0)
    _write(db, COLD)
    db.write("TACH1", 1000.0)  # engine start at clock.t


def test_shipped_warmup_is_30_s(gw):
    assert gw.read("TACH1.warmup") == 30.0


def test_every_cylinder_is_covered(gw):
    assert len(_keys(gw)) == 2 * gw.variables["c"] == 12


def test_engine_stopped_on_ground_cold_sets_no_flag(gw):
    # The definition of done's test: CHT11 below its low alarm with
    # TACH1 = 0 and IAS = 0
    gw.write("TACH1", 0.0)
    gw.write("IAS", 0.0)
    assert _write(gw, COLD) == _all(False)


def test_cold_10_s_after_start_sets_no_flag(gw, clock):
    _cold_start(gw, clock)
    clock.t += 10.0
    assert _write(gw, COLD) == _all(False)


def test_cold_40_s_after_start_sets_the_flag(gw, clock):
    _cold_start(gw, clock)
    clock.t += 10.0
    _write(gw, COLD)
    clock.t += 30.0
    assert _write(gw, COLD) == _all(True)


def test_warmup_ends_at_30_s(gw, clock):
    _cold_start(gw, clock)
    clock.t += 29.9
    assert _write(gw, COLD) == _all(False)
    clock.t += 0.2
    assert _write(gw, COLD) == _all(True)


def test_a_high_alarm_does_not_wait_for_warmup(gw, clock):
    _cold_start(gw, clock)
    clock.t += 5.0
    assert _write(gw, HOT) == _all(True)


def test_warm_after_warmup_sets_no_flag(gw, clock):
    _cold_start(gw, clock)
    clock.t += 40.0
    assert _write(gw, WARM) == _all(False)


def test_configured_warmup_is_read_at_each_evaluation(gw, clock):
    gw.write("TACH1.warmup", 60.0)
    _cold_start(gw, clock)
    clock.t += 40.0
    assert _write(gw, COLD) == _all(False)
    clock.t += 30.0
    assert _write(gw, COLD) == _all(True)


def test_without_a_configured_value_the_warmup_is_30_s(database, clock):
    # An install whose init files never set TACH1.warmup: the 30 s default
    # still holds (Bill, 2026-11-20)
    database.load_init_file(DEFAULT_INI)
    database.write("TACH1.warmup", "None")
    pl = _start(database, ini=False)
    try:
        assert database.read("TACH1.warmup") is None
        _cold_start(database, clock)
        clock.t += 25.0
        assert _write(database, COLD) == _all(False)
        clock.t += 10.0
        assert _write(database, COLD) == _all(True)
    finally:
        pl.stop()


def test_engine_started_before_the_gateway_still_warms_up(gw, clock):
    # Avionics master on after start: the first TACH1 reading is running
    gw.write("IAS", 0.0)
    gw.write("TACH1", 1100.0)
    clock.t += 10.0
    assert _write(gw, COLD) == _all(False)
    clock.t += 30.0
    assert _write(gw, COLD) == _all(True)


def test_gateway_restarted_in_flight_starts_no_warmup(gw, clock):
    gw.write("IAS", 120.0)
    gw.write("TACH1", 2400.0)
    clock.t += 1.0
    assert _write(gw, COLD) == _all(True)


def test_tach_recovering_in_flight_starts_no_warmup(gw, clock):
    gw.write("IAS", 120.0)
    gw.write("TACH1", 2400.0)
    gw.get_raw_item("TACH1").fail = True
    gw.write("TACH1", 0.0)
    gw.get_raw_item("TACH1").fail = False
    gw.write("TACH1", 2400.0)
    clock.t += 1.0
    assert _write(gw, COLD) == _all(True)


def test_tach_reading_zero_in_flight_leaves_alarms_armed(gw, clock):
    gw.write("IAS", 120.0)
    gw.write("TACH1", 0.0)
    assert _write(gw, COLD) == _all(True)


def test_start_with_airspeed_untrusted_starts_no_warmup(gw, clock):
    # No trusted on-ground reading at start: the alarm stays armed
    gw.write("IAS", 0.0)
    gw.write("TACH1", 0.0)
    gw.get_raw_item("IAS").timestamp = time.time() - 3.0  # timed out
    gw.write("TACH1", 1000.0)
    clock.t += 10.0
    assert _write(gw, COLD) == _all(True)


def test_tach_failed_at_start_starts_no_warmup(gw, clock):
    gw.write("IAS", 0.0)
    gw.write("TACH1", 0.0)
    gw.get_raw_item("TACH1").fail = True
    gw.write("TACH1", 1000.0)
    clock.t += 10.0
    assert _write(gw, COLD) == _all(True)


def test_a_restart_on_the_ground_runs_a_new_warmup(gw, clock):
    _cold_start(gw, clock)
    clock.t += 60.0
    gw.write("TACH1", 0.0)  # shut down
    _write(gw, COLD)
    clock.t += 60.0
    gw.write("TACH1", 1000.0)  # restart
    clock.t += 10.0
    assert _write(gw, COLD) == _all(False)
    clock.t += 30.0
    assert _write(gw, COLD) == _all(True)


def test_engine_running_on_the_ground_restarts_no_warmup(gw, clock):
    # Taxi after the warm-up: rpm moving above 500 is not a new start
    _cold_start(gw, clock)
    clock.t += 40.0
    gw.write("TACH1", 1800.0)
    gw.write("TACH1", 900.0)
    assert _write(gw, COLD) == _all(True)
