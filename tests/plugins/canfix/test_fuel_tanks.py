"""The shipped CAN-FIX map routes every Fuel Quantity id to its tank (AER-5465).

Tank n is id 550 + (n-1) mod 4, index floor((n-1)/4) (canfix-spec #7). The
map carries tanks 1-4 at index 0 and a commented row for tank 5 on id 550,
index 1. The database is built with t: 5 so FUELQ4 and FUELQ5 exist.
"""

import importlib
import re
import shutil
import time

import can
import canfix
import pytest

import fixgw.database as fixdatabase
import fixgw.plugins.canfix
from fixgw import cfg

MAP_YAML = "src/fixgw/config/canfix/map.yaml"
TANK5_ROW = '# - { canid: 0x226, index: 1, fixid: "FUELQ5", priority: 0 }'


def _tank_frame(tank, value):
    p = canfix.Parameter()
    p.identifier = 0x226 + (tank - 1) % 4
    p.index = (tank - 1) // 4
    p.node = 0x30
    p.value = value
    return p.msg


@pytest.fixture
def five_tank_db(tmp_path):
    shutil.copy("src/fixgw/config/database.yaml", tmp_path)
    shutil.copytree("src/fixgw/config/database", tmp_path / "database")
    variables = tmp_path / "database" / "variables.yaml"
    text, n = re.subn(r"\bt: 3\b", "t: 5", variables.read_text())
    assert n == 1
    variables.write_text(text)
    importlib.reload(fixdatabase)
    fixdatabase.init(str(tmp_path / "database.yaml"))
    return fixdatabase


def _start(config_data, mapfile):
    cc, cc_meta = cfg.from_yaml(
        config_data.replace("tests/config/canfix/map.yaml", str(mapfile)),
        metadata=True,
    )
    pl = fixgw.plugins.canfix.Plugin("canfix", cc, cc_meta)
    pl.start()
    bus = can.Bus(cc["channel"], interface=cc["interface"])
    time.sleep(0.1)
    return pl, bus


def _run(config_data, mapfile, db, sends):
    pl, bus = _start(config_data, mapfile)
    try:
        for tank, value in sends:
            bus.send(_tank_frame(tank, value))
            time.sleep(0.03)
        return {f"FUELQ{t}": db.read(f"FUELQ{t}")[0] for t in range(1, 6)}
    finally:
        pl.stop()
        bus.shutdown()


def test_shipped_map_routes_tanks_1_to_4_and_not_index_1(config_data, five_tank_db):
    sends = [(1, 11.0), (2, 12.0), (3, 13.0), (4, 14.0), (5, 15.0)]
    values = _run(config_data, MAP_YAML, five_tank_db, sends)
    assert values["FUELQ1"] == pytest.approx(11.0)
    assert values["FUELQ2"] == pytest.approx(12.0)
    assert values["FUELQ3"] == pytest.approx(13.0)
    assert values["FUELQ4"] == pytest.approx(14.0)
    # Tank 5 (id 550, index 1) is commented out: it writes nothing, and above
    # all it does not land on tank 1.
    assert values["FUELQ5"] == pytest.approx(0.0)


def test_uncommented_tank5_example_routes_index_1(config_data, five_tank_db, tmp_path):
    with open(MAP_YAML) as f:
        text = f.read()
    assert text.count(TANK5_ROW) == 1
    mapfile = tmp_path / "map_tank5.yaml"
    mapfile.write_text(text.replace(TANK5_ROW, TANK5_ROW[2:]))
    values = _run(config_data, mapfile, five_tank_db, [(1, 21.0), (5, 25.0)])
    assert values["FUELQ1"] == pytest.approx(21.0)
    assert values["FUELQ5"] == pytest.approx(25.0)
