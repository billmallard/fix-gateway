"""A compute output is old whenever any of its inputs is old (AER-4450).

The repro from the bench: LAT is written once, WPLAT/WPLON keep updating
every second (the flightplan plugin rewrites them every cycle), and the
bearing output read good from a frozen position for as long as WPLAT ticked.

These run on the real database, not a fake item, because the flag was lost
in the database: the value getter worked old out from the output's own age
and dropped the old flag compute had just set on it.  The clock is driven by
hand and database.update() is called as the 1 s UpdateThread would call it.
"""

import io

import pytest

import fixgw.database as database
import fixgw.plugins.compute as compute

DB = """
entries:
- {key: LAT, type: float, min: -90.0, max: 90.0, initial: 0.0, tol: 2000}
- {key: LONG, type: float, min: -180.0, max: 180.0, initial: 0.0, tol: 2000}
- {key: WPLAT, type: float, min: -90.0, max: 90.0, initial: 0.0, tol: 50000}
- {key: WPLON, type: float, min: -180.0, max: 180.0, initial: 0.0, tol: 50000}
- {key: MAGVAR, type: float, min: -180.0, max: 180.0, initial: 0.0, tol: 60000}
- {key: TRACK, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
- {key: BRG1SRC, type: int, min: 0, max: 2, initial: 0, tol: 0}
- {key: VOR1BRG, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
- {key: VOR2BRG, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
- {key: GPSBRGT, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
- {key: GPSBRG, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
- {key: TRACKM, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
- {key: BRG1, type: float, min: 0.0, max: 360.0, initial: 0.0, tol: 2000}
"""

# The shipped wiring for these outputs (config/connections/compute.yaml).
FUNCTIONS = [
    (compute.bearingFunction, ["LAT", "LONG", "WPLAT", "WPLON"], "GPSBRGT"),
    (compute.wrap360Function, ["GPSBRGT", "MAGVAR"], "GPSBRG"),
    (compute.wrap360Function, ["TRACK", "MAGVAR"], "TRACKM"),
    (compute.selectFunction, ["BRG1SRC", "VOR1BRG", "VOR2BRG", "GPSBRG"], "BRG1"),
]


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
def bus(monkeypatch):
    database.init(io.StringIO(DB))
    database._stop_update_thread()  # the test ticks update() itself
    clock = Clock()
    monkeypatch.setattr(database, "time", clock)
    monkeypatch.setattr(compute.quorum, "leader", True)
    parent = Parent()
    for factory, inputs, output in FUNCTIONS:
        f = factory(inputs, output, True)
        for each in inputs:
            database.callback_add("compute", each, f, parent)
    return clock


def old(key):
    return database.read(key)[2]


def run_seconds(clock, seconds, tick, update=True):
    """Advance the clock a second at a time, writing the ticking sibling
    inputs each second, with or without the 1 s database.update() pass."""
    for _ in range(seconds):
        clock.now += 1.0
        if update:
            database.update()
        tick()


def start_bearing(clock):
    database.write("BRG1SRC", 2)
    database.write("MAGVAR", 3.0)
    database.write("LAT", 35.0)
    database.write("LONG", -97.0)
    database.write("WPLAT", 36.0)
    database.write("WPLON", -97.0)


def tick_waypoint():
    database.write("WPLAT", 36.0)
    database.write("WPLON", -97.0)
    database.write("MAGVAR", 3.0)


def test_bearing_chain_is_good_while_position_is_fresh(bus):
    start_bearing(bus)
    for _ in range(5):
        bus.now += 1.0
        database.update()
        database.write("LAT", 35.0)
        database.write("LONG", -97.0)
        tick_waypoint()
    for key in ("GPSBRGT", "GPSBRG", "BRG1"):
        assert not old(key), key


# The bench repro: LAT stale, WPLAT ticking. bearing -> GPSBRGT,
# wrap360 -> GPSBRG, select -> BRG1 (the HSI bearing pointer).
@pytest.mark.parametrize("key", ["GPSBRGT", "GPSBRG", "BRG1"])
def test_output_goes_old_when_position_stops_and_waypoint_keeps_ticking(bus, key):
    start_bearing(bus)
    run_seconds(bus, 6, tick_waypoint)
    assert old("LAT")
    assert old(key)


# Between LAT outliving its tol and the next update() pass, a recompute
# triggered by WPLAT still has LAT's last-callback tuple, which was fresh.
@pytest.mark.parametrize("key", ["GPSBRGT", "GPSBRG", "BRG1"])
def test_output_is_old_before_the_update_pass_has_flagged_the_input(bus, key):
    start_bearing(bus)
    run_seconds(bus, 3, tick_waypoint, update=False)
    assert old("LAT")
    assert old(key)


def test_wrap360_output_goes_old_when_one_input_stops(bus):
    database.write("MAGVAR", 3.0)
    database.write("TRACK", 90.0)
    run_seconds(bus, 6, lambda: database.write("MAGVAR", 3.0))
    assert old("TRACK")
    assert old("TRACKM")


def test_select_output_goes_old_when_its_source_stops_and_the_selector_ticks(bus):
    database.write("BRG1SRC", 0)
    database.write("VOR1BRG", 120.0)
    run_seconds(bus, 6, lambda: database.write("BRG1SRC", 0))
    assert old("VOR1BRG")
    assert old("BRG1")


def test_outputs_recover_when_the_stale_input_resumes(bus):
    start_bearing(bus)
    run_seconds(bus, 6, tick_waypoint)
    assert old("BRG1")
    database.write("LAT", 35.0)
    database.write("LONG", -97.0)
    for key in ("LAT", "GPSBRGT", "GPSBRG", "BRG1"):
        assert not old(key), key


# Reading the output must not clear the flag: the netfix server, the
# recorder and every downstream compute read it.
def test_reading_the_output_does_not_clear_its_old_flag(bus):
    start_bearing(bus)
    run_seconds(bus, 6, tick_waypoint)
    for _ in range(3):
        database.read("GPSBRGT")
    assert old("GPSBRGT")
    assert database.get_raw_item("GPSBRGT").old
