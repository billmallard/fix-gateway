"""The shipped fuel-tank annunciation follows the tank count (AER-6138).

The shipped database declares three tanks (``t: 3`` in
database/variables.yaml) and compute's FUELQT sums all three, but the
annunciate list named FUELQ1 and FUELQ2 only and default.ini set no limits for
FUELQ3: a low third tank set no flag while the total still looked healthy.
The list now carries one ``FUELQt`` item and default.ini one ``FUELQt``
block, both expanded by the tank count.

A tank no source writes expires at its initial 0, below its low alarm, and
the expiry fires the callback; ``skip_unwritten`` keeps it quiet until a
source writes it, as FUELQT's sum leaves it out (AER-5391).
"""

import shutil

import pytest

from fixgw import cfg
import fixgw.database as fixdatabase

ANNUNCIATE_YAML = "src/fixgw/config/connections/annunciate.yaml"
DEFAULT_INI = "src/fixgw/config/init_data/default.ini"


def _start(db):
    from fixgw.plugins import annunciate

    with open(ANNUNCIATE_YAML) as f:
        conf, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = annunciate.Plugin("annunciate", conf["annunciate"], meta)
    pl.run()
    return pl


@pytest.fixture
def plugin(database):
    # The server's initialization-file loader, which expands FUELQt.*
    fixdatabase.load_init_file(DEFAULT_INI)
    pl = _start(database)
    yield database, pl
    pl.stop()


def _tanks():
    return ["FUELQ{}".format(n) for n in range(1, fixdatabase.variables["t"] + 1)]


def test_shipped_database_has_three_tanks(database):
    assert _tanks() == ["FUELQ1", "FUELQ2", "FUELQ3"]


def test_every_tank_has_an_item(plugin):
    _db, pl = plugin
    keys = [i.key for i in pl.items]
    assert [k for k in keys if k.startswith("FUELQ") and k != "FUELQT"] == _tanks()
    assert "FUELQT" in keys


@pytest.mark.parametrize("n", [1, 2, 3])
def test_every_tank_has_shipped_limits(plugin, n):
    db, _pl = plugin
    key = "FUELQ{}".format(n)
    assert db.read(key + ".Min") == 0.0
    assert db.read(key + ".Max") == 21.0
    assert db.read(key + ".lowWarn") == 5.0
    assert db.read(key + ".lowAlarm") == 1.0


@pytest.mark.parametrize("n", [1, 2, 3])
def test_a_tank_below_its_low_alarm_annunciates(plugin, n):
    db, _pl = plugin
    key = "FUELQ{}".format(n)
    db.write(key, 10.0)
    assert db.get_raw_item(key).annunciate is False
    db.write(key, 0.5)
    assert db.get_raw_item(key).annunciate is True


def test_third_tank_low_while_the_total_looks_healthy(plugin):
    # The FHA EMS-2 case: two full tanks, a nearly empty third
    db, _pl = plugin
    db.write("FUELQ1", 20.0)
    db.write("FUELQ2", 20.0)
    db.write("FUELQ3", 0.5)
    db.write("FUELQT", 40.5)
    assert db.get_raw_item("FUELQT").annunciate is False
    assert db.get_raw_item("FUELQ3").annunciate is True


def test_third_tank_clears_past_the_deadband(plugin):
    db, _pl = plugin
    db.write("FUELQ3", 0.5)
    assert db.get_raw_item("FUELQ3").annunciate is True
    db.write("FUELQ3", 1.5)  # above lowAlarm, inside the 1% (2 gal) deadband
    assert db.get_raw_item("FUELQ3").annunciate is True
    db.write("FUELQ3", 3.5)
    assert db.get_raw_item("FUELQ3").annunciate is False


def test_a_tank_no_source_writes_is_not_annunciated_when_it_expires(plugin):
    # A two-tank CAN install: FUELQ3 is never written, expires at 0.0
    db, _pl = plugin
    item = db.get_raw_item("FUELQ3")
    assert item.written is False
    item.expire()
    assert item.value[0] == 0.0 and item.old
    assert item.annunciate is False


def test_a_tank_no_source_writes_is_not_annunciated_on_a_limit_change(plugin):
    # A limit written after start (the configurator, netfix) fires the
    # callback too
    db, _pl = plugin
    db.write("FUELQ3.lowAlarm", 2.0)
    assert db.get_raw_item("FUELQ3").annunciate is False


def test_a_written_tank_that_expires_low_still_annunciates(plugin):
    # Once a source has written it, the tank is a fitted tank
    db, _pl = plugin
    db.write("FUELQ3", 0.5)
    item = db.get_raw_item("FUELQ3")
    item.expire()
    assert item.annunciate is True


def test_an_item_without_skip_unwritten_annunciates_on_expiry(plugin):
    # Control: the option, not the expiry path, keeps the unwritten tank quiet
    db, _pl = plugin
    item = db.get_raw_item("FUELQT")
    assert item.written is False
    item.expire()
    assert item.annunciate is True


def test_the_item_list_follows_the_tank_count(database, tmp_path):
    # A four-tank install edits only variables.yaml
    shutil.copy("src/fixgw/config/database.yaml", tmp_path / "database.yaml")
    shutil.copytree("src/fixgw/config/database", tmp_path / "database")
    var = tmp_path / "database" / "variables.yaml"
    text = var.read_text()
    assert "  t: 3  # Fuel Tanks" in text
    var.write_text(text.replace("  t: 3  # Fuel Tanks", "  t: 4  # Fuel Tanks"))
    fixdatabase.init(str(tmp_path / "database.yaml"))
    database.write("FUELQ4.lowAlarm", 1.0)
    pl = _start(database)
    try:
        keys = [i.key for i in pl.items if i.key.startswith("FUELQ")]
        assert keys == ["FUELQ1", "FUELQ2", "FUELQ3", "FUELQ4", "FUELQT"]
        database.write("FUELQ4", 0.5)
        assert database.get_raw_item("FUELQ4").annunciate is True
    finally:
        pl.stop()


def test_status_names_the_option(plugin):
    _db, pl = plugin
    item = [i for i in pl.items if i.key == "FUELQ3"][0]
    assert str(item).endswith("  Skip Unwritten: Yes")
