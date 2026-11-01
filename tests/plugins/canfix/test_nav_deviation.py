"""CAN-FIX input rows can scale the decoded value (AER-4477)."""

import time

import can
import canfix
import pytest

import fixgw.plugins.canfix
from fixgw import cfg


def _frame(name, value, index=0):
    p = canfix.Parameter()
    p.name = name
    p.node = 0x30
    p.index = index
    p.value = value
    return p.msg


def _send(bus, msg):
    bus.send(msg)
    time.sleep(0.03)


MAP = """
meta replacements:
  Low Warn: lowWarn
inputs:
  - { canid: 0x1C1, index: 0, fixid: "NAV1GSI", scale: 2.0 }
  - { canid: 0x180, index: 0, fixid: "PITCH" }
outputs: []
encoders: []
switches: []
"""


def _plugin(config_data, tmp_path, map_text):
    mapfile = tmp_path / "map.yaml"
    mapfile.write_text(map_text)
    cc, cc_meta = cfg.from_yaml(
        config_data.replace("tests/config/canfix/map.yaml", str(mapfile)),
        metadata=True,
    )
    return fixgw.plugins.canfix.Plugin("canfix", cc, cc_meta)


def test_scale_multiplies_the_decoded_value(config_data, tmp_path, database):
    pl = _plugin(config_data, tmp_path, MAP)
    pl.start()
    bus = can.Bus("tcan0", interface="virtual")
    time.sleep(0.1)
    try:
        _send(bus, _frame("Glideslope Deviation", 0.25))
        _send(bus, _frame("Pitch Angle", 12.5))
        assert database.read("NAV1GSI")[0] == pytest.approx(0.5)
        assert database.read("PITCH")[0] == pytest.approx(12.5)   # no scale: unchanged
    finally:
        pl.stop()
        bus.shutdown()


@pytest.mark.parametrize("bad", ['"x"', "true", "0"])
def test_scale_must_be_a_nonzero_number(config_data, tmp_path, database, bad):
    with pytest.raises(ValueError, match="scale"):
        _plugin(config_data, tmp_path, MAP.replace("scale: 2.0", f"scale: {bad}"))
