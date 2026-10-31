"""The shipped annunciate.yaml against the shipped database (AER-4370).

A tach that fails and reads 0 in flight must not silence the oil-pressure,
oil-temperature and volts alarms on a running engine.  These tests run the
real plugin on the real config, so a later edit to annunciate.yaml that puts
a single-input bypass back is caught here.
"""

import time

import pytest

from fixgw import cfg

ANNUNCIATE_YAML = "src/fixgw/config/connections/annunciate.yaml"

LIMITS = {
    "OILP1": {"lowAlarm": 25.0},
    "OILT1": {"highAlarm": 118.0},
    "VOLT": {"lowAlarm": 11.0},
}
# A value past each item's alarm limit
ALARM = {"OILP1": 15.0, "OILT1": 125.0, "VOLT": 10.5}
NORMAL = {"OILP1": 60.0, "OILT1": 90.0, "VOLT": 14.0}


def _items():
    with open(ANNUNCIATE_YAML) as f:
        return cfg.from_yaml(f.read())["annunciate"]["items"]


@pytest.fixture
def plugin(database):
    from fixgw.plugins import annunciate

    for key, aux in LIMITS.items():
        for name, value in aux.items():
            database.write(f"{key}.{name}", value)
    with open(ANNUNCIATE_YAML) as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = annunciate.Plugin("annunciate", conf["annunciate"], meta)
    pl.run()
    # Running engine, airborne, every value normal: releases the start latches
    for key, value in [("TACH1", 2400.0), ("IAS", 120.0), *NORMAL.items()]:
        database.write(key, value)
    yield database
    pl.stop()


def _annunciating(db):
    for key, value in ALARM.items():
        db.write(key, value)
    return {key: db.get_raw_item(key).annunciate for key in ALARM}


def test_no_shipped_bypass_rests_on_one_input():
    for item in _items():
        cond = item.get("cond_bypass")
        if cond is None or cond == "None":
            continue
        assert isinstance(cond, list) and len(cond) >= 2, item["key"]


def test_alarms_fire_in_cruise(plugin):
    assert _annunciating(plugin) == {key: True for key in ALARM}


def test_tach_reading_zero_in_flight_leaves_alarms_armed(plugin):
    plugin.write("TACH1", 0.0)
    assert _annunciating(plugin) == {key: True for key in ALARM}


def test_tach_failed_in_flight_leaves_alarms_armed(plugin):
    plugin.write("TACH1", 0.0)
    plugin.get_raw_item("TACH1").fail = True
    assert _annunciating(plugin) == {key: True for key in ALARM}


def test_tach_and_airspeed_lost_leaves_alarms_armed(plugin):
    plugin.write("TACH1", 0.0)
    plugin.write("IAS", 0.0)
    plugin.get_raw_item("IAS").timestamp = time.time() - 3.0  # timed out
    assert _annunciating(plugin) == {key: True for key in ALARM}


def test_engine_stopped_on_ground_is_still_bypassed(plugin):
    plugin.write("TACH1", 0.0)
    plugin.write("IAS", 0.0)
    assert _annunciating(plugin) == {key: False for key in ALARM}
