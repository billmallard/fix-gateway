"""An xplane.yaml copied before TALTSRC1 still gets TALT (AER-6458).

TALTSRC1 added a ``TALT`` dataref to the shipped ``connections/xplane.yaml``.
Installs keep the config they copied, so an X-Plane panel set up before it
would map ``LAT`` but not ``TALT``, and the SVS, which reads only ``TALT``
(pyEfis AI-SVS-002), would show SVS UNAVAIL. The plugin subscribes the sim's
elevation for ``TALT`` whenever the config maps ``LAT`` and not ``TALT``.
"""

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from fixgw.plugins.xplane import TALT_DATAREF, MainThread

CONFIG = Path(__file__).resolve().parents[3] / "src/fixgw/config"


def shipped():
    with open(CONFIG / "connections" / "xplane.yaml") as f:
        return yaml.safe_load(f)["xplane"]


def plugin(config):
    parent = MagicMock()
    parent.config = config
    with patch("socket.socket"):
        return MainThread(parent)


def talt_entries(xp):
    return [i for i, e in xp.dataref_map.items() if e[0] == "TALT"]


def test_a_config_copied_before_taltsrc1_still_writes_talt():
    cfg = shipped()
    old = {k: v for k, v in cfg["datarefs"].items() if k != "TALT"}
    xp = plugin({"recv": cfg["recv"], "datarefs": old})
    [index] = talt_entries(xp)
    assert xp.dataref_map[index][1:] == TALT_DATAREF

    xp._ingest_rref(b"RREF," + struct.pack("<If", index, 300.0))
    xp.parent.db_write.assert_called_once()
    key, value = xp.parent.db_write.call_args.args
    assert key == "TALT"
    assert abs(value - 984.25) < 0.01


def test_a_data_output_row_for_lat_also_gets_talt():
    xp = plugin({"recv": {20: ["LAT", "LONG", "ALT", "AGL", "_", "_", "_", "_"]}})
    assert len(talt_entries(xp)) == 1


def test_the_shipped_config_subscribes_talt_once():
    cfg = shipped()
    xp = plugin({"recv": cfg["recv"], "datarefs": cfg["datarefs"]})
    assert len(talt_entries(xp)) == 1
    assert len(set(xp.dataref_map)) == len(xp.dataref_map)


def test_a_config_without_position_gets_no_talt():
    xp = plugin({"datarefs": {"IAS": "sim/flightmodel/position/indicated_airspeed"}})
    assert talt_entries(xp) == []
