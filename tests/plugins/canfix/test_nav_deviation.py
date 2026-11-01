"""CAN-FIX nav deviation is scaled onto the +/-1 needle keys (AER-4477).

NAV1CDI/NAV1GSI are -1..+1, full-scale needle deflection. CAN-FIX 449
(Glideslope Deviation) is degrees, so a frame written straight into NAV1GSI
pegs the needle at anything past one degree. The input map's per-row `scale`
converts it. 448 (VOR/LOC Deviation) is UINT degrees: it cannot carry a left
deviation, so no scale makes it a needle and the shipped map leaves it out.
"""

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


@pytest.fixture
def shipped(config_data, database):
    cc, cc_meta = cfg.from_yaml(
        config_data.replace(
            "tests/config/canfix/map.yaml", "src/fixgw/config/canfix/map.yaml"
        ),
        metadata=True,
    )
    pl = fixgw.plugins.canfix.Plugin("canfix", cc, cc_meta)
    pl.start()
    bus = can.Bus(cc["channel"], interface=cc["interface"])
    time.sleep(0.1)
    yield pl, bus
    pl.stop()
    bus.shutdown()


@pytest.mark.parametrize(
    "degrees, needle",
    [
        (0.35, 0.5),    # one dot of a two-dot scale: half deflection
        (-0.35, -0.5),
        (0.70, 1.0),    # full scale
        (0.0, 0.0),
        (2.0, 1.0),     # past full scale: the key's max pegs it
    ],
)
def test_glideslope_degrees_scale_onto_the_needle(shipped, database, degrees, needle):
    pl, bus = shipped
    _send(bus, _frame("Glideslope Deviation", degrees))
    assert database.read("NAV1GSI")[0] == pytest.approx(needle, abs=0.002)
    assert pl.get_status()["Invalid Frames"] == 0


def test_vor_loc_deviation_does_not_reach_the_needle(shipped, database):
    pl, bus = shipped
    database.write("NAV1CDI", 0.25)
    _send(bus, _frame("VOR/LOC Deviation", 2.0))
    assert database.read("NAV1CDI")[0] == 0.25


def test_vor_loc_deviation_cannot_encode_left_of_course():
    # Why 448 is unmapped: the spec type is UINT, so the library refuses it.
    with pytest.raises(Exception):
        _frame("VOR/LOC Deviation", -2.0)


# The scale mechanism itself, on a map written for the test.

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
