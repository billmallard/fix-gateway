"""The shipped CAN-FIX maps take every cylinder up to c (CYL1, AER-6456).

CHT (1280/1281) and EGT (1282/1283) carry one id per engine and the cylinder
in the index (canfix-spec). The maps listed indexes 0-3 by hand, so with c: 6
in variables.yaml a frame for cylinder 5 or 6 was dropped before the database,
and CHTMAX1, which covers CHT1c since G94 (AER-6244), never saw it. The maps
now carry one `index: c` row per id, which the loader expands to one input per
cylinder up to c.
"""

import gc
import importlib
import re
import shutil
import time

import can
import can.interfaces.virtual as virtual
import canfix
import pytest

import fixgw.database as fixdatabase
import fixgw.plugins.canfix
import fixgw.plugins.compute as compute
from fixgw import cfg
from fixgw.plugins.canfix import mapping

MAP_YAML = "src/fixgw/config/canfix/map.yaml"
TWO_LANE_MAP_YAML = "src/fixgw/config/canfix/two_lane_map.yaml"
COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"
DEFAULT_INI = "src/fixgw/config/init_data/default.ini"
CYLINDER_IDS = {0x500: "CHT1", 0x501: "CHT2", 0x502: "EGT1", 0x503: "EGT2"}


def _db(tmp_path, engines, cylinders):
    shutil.copy("src/fixgw/config/database.yaml", tmp_path)
    shutil.copytree("src/fixgw/config/database", tmp_path / "database")
    variables = tmp_path / "database" / "variables.yaml"
    text = variables.read_text()
    for var, count in (("e", engines), ("c", cylinders)):
        text, n = re.subn(rf"\b{var}: \d+\b", f"{var}: {count}", text, count=1)
        assert n == 1
    variables.write_text(text)
    importlib.reload(fixdatabase)
    fixdatabase.init(str(tmp_path / "database.yaml"))
    return fixdatabase


def _frame(canid, index, value):
    p = canfix.Parameter()
    p.identifier = canid
    p.index = index
    p.node = 0x30
    p.value = value
    return p.msg


def _send(config_data, mapfile, frames):
    cc, cc_meta = cfg.from_yaml(
        config_data.replace("tests/config/canfix/map.yaml", str(mapfile)),
        metadata=True,
    )
    pl = fixgw.plugins.canfix.Plugin("canfix", cc, cc_meta)
    pl.start()
    bus = can.Bus(cc["channel"], interface=cc["interface"])
    time.sleep(0.1)
    try:
        for canid, index, value in frames:
            bus.send(_frame(canid, index, value))
            time.sleep(0.01)
        time.sleep(0.1)
    finally:
        pl.stop()
        bus.shutdown()
    return pl


@pytest.mark.parametrize("mapfile", [MAP_YAML, TWO_LANE_MAP_YAML])
def test_cylinder_5_on_six_cylinders_sets_cht15_and_raises_chtmax1(
    config_data, tmp_path, mapfile
):
    db = _db(tmp_path, 1, 6)
    db.load_init_file(DEFAULT_INI)
    with open(COMPUTE_YAML) as f:
        cc, meta = cfg.from_yaml(f.read(), metadata=True)
    pl = compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    try:
        high_alarm = db.get_raw_item("CHTMAX1").get_aux_value("highAlarm")
        frames = [(0x500, i, 180.0) for i in range(4)] + [(0x500, 4, 240.0)]
        _send(config_data, mapfile, frames)
        assert db.read("CHT15")[0] == pytest.approx(240.0)
        assert db.read("CHT14")[0] == pytest.approx(180.0)
        assert db.read("CHTMAX1")[0] == pytest.approx(240.0)
        assert db.read("CHTMAX1")[0] > high_alarm
    finally:
        pl.shutdown()


def test_two_engines_of_24_cylinders_map_each_index_to_its_own_key(
    config_data, tmp_path
):
    db = _db(tmp_path, 2, 24)
    # A distinct value at every id and index: each key ends up holding its
    # own, so no two inputs share a key and none is missing.
    frames, expect = [], {}
    for n, (canid, prefix) in enumerate(CYLINDER_IDS.items()):
        for i in range(24):
            value = 100.0 + 25 * n + i
            frames.append((canid, i, value))
            expect[f"{prefix}{i + 1}"] = value
    _send(config_data, MAP_YAML, frames)
    assert len(expect) == 96
    assert {k: db.read(k)[0] for k in expect} == pytest.approx(expect)
    assert db.read("CHT224")[0] == pytest.approx(expect["CHT224"])


def test_an_index_past_c_is_ignored_not_an_error(config_data, tmp_path):
    db = _db(tmp_path, 1, 6)
    # Index 5 is the last cylinder; 6 and up are past c.
    frames = [(0x500, 6, 250.0), (0x500, 255, 251.0), (0x502, 6, 700.0)]
    frames += [(0x500, 5, 190.0), (0x502, 5, 650.0)]
    pl = _send(config_data, MAP_YAML, frames)
    assert db.read("CHT16")[0] == pytest.approx(190.0)
    assert db.read("EGT16")[0] == pytest.approx(650.0)
    for n in range(1, 6):
        assert db.read(f"CHT1{n}")[0] == pytest.approx(0.0)
        assert db.read(f"EGT1{n}")[0] == pytest.approx(0.0)
    status = pl.get_status()
    assert status["Received Frames"] == 5
    assert status["Invalid Frames"] == 0


def test_the_shipped_maps_list_no_cylinder_by_hand():
    for path in (MAP_YAML, TWO_LANE_MAP_YAML):
        with open(path) as f:
            inputs = cfg.from_yaml(f.read())["inputs"]
        rows = [r for r in inputs if r["canid"] in CYLINDER_IDS]
        assert rows == [
            {"canid": canid, "index": "c", "fixid": f"{prefix}c", "priority": 0}
            for canid, prefix in CYLINDER_IDS.items()
        ], path


def _map(tmp_path, row, ignore_missing):
    p = tmp_path / "map.yaml"
    p.write_text(
        f"ignore_fixid_missing: {'true' if ignore_missing else 'false'}\n"
        "meta replacements:\n  Low Warn: lowWarn\n"
        f"inputs:\n  - {row}\n"
        "outputs: []\nencoders: []\nswitches: []\n"
    )
    return str(p)


def test_an_index_variable_must_be_named_in_the_fixid(database, tmp_path):
    row = '{ canid: 0x500, index: c, fixid: "CHT11" }'
    for ignore_missing in (True, False):
        with pytest.raises(ValueError, match="index 'c'"):
            mapping.Mapping(_map(tmp_path, row, ignore_missing))


def test_an_index_variable_the_database_lacks(database, tmp_path):
    row = '{ canid: 0x500, index: z, fixid: "CHT1z" }'
    with pytest.raises(ValueError, match="index 'z'"):
        mapping.Mapping(_map(tmp_path, row, False))
    m = mapping.Mapping(_map(tmp_path, row, True))
    assert m.input_mapping[0x400] is None


def test_a_stopped_plugin_costs_the_next_one_no_frame(
    config_data, tmp_path, monkeypatch
):
    # AER-6615: stop() left the plugin's bus open, so it stayed on the
    # virtual channel until the garbage collector freed it. When that
    # happened inside VirtualBus.send, the freed bus took itself out of the
    # receiver list send was walking, and the next receiver, the live
    # plugin, lost the frame. CI lost CHT28 that way. The first plugin's
    # output callbacks keep its bus alive until a new database replaces
    # them, as the next test's _db does. Automatic collection is off and
    # send collects at every copy, so the moment is the same every run.
    deepcopy = virtual.deepcopy

    def collect_then_copy(msg):
        gc.collect()
        return deepcopy(msg)

    gc.disable()
    try:
        (tmp_path / "first").mkdir()
        _db(tmp_path / "first", 1, 6)
        _send(config_data, MAP_YAML, [])
        db = _db(tmp_path, 1, 6)
        monkeypatch.setattr(virtual, "deepcopy", collect_then_copy)
        frames = [(0x500, i, 180.0 + i) for i in range(6)]
        pl = _send(config_data, MAP_YAML, frames)
    finally:
        gc.enable()
    assert pl.get_status()["Received Frames"] == 6
    assert [db.read(f"CHT1{i + 1}")[0] for i in range(6)] == pytest.approx(
        [180.0 + i for i in range(6)]
    )
