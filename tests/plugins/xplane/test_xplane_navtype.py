"""X-Plane's tuned navaid type reaches NAVTYPE through NAVSRC (fix-gateway#135).

pyEfis's HSI reads NAVTYPE (0=GPS, 1=VOR, 2=LOC, 3=LOC-BC) to label the source
VOR{n}/LOC{n} instead of VLOC{n}, and to refuse an OBS course push on a
localizer. fix-gateway defined no NAVTYPE and wrote none, so every panel read
VLOC and a CRS push on a localizer wrote the VOR bearing into NAV{n}CRS.

The plugin tests drive the dataref code map directly. The gateway tests run the
shipped config end to end, without a sim: the xplane plugin's dataref map built
from ``connections/xplane.yaml`` ingests an RREF packet into the real database,
and the shipped ``connections/compute.yaml`` select routes it into NAVTYPE.
"""

import struct
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import fixgw.database as database
import fixgw.plugins.compute
from fixgw import cfg
from fixgw.plugins.xplane import MainThread, _build_dataref_map

CONFIG = Path(__file__).resolve().parents[3] / "src/fixgw/config"
NAV1 = "sim/cockpit/radios/nav_type[0]"
NAV2 = "sim/cockpit/radios/nav_type[1]"

# X-Plane navaid codes: 0 nothing tuned, 2 NDB, 3 VOR, 4 ILS localizer,
# 5 standalone localizer, 12 DME.
XP_NONE, XP_NDB, XP_VOR, XP_ILS, XP_LOC, XP_DME = 0, 2, 3, 4, 5, 12


def shipped(name):
    with open(CONFIG / "connections" / name) as f:
        return yaml.safe_load(f)


def rref(xp, **values):
    """An RREF reply carrying ``values`` by dataref name."""
    idx = {e[1]: i for i, e in xp.dataref_map.items()}
    return b"RREF," + b"".join(struct.pack("<If", idx[d], v) for d, v in values.items())


# ---------------------------------------------------------------------------
# The plugin's {code: value} dataref map
# ---------------------------------------------------------------------------

def test_build_dataref_map_parses_a_code_map():
    m = _build_dataref_map({"NAV1TYPE": [NAV1, {"3": 1, 4: 2}]})
    (key, dref, table), = m.values()
    assert (key, dref) == ("NAV1TYPE", NAV1)
    assert table == {3: 1.0, 4: 2.0}


@pytest.fixture
def mapped():
    parent = MagicMock()
    parent.config = {"datarefs": {"NAV1TYPE": [NAV1, {3: 1, 4: 2, 5: 2}]}}
    with patch("socket.socket"):
        xp = MainThread(parent)
    return xp, parent


@pytest.mark.parametrize("code, want", [
    (XP_VOR, 1.0), (XP_ILS, 2.0), (XP_LOC, 2.0),
    (XP_NONE, 0.0), (XP_NDB, 0.0), (XP_DME, 0.0),   # unlisted -> 0
    (3.0000002, 1.0),                                # rounded, not truncated
])
def test_code_map_translates_and_unlisted_reads_zero(mapped, code, want):
    xp, parent = mapped
    xp._ingest_rref(rref(xp, **{NAV1: code}))
    parent.db_write.assert_called_once_with("NAV1TYPE", want)


def test_code_map_survives_a_nan(mapped):
    xp, parent = mapped
    xp._ingest_rref(rref(xp, **{NAV1: float("nan")}))     # logged, not raised
    parent.db_write.assert_not_called()


# ---------------------------------------------------------------------------
# The shipped config, end to end
# ---------------------------------------------------------------------------

def test_shipped_rows_map_xplane_nav_type():
    rows = shipped("xplane.yaml")["xplane"]["datarefs"]
    assert rows["NAV1TYPE"] == [NAV1, {3: 1, 4: 2, 5: 2}]
    assert rows["NAV2TYPE"] == [NAV2, {3: 1, 4: 2, 5: 2}]


def test_shipped_rows_survive_the_gateway_config_loader():
    # The server parses connection files with fixgw.cfg, not yaml.safe_load.
    with open(CONFIG / "connections" / "xplane.yaml") as f:
        rows = cfg.from_yaml(f.read())["xplane"]["datarefs"]
    m = _build_dataref_map(rows)
    by_key = {e[0]: e for e in m.values()}
    assert by_key["NAV1TYPE"][1:] == (NAV1, {3: 1.0, 4: 2.0, 5: 2.0})
    assert by_key["NAV2TYPE"][1:] == (NAV2, {3: 1.0, 4: 2.0, 5: 2.0})


def test_navtype_keys_are_declared_and_navtype_has_one_writer():
    database.init(str(CONFIG / "database.yaml"))
    for key in ("NAV1TYPE", "NAV2TYPE", "NAVTYPE"):
        item = database.get_raw_item(key)
        assert (item.min, item.max) == (0.0, 3.0)
    writers = [f for f in shipped("compute.yaml")["compute"]["functions"]
               if f["output"] == "NAVTYPE"]
    assert writers == [{
        "function": "select",
        "inputs": ["NAVSRC", "NAV1TYPE", "NAV2TYPE", 0.0],
        "output": "NAVTYPE",
        "require_leader": False,
    }]


@pytest.fixture
def gateway():
    database.init(str(CONFIG / "database.yaml"))
    compute = shipped("compute.yaml")["compute"]
    cc, cc_meta = cfg.from_yaml(yaml.safe_dump(compute), metadata=True)
    pl = fixgw.plugins.compute.Plugin("compute", cc, cc_meta)
    pl.start()
    time.sleep(0.1)

    parent = MagicMock()
    parent.config = {"datarefs": shipped("xplane.yaml")["xplane"]["datarefs"]}
    parent.db_write = database.write
    with patch("socket.socket"):
        xp = MainThread(parent)
    yield xp
    pl.shutdown()


def test_navtype_follows_the_selected_radio(gateway):
    gateway._ingest_rref(rref(gateway, **{NAV1: XP_VOR, NAV2: XP_ILS}))
    assert database.read("NAV1TYPE")[0] == 1
    assert database.read("NAV2TYPE")[0] == 2

    database.write("NAVSRC", 0)
    assert database.read("NAVTYPE")[0] == 1          # NAV1 on a VOR -> "VOR1"
    database.write("NAVSRC", 1)
    assert database.read("NAVTYPE")[0] == 2          # NAV2 on an ILS -> "LOC2"
    database.write("NAVSRC", 2)
    assert database.read("NAVTYPE")[0] == 0          # GPS


def test_navtype_follows_a_retune(gateway):
    database.write("NAVSRC", 0)
    for code, want in ((XP_VOR, 1), (XP_LOC, 2), (XP_NONE, 0), (XP_ILS, 2)):
        gateway._ingest_rref(rref(gateway, **{NAV1: code}))
        assert database.read("NAVTYPE")[0] == want
