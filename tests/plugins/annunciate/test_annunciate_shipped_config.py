"""The shipped annunciate.yaml against the shipped database (AER-4370).

A tach that fails and reads 0 in flight must not silence the oil-pressure,
oil-temperature and volts alarms on a running engine.  These tests run the
real plugin on the real config, so a later edit to annunciate.yaml that puts
a single-input bypass back is caught here.

The ground/air airspeed split is the aircraft's IAS.Vgnd setting, 40 kt in
the shipped init_data/default.ini, not a literal in each item (AER-4619).
"""

import shutil
import time

import pytest
import yaml

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


@pytest.fixture
def plugin(database):
    from fixgw.plugins import annunciate

    database.load_init_file(DEFAULT_INI)  # the server's loader
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


# Every cylinder the database declares, from variables.yaml (c: 6 shipped).
# CHT15 and CHT16 had no limits and no annunciate item (fix-gateway#199).
def _cylinder_keys(db):
    return [
        f"{name}1{c}"
        for name in ("CHT", "EGT")
        for c in range(1, db.variables["c"] + 1)
    ]


def test_the_shipped_database_has_six_cylinders(database):
    assert database.variables["c"] == 6


def test_every_cylinder_alarms_above_its_high_limit(plugin):
    fired = {}
    for key in _cylinder_keys(plugin):
        high = plugin.read(f"{key}.highAlarm")
        assert high is not None, key
        plugin.write(key, high - 20.0)
        plugin.write(key, high + 5.0)
        fired[key] = plugin.get_raw_item(key).annunciate
    assert fired == {key: True for key in _cylinder_keys(plugin)}


def test_cylinder_five_alarms_on_its_limit_alone(plugin):
    # The annunciate list on its own: a limit set by hand, not from the ini
    plugin.write("CHT15.highAlarm", 230.0)
    plugin.write("CHT15", 200.0)
    plugin.write("CHT15", 240.0)
    assert plugin.get_raw_item("CHT15").annunciate is True
    plugin.write("CHT15", 200.0)
    assert plugin.get_raw_item("CHT15").annunciate is False


AUX = ["Min", "Max", "lowWarn", "highWarn", "lowAlarm", "highAlarm"]


def test_every_cylinder_has_cylinder_ones_limits(database):
    database.load_init_file(DEFAULT_INI)
    limits = {k: [database.read(f"{k}.{a}") for a in AUX] for k in _cylinder_keys(database)}
    assert None not in limits["CHT11"] and None not in limits["EGT11"]
    assert limits == {
        k: limits["CHT11"] if k.startswith("CHT") else limits["EGT11"]
        for k in limits
    }


def test_a_later_line_still_sets_one_cylinder(database, tmp_path):
    overlay = tmp_path / "engine.ini"
    overlay.write_text("CHT13.highAlarm = 220\n")
    database.load_init_file(DEFAULT_INI)
    database.load_init_file(str(overlay))
    assert database.read("CHT13.highAlarm") == 220.0
    assert database.read("CHT14.highAlarm") == pytest.approx(232.2222222)


@pytest.fixture
def four_cylinder_database(database, tmp_path):
    # The shipped config with c: 4, as a four-cylinder install sets it
    config = tmp_path / "config"
    shutil.copytree("src/fixgw/config", config)
    variables = config / "database" / "variables.yaml"
    doc = yaml.safe_load(variables.read_text())
    doc["variables"]["c"] = 4
    variables.write_text(yaml.safe_dump(doc))
    database.init(str(config / "database.yaml"))
    assert database.variables["c"] == 4
    return database, config


def test_a_four_cylinder_config_loads_and_checks_four(four_cylinder_database):
    from fixgw.plugins import annunciate

    database, config = four_cylinder_database
    database.load_init_file(str(config / "init_data" / "default.ini"))
    assert database.read("CHT14.highAlarm") == pytest.approx(232.2222222)
    with pytest.raises(KeyError):
        database.get_raw_item("CHT15")
    with open(config / "connections" / "annunciate.yaml") as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = annunciate.Plugin("annunciate", conf["annunciate"], meta)
    pl.run()
    try:
        keys = [i.key for i in pl.items if i.key[:3] in ("CHT", "EGT")]
        assert keys == _cylinder_keys(database)
        assert len(keys) == 8
    finally:
        pl.stop()


def test_expand_key(database):
    assert database.expand_key("CHTec") == [f"CHT1{c}" for c in range(1, 7)]
    assert database.expand_key("EGTec.highAlarm") == [
        f"EGT1{c}.highAlarm" for c in range(1, 7)
    ]
    assert database.expand_key("CHT13.lowWarn") == ["CHT13.lowWarn"]
    assert database.expand_key("IAS.Vgnd") == ["IAS.Vgnd"]
    assert database.expand_key("FUELQt") == ["FUELQ1", "FUELQ2", "FUELQ3"]
    # A letter this database does not set is left alone
    assert database.expand_key("CHTxq") == ["CHTxq"]
