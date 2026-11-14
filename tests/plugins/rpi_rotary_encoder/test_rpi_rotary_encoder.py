import os
import sys
import types

import pytest

from fixgw import cfg

SHIPPED_YAML = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "src", "fixgw", "config", "connections",
    "baro_rpi_rotary_encoder.yaml",
)


class FakeGPIO:
    BCM = "BCM"
    IN = "IN"
    PUD_UP = "PUD_UP"
    PUD_DOWN = "PUD_DOWN"

    def __init__(self):
        self.levels = {}

    def setmode(self, mode):
        pass

    def setup(self, pin, direction, pull_up_down=None):
        self.levels.setdefault(pin, 0)

    def input(self, pin):
        return self.levels[pin]


@pytest.fixture
def encoder(monkeypatch):
    gpio = FakeGPIO()
    rpi = types.ModuleType("RPi")
    rpi.GPIO = gpio
    monkeypatch.setitem(sys.modules, "RPi", rpi)
    monkeypatch.setitem(sys.modules, "RPi.GPIO", gpio)
    monkeypatch.delitem(sys.modules, "fixgw.plugins.rpi_rotary_encoder", raising=False)
    import fixgw.plugins.rpi_rotary_encoder as mod

    return mod, gpio


class FakeLog:
    def __getattr__(self, name):
        return lambda *a, **k: None


class FakeParent:
    def __init__(self, config):
        self.config = config
        self.log = FakeLog()
        self.writes = []

    def db_write(self, key, value):
        self.writes.append((key, value))


def shipped_config():
    with open(SHIPPED_YAML) as f:
        data, _ = cfg.parse_yaml_with_metadata(f, SHIPPED_YAML)
    return data["rotary_encoder"]


def turn_once(monkeypatch, mod, gpio, thread, button_held):
    """One detent: CLK goes high with DT low, then run() stops."""
    gpio.levels[thread.btnpin] = 0 if button_held else 1  # pulled up, low when held
    gpio.levels[thread.dt] = 0

    calls = []

    def fake_sleep(_):
        calls.append(None)
        if len(calls) == 1:
            gpio.levels[thread.clk] = 1
        else:
            thread.getout = True

    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    thread.run()


def test_shipped_yaml_loads_btn_as_bool():
    # The premise of the bug: YAML gives the plugin a bool, not "True".
    assert shipped_config()["btn"] is True


def test_shipped_config_button_held_turn_writes_baro(monkeypatch, encoder):
    mod, gpio = encoder
    parent = FakeParent(shipped_config())
    thread = mod.MainThread(parent)

    turn_once(monkeypatch, mod, gpio, thread, button_held=True)

    assert parent.writes == [("BARO", pytest.approx(29.93))]


def test_shipped_config_button_released_turn_writes_rkey(monkeypatch, encoder):
    mod, gpio = encoder
    parent = FakeParent(shipped_config())
    thread = mod.MainThread(parent)

    turn_once(monkeypatch, mod, gpio, thread, button_held=False)

    assert parent.writes == [("PITCHSET", 1.0)]


@pytest.mark.parametrize("value", ["True", "true", "TRUE"])
def test_string_true_still_enables_button(monkeypatch, encoder, value):
    mod, gpio = encoder
    config = dict(shipped_config(), btn=value)
    parent = FakeParent(config)
    thread = mod.MainThread(parent)

    turn_once(monkeypatch, mod, gpio, thread, button_held=True)

    assert parent.writes[0][0] == "BARO"


@pytest.mark.parametrize("value", [False, "False", "false", None, "absent"])
def test_button_disabled_turn_writes_rkey_even_when_held(monkeypatch, encoder, value):
    mod, gpio = encoder
    config = dict(shipped_config(), btn=value)
    if value == "absent":
        del config["btn"]
    parent = FakeParent(config)
    thread = mod.MainThread(parent)

    turn_once(monkeypatch, mod, gpio, thread, button_held=True)

    assert parent.writes == [("PITCHSET", 1.0)]
