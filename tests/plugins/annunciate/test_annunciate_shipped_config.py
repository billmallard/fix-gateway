"""The shipped annunciate.yaml against the shipped database (AER-4370).

A tach that fails and reads 0 in flight must not silence the oil-pressure,
oil-temperature and volts alarms on a running engine.  These tests run the
real plugin on the real config, so a later edit to annunciate.yaml that puts
a single-input bypass back is caught here.

The ground/air airspeed split is the aircraft's IAS.Vgnd setting, 40 kt in
the shipped init_data/default.ini, not a literal in each item (AER-4619).
"""

import time

import pytest

from fixgw import cfg

ANNUNCIATE_YAML = "src/fixgw/config/connections/annunciate.yaml"
DEFAULT_INI = "src/fixgw/config/init_data/default.ini"
ENGINE_ALARMS = {"OILP1", "OILT1", "VOLT"}

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


def _load_ini(db, path):
    # The server's initialization-file loader (server.main_setup)
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and line[0] != "#":
                x = line.split("=")
                if len(x) >= 2:
                    db.write(x[0].strip(), x[1].strip())


@pytest.fixture
def plugin(database):
    from fixgw.plugins import annunciate

    _load_ini(database, DEFAULT_INI)
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


def test_every_engine_alarm_bypass_reads_the_one_airspeed_setting():
    terms = {
        item["key"]: [c for c in item["cond_bypass"] if c.split()[0] == "IAS"]
        for item in _items()
        if item["key"] in ENGINE_ALARMS
    }
    assert terms == {key: ["IAS < IAS.Vgnd"] for key in ENGINE_ALARMS}


def test_shipped_airspeed_setting_is_40_kt(plugin):
    assert plugin.read("IAS.Vgnd") == 40.0


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


@pytest.mark.parametrize("ias, bypassed", [(39.0, True), (41.0, False)])
def test_default_split_is_still_40_kt(plugin, ias, bypassed):
    plugin.write("TACH1", 0.0)
    plugin.write("IAS", ias)
    assert _annunciating(plugin) == {key: not bypassed for key in ALARM}


def test_a_slow_type_set_to_25_kt_stays_armed_at_30_kt(plugin):
    plugin.write("IAS.Vgnd", 25.0)
    plugin.write("TACH1", 0.0)
    plugin.write("IAS", 30.0)
    assert _annunciating(plugin) == {key: True for key in ALARM}


def test_a_slow_type_set_to_25_kt_is_bypassed_stopped_below_it(plugin):
    plugin.write("IAS.Vgnd", 25.0)
    plugin.write("TACH1", 0.0)
    plugin.write("IAS", 20.0)
    assert _annunciating(plugin) == {key: False for key in ALARM}


def test_without_the_setting_the_alarms_stay_armed_on_the_ground(database):
    # An install whose init files never set IAS.Vgnd: no bypass, never a
    # silent alarm
    from fixgw.plugins import annunciate

    for key, aux in LIMITS.items():
        for name, value in aux.items():
            database.write(f"{key}.{name}", value)
    with open(ANNUNCIATE_YAML) as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = annunciate.Plugin("annunciate", conf["annunciate"], meta)
    pl.run()
    for key, value in [("TACH1", 2400.0), ("IAS", 120.0), *NORMAL.items()]:
        database.write(key, value)
    database.write("TACH1", 0.0)
    database.write("IAS", 0.0)
    try:
        assert _annunciating(database) == {key: True for key in ALARM}
    finally:
        pl.stop()
