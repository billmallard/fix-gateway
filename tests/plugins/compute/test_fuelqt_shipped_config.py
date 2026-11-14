"""FUELQT sums the tanks that exist (FUEL1 brief row F7, decision D4; AER-5391).

The shipped compute.yaml summed a hard-coded FUELQ1..FUELQ3, and sum published
nothing until every input had been written once. CAN-FIX maps only FUELQ1 and
FUELQ2 (canfix/map.yaml), so on a 2-tank CAN install FUELQT was never written.

Now the sum's inputs follow the database's tank count `t` (FUELQ1..FUELQ<t>),
and it publishes from the tanks that have been written, flagged old, bad or
failed when one of those is. A tank no source has ever written is left out
rather than flagged, or every install with fewer tanks than `t` would show a
flagged total for good.
"""

import io
import time

import pytest

import fixgw.database as fixdatabase
import fixgw.plugins.compute as compute
from fixgw import cfg

COMPUTE_YAML = "src/fixgw/config/connections/compute.yaml"


def _compute_config():
    with open(COMPUTE_YAML) as f:
        return cfg.from_yaml(f.read(), metadata=True)


def _fuelqt_function():
    cc, _ = _compute_config()
    writers = [f for f in cc["compute"]["functions"] if f["output"] == "FUELQT"]
    assert len(writers) == 1
    return writers[0]


# --- The shipped database and the whole shipped compute.yaml ---------------


@pytest.fixture
def shipped(database):
    cc, meta = _compute_config()
    pl = compute.Plugin("compute", cc["compute"], meta)
    pl.start()
    time.sleep(0.1)
    yield database
    pl.shutdown()


def test_fuelqt_is_written_from_the_two_can_tanks_alone(shipped):
    db = shipped
    db.write("FUELQ1", 10.0)
    db.write("FUELQ2", 12.5)          # FUELQ3 is never written, as on CAN
    value, _, old, bad, fail, _ = db.read("FUELQT")
    assert value == pytest.approx(22.5)
    assert (old, bad, fail) == (False, False, False)
    assert "compute" in fixdatabase.writers()["FUELQT"]


def test_fuelqt_is_a_sum_of_the_tank_pattern():
    f = _fuelqt_function()
    assert f["function"] == "sum"
    assert f["inputs"] == ["FUELQt"]


# --- The sum follows `t` -----------------------------------------------------


# Starts at the real time: init() stamps every key with time.time(), and a
# key no test writes must age from that stamp.
class Clock:
    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now


def _db(tanks):
    return f"""
variables:
  t: {tanks}
entries:
- {{key: FUELQt, description: Fuel Quantity Tank %t, type: float, min: 0.0, max: 200.0, initial: 0.0, tol: 2000}}
- {{key: FUELQT, description: Total Fuel Quantity, type: float, min: 0.0, max: 200.0, initial: 0.0, tol: 2000}}
"""


@pytest.fixture
def tanks(monkeypatch):
    plugins = []
    clock = Clock()

    def make(t):
        fixdatabase.init(io.StringIO(_db(t)))
        fixdatabase._stop_update_thread()  # the test ticks the clock itself
        monkeypatch.setattr(fixdatabase, "time", clock)
        monkeypatch.setattr(compute.quorum, "leader", True)
        _, meta = _compute_config()
        pl = compute.Plugin("compute", {"functions": [_fuelqt_function()]}, meta)
        pl.start()
        plugins.append(pl)
        return clock

    yield make
    for pl in plugins:
        pl.shutdown()


@pytest.mark.parametrize("t", [1, 2, 4, 6])
def test_fuelqt_sums_every_tank_up_to_t(tanks, t):
    tanks(t)
    for n in range(1, t + 1):
        fixdatabase.write(f"FUELQ{n}", float(n))
    assert fixdatabase.read("FUELQT")[0] == pytest.approx(t * (t + 1) / 2)


def test_fuelqt_publishes_from_the_first_tank_written(tanks):
    tanks(4)
    fixdatabase.write("FUELQ2", 7.0)
    value, _, old, bad, fail, _ = fixdatabase.read("FUELQT")
    assert value == pytest.approx(7.0)
    assert (old, bad, fail) == (False, False, False)


# --- Flagged when a written tank is old, bad or failed ----------------------


def test_a_failed_tank_fails_fuelqt(tanks):
    tanks(3)
    fixdatabase.write("FUELQ1", 10.0)
    fixdatabase.write("FUELQ2", (12.0, False, False, True))  # (value, annunc, bad, fail)
    value, _, _, _, fail, _ = fixdatabase.read("FUELQT")
    assert fail is True and value == 0.0


def test_a_bad_tank_marks_fuelqt_bad(tanks):
    tanks(3)
    fixdatabase.write("FUELQ1", 10.0)
    fixdatabase.write("FUELQ2", (12.0, False, True, False))
    value, _, old, bad, fail, _ = fixdatabase.read("FUELQT")
    assert value == pytest.approx(22.0)
    assert (old, bad, fail) == (False, True, False)


def test_a_tank_that_stops_reporting_makes_fuelqt_old(tanks):
    clock = tanks(3)
    fixdatabase.write("FUELQ1", 10.0)
    fixdatabase.write("FUELQ2", 12.0)
    clock.now += 2.5                   # FUELQ2 outlives its 2000 ms tol
    fixdatabase.write("FUELQ1", 9.5)   # FUELQ1 keeps reporting
    assert fixdatabase.read("FUELQ2")[2] is True
    assert fixdatabase.read("FUELQT")[2] is True
    fixdatabase.write("FUELQ2", 11.5)  # and comes back
    value, _, old, bad, fail, _ = fixdatabase.read("FUELQT")
    assert value == pytest.approx(21.0)
    assert (old, bad, fail) == (False, False, False)


def test_a_tank_never_written_does_not_flag_fuelqt(tanks):
    clock = tanks(3)
    fixdatabase.write("FUELQ1", 10.0)
    fixdatabase.write("FUELQ2", 12.0)
    clock.now += 2.5                   # FUELQ3 was never written: long old
    fixdatabase.update()
    fixdatabase.write("FUELQ1", 10.0)
    fixdatabase.write("FUELQ2", 12.0)
    assert fixdatabase.read("FUELQ3")[2] is True
    value, _, old, bad, fail, _ = fixdatabase.read("FUELQT")
    assert value == pytest.approx(22.0)
    assert (old, bad, fail) == (False, False, False)
