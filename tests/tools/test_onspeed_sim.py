"""tools/onspeed_sim.py: the OnSpeed sentence simulator (onspeed spec 7.3)."""

import importlib.util
import io
import os
import pty
import time
import tty

import pytest

import fixgw.database as database
import fixgw.plugins.onspeed as onspeed
from fixgw.plugins.onspeed.sentence import parse

_spec = importlib.util.spec_from_file_location("onspeed_sim", "tools/onspeed_sim.py")
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

PERIOD = int((sim.TAXI_S + sim.SWEEP_S * len(sim.SETPOINTS)) * sim.RATE_HZ)


def _frames(n=PERIOD):
    return [parse(line) for line in sim.sentences(n)]


def test_every_sentence_of_a_full_profile_decodes():
    assert len(_frames()) == PERIOD


def test_each_flap_sweep_crosses_its_warning_aoa():
    frames = _frames()
    for flaps, (_t, _f, _s, warn) in sim.SETPOINTS.items():
        sweep = [f for f in frames if f.flaps == flaps and f.ias >= 30]
        assert sweep, flaps
        assert {f.stall_warn_aoa for f in sweep} == {warn}
        assert min(f.aoa for f in sweep) < 1.0
        assert max(f.aoa for f in sweep) > warn


def test_taxi_frames_carry_the_low_speed_zero():
    taxi = [f for f in _frames() if f.ias < 25]
    assert taxi
    assert all(f.aoa == 0 and f.percent_lift == 0 for f in taxi)


def test_setpoints_are_in_onspeed_order():
    for tones, fast, slow, warn in sim.SETPOINTS.values():
        assert tones < fast <= slow <= warn


DB_CONFIG = """
variables: {}
entries:
- key: AOA
  description: Angle of attack
  type: float
  min: -180.0
  max: 180.0
  units: deg
  initial: 0.0
  tol: 300
  aux: [Min, Max, 0g, Warn, Stall]
"""


def test_plugin_reads_the_simulator_through_a_real_pty():
    # End to end over a real port: pyserial opens the pty slave by name, as
    # it would /dev/ttyUSB0, and the plugin thread runs as in the gateway.
    pytest.importorskip("serial")
    database.init(io.StringIO(DB_CONFIG))
    master, slave = pty.openpty()
    tty.setraw(slave)
    p = onspeed.Plugin("onspeed", {"port": os.ttyname(slave)}, {})
    p.run()
    try:
        deadline = time.monotonic() + 5
        while not p.thread.port_open and time.monotonic() < deadline:
            time.sleep(0.01)
        assert p.thread.port_open
        lines = list(sim.sentences(int(sim.TAXI_S * sim.RATE_HZ) + 40))
        for line in lines:
            os.write(master, line)
        deadline = time.monotonic() + 5
        while p.thread.counters["lines"] < len(lines) - 1 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        p.stop()
        os.close(master)
        os.close(slave)
    c = p.thread.counters
    # The first line after open is always discarded; every other one decodes.
    assert c["lines"] == len(lines) - 1
    assert c["frames"] == c["lines"]
    assert c["low_ias"] > 0
    value, _a, _old, bad, fail, _s = database.read("AOA")
    assert (bad, fail) == (False, False)
    assert value == parse(lines[-1]).aoa
    assert database.read("AOA.Warn") == sim.SETPOINTS[0][3]
