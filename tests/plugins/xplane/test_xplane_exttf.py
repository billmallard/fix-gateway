"""X-Plane's FMS TO/FROM reaches GPSTF on GPSSRC = 1 (fix-gateway#41).

The shipped ``connections/xplane.yaml`` used to map X-Plane's GPS course and
deviation to EXTCRS/EXTCDI but nothing to EXTTF, so GPSTF stayed at EXTTF's
initial 0 and the HSI's TO/FROM flag never followed X-Plane on NAVSRC = 2.

This runs the shipped config end to end, without a sim: the xplane plugin's
dataref map built from ``connections/xplane.yaml`` ingests an RREF packet
into the real database, and the shipped ``connections/compute.yaml`` selects
route it into GPSTF.
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
from fixgw.plugins.xplane import MainThread

CONFIG = Path(__file__).resolve().parents[3] / "src/fixgw/config"


def shipped(name):
    with open(CONFIG / "connections" / name) as f:
        return yaml.safe_load(f)


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


def rref(xp, **values):
    """An RREF reply carrying ``values`` by dataref name."""
    idx = {e[1]: i for i, e in xp.dataref_map.items()}
    return b"RREF," + b"".join(struct.pack("<If", idx[d], v) for d, v in values.items())


def test_exttf_is_mapped_from_gps_fromto():
    assert shipped("xplane.yaml")["xplane"]["datarefs"]["EXTTF"] == "sim/cockpit/radios/gps_fromto"


def test_gpstf_follows_xplane_on_gpssrc_1(gateway):
    database.write("FPLTF", 1)
    database.write("GPSSRC", 1)
    for fromto in (1.0, 2.0, 1.0):
        gateway._ingest_rref(rref(gateway, **{"sim/cockpit/radios/gps_fromto": fromto}))
        assert database.read("EXTTF")[0] == int(fromto)
        assert database.read("GPSTF")[0] == int(fromto)


def test_gpstf_follows_the_internal_plan_on_gpssrc_0(gateway):
    database.write("GPSSRC", 0)
    database.write("FPLTF", 1)
    gateway._ingest_rref(rref(gateway, **{"sim/cockpit/radios/gps_fromto": 2.0}))
    assert database.read("EXTTF")[0] == 2
    assert database.read("GPSTF")[0] == 1
