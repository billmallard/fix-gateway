"""A compute output's value and flags change as one update (AER-4470).

Each function used to write the value, then each flag through its own
setter, and every setter fires the callbacks.  At fail onset the first
callback carried the value computed from the failed input, still flagged
good, and only the next ones carried fail and then 0.0.  At old onset one
callback carried the new value with the previous, good, old flag.

These record every callback tuple on the real database across a fail onset
and an old onset and require each one to be the final state: no consumer
ever sees a value paired with flags it does not have.
"""

import io

import pytest

import fixgw.database as database
import fixgw.plugins.compute as compute

DB = """
entries:
- {key: I1, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
- {key: I2, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
- {key: I3, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
- {key: I4, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
- {key: I5, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
- {key: SEL, type: int, min: 0, max: 1, initial: 0, tol: 0}
- {key: O1, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
- {key: O2, type: float, min: -100000.0, max: 100000.0, initial: 0.0, tol: 2000}
"""

# (factory, inputs, output, {input: good value}, input to fail or stop).
# The values keep every function's arithmetic in range.
CASES = {
    "altp": (compute.altPressure, ["I1", "I2"], "O1", {"I1": 29.5, "I2": 5000.0}, "I1"),
    "altd": (
        compute.altDensity,
        ["I1", "I2", "I3"],
        "O1",
        {"I1": 5000.0, "I2": 5000.0, "I3": 20.0},
        "I3",
    ),
    "average": (compute.averageFunction, ["I1", "I2"], "O1", {"I1": 10.0, "I2": 20.0}, "I2"),
    "sum": (compute.sumFunction, ["I1", "I2"], "O1", {"I1": 10.0, "I2": 20.0}, "I2"),
    "max": (compute.maxFunction, ["I1", "I2"], "O1", {"I1": 10.0, "I2": 20.0}, "I2"),
    "min": (compute.minFunction, ["I1", "I2"], "O1", {"I1": 10.0, "I2": 20.0}, "I2"),
    "span": (compute.spanFunction, ["I1", "I2"], "O1", {"I1": 10.0, "I2": 20.0}, "I2"),
    "wrap360": (compute.wrap360Function, ["I1", "I2"], "O1", {"I1": 350.0, "I2": 20.0}, "I1"),
    "xte": (
        compute.xteFunction,
        ["I1", "I2", "I3", "I4", "I5"],
        "O1",
        {"I1": 35.0, "I2": -97.1, "I3": 36.0, "I4": -97.0, "I5": 0.0},
        "I1",
    ),
    "bearing": (
        compute.bearingFunction,
        ["I1", "I2", "I3", "I4"],
        "O1",
        {"I1": 35.0, "I2": -97.0, "I3": 36.0, "I4": -96.0},
        "I1",
    ),
    "select": (
        compute.selectFunction,
        ["SEL", "I1", "I2"],
        "O1",
        {"SEL": 0, "I1": 120.0, "I2": 240.0},
        "I1",
    ),
    "wind_triangle": (
        compute.windTriangle,
        ["I1", "I2", "I3", "I4"],
        ["O1", "O2"],
        {"I1": 110.0, "I2": 95.0, "I3": 120.0, "I4": 90.0},
        "I3",
    ),
    "wind_components": (
        compute.windComponents,
        ["I1", "I2", "I3"],
        ["O1", "O2"],
        {"I1": 15.0, "I2": 300.0, "I3": 270.0},
        "I1",
    ),
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class Parent:
    def db_get_item(self, key):
        return database.get_raw_item(key)

    def db_read(self, key):
        return database.read(key)


@pytest.fixture
def clock(monkeypatch):
    database.init(io.StringIO(DB))
    database._stop_update_thread()  # the test ticks the clock itself
    clock = Clock()
    monkeypatch.setattr(database, "time", clock)
    monkeypatch.setattr(compute.quorum, "leader", True)
    return clock


def record(into):
    return lambda key, value, udata: into.append(value)


def wire(case):
    factory, inputs, output, good, target = CASES[case]
    outputs = output if isinstance(output, list) else [output]
    f = factory(inputs, output, True)
    for each in inputs:
        database.callback_add("compute", each, f, Parent())
    seen = {o: [] for o in outputs}
    for o in outputs:
        database.callback_add("recorder", o, record(seen[o]), None)
    for key, value in good.items():
        database.write(key, value)
    for o in outputs:
        assert not any(database.read(o)[2:]), (o, database.read(o))
        seen[o].clear()
    return inputs, good, target, seen


def assert_every_callback_is_final(seen):
    for o, tuples in seen.items():
        assert tuples, f"{o} published nothing"
        final = database.read(o)
        for t in tuples:
            assert t == final, f"{o} published {t}, settled at {final}"


@pytest.mark.parametrize("case", sorted(CASES))
def test_fail_onset_publishes_only_the_failed_state(clock, case):
    inputs, good, target, seen = wire(case)
    database.write(target, (good[target], False, False, True))  # (value, annunc, bad, fail)
    assert_every_callback_is_final(seen)
    for o in seen:
        value, _, _, _, fail, _ = database.read(o)
        assert fail and value == 0.0, (o, database.read(o))


# The target stops writing and a sibling keeps ticking; it outlives its tol
# before the 1 s update() pass has flagged it, so the sibling's recompute is
# the one that turns the output old (AER-4450).
@pytest.mark.parametrize("case", sorted(CASES))
def test_old_onset_publishes_only_the_old_state(clock, case):
    inputs, good, target, seen = wire(case)
    sibling = next(k for k in inputs if k != target and k != "SEL")
    if case == "select":
        sibling = "SEL"
    clock.now += 2.5
    database.write(sibling, good[sibling])
    assert database.read(target)[2]
    assert_every_callback_is_final(seen)
    for o in seen:
        assert database.read(o)[2], (o, database.read(o))


# ALT fails, then stops arriving and its fail flag clears while it is old.
# PALT goes from failed straight to old-not-failed through the flags-only
# path, and still holds the 0.0 placeholder from the fail write.  A BARO write
# in between re-publishes the failed state, so PALT's own timestamp is still
# inside its tol when ALT ages out.  No callback on the way may carry that 0.0
# with every flag good (AER-4459).
def test_altp_fail_to_old_never_publishes_the_placeholder_as_good(clock):
    inputs, good, target, seen = wire("altp")
    database.write("I2", (good["I2"], False, False, True))
    assert database.read("O1")[4]
    clock.now += 1.0
    database.write("I1", good["I1"])
    seen["O1"].clear()
    clock.now += 1.5
    database.get_raw_item("I2").fail = False
    assert database.read("I2")[2] and not database.read("I2")[4]
    final = database.read("O1")
    assert final[2] and not final[4], final
    assert seen["O1"], "O1 published nothing"
    for t in seen["O1"]:
        assert any(t[2:5]), f"O1 published {t} with old, bad and fail all clear"


# --- db_item.set itself ---------------------------------------------------


@pytest.fixture
def item(clock):
    o = database.get_raw_item("O1")
    calls = []
    database.callback_add("recorder", "O1", record(calls), None)
    return o, calls


def test_set_writes_value_and_every_flag_with_one_callback(item):
    o, calls = item
    o.set(5.0, old=True, bad=True, fail=True, secfail=True)
    assert calls == [(5.0, False, True, True, True, True)]


def test_set_leaves_a_flag_it_is_not_given(item):
    o, calls = item
    o.set(5.0, bad=True)
    o.set(6.0, fail=True)
    assert calls[-1] == (6.0, False, False, True, True, False)


def test_set_bounds_the_value_and_ends_the_expiry(item, clock):
    o, calls = item
    o.set(1.0)
    clock.now += 3.0
    database.update()
    assert o.value[2]
    o.set(1e9, old=False)
    assert o.value == (100000.0, False, False, False, False, False)
