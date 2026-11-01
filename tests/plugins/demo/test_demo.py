import io

import pytest

import fixgw.database as database
import fixgw.plugin as plugin_base
import fixgw.plugins.demo as demo


class FakeLog:
    def __init__(self):
        self.debugs = []

    def debug(self, message):
        self.debugs.append(message)


class FakeParent:
    def __init__(self):
        self.log = FakeLog()
        self.values = {}
        self.writes = []

    def db_write(self, key, value):
        self.values[key] = value
        self.writes.append((key, value))

    def db_read(self, key):
        return (self.values[key], False, False, False, False, False)


def test_main_thread_initializes_points_and_runs_script_paths(monkeypatch):
    parent = FakeParent()
    thread = demo.MainThread(parent)
    assert parent.values["IAS"] == 113.0
    assert parent.values["LAT"] == 40.000200

    sleeps = 0

    def stop_after_script_progress(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 25:
            thread.getout = True

    monkeypatch.setattr(demo.time, "sleep", stop_after_script_progress)

    thread.run()

    assert thread.running is False
    assert ("MAVMSG", "NO DATA") in parent.writes
    assert any(
        key == "ROLL" and isinstance(value, (int, float)) and value > 0
        for key, value in parent.writes
    )
    assert any(key == "LAT" and value != 40.000200 for key, value in parent.writes)


def test_main_thread_wraps_single_entry_script_and_stop(monkeypatch):
    parent = FakeParent()
    thread = demo.MainThread(parent)
    thread.script = [{"MAVMSG": "NO DATA"}]

    def stop_after_sleep(_seconds):
        thread.getout = True

    monkeypatch.setattr(demo.time, "sleep", stop_after_sleep)
    thread.run()
    thread.stop()

    assert ("MAVMSG", "NO DATA") in parent.writes
    assert thread.running is False
    assert thread.getout is True


def test_plugin_lifecycle_status_and_failure(monkeypatch):
    class DummyThread:
        def __init__(self, _parent):
            self.started = False
            self.stopped = False
            self.alive = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.joined = timeout

    monkeypatch.setattr(demo, "MainThread", DummyThread)
    plugin = demo.Plugin("demo", {}, {})

    plugin.run()
    plugin.stop()

    assert plugin.thread.started is True
    assert plugin.thread.stopped is True
    assert plugin.get_status() == demo.OrderedDict()

    failing = demo.Plugin("demo", {}, {})
    failing.thread.alive = True
    with pytest.raises(plugin_base.PluginFail):
        failing.stop()


# The refresh loop reads each point and writes it back.  A read returns
# (value, annunc, old, bad, fail, secfail), but a tuple write is taken as
# (value, annunc, bad, fail, secfail), so writing the read back moved every
# flag one place: old became bad and bad became fail.  Under the writer-held
# old flag (AER-4450) a compute output that demo also writes (CDI, COURSE in
# the shipped default config) went old, then BAD, then FAILED (AER-4471).
# These run on the real database because the shift is in its tuple setter.
DB = """
entries:
- {key: CDI, type: float, min: -1.0, max: 1.0, initial: 0.0, tol: 2000}
- {key: MAVMSG, type: str, initial: '', tol: 0}
"""


class DbParent:
    def db_write(self, key, value):
        database.write(key, value)

    def db_read(self, key):
        return database.read(key)


def _demo_on_real_db():
    database.init(io.StringIO(DB))
    database._stop_update_thread()
    thread = demo.MainThread(FakeParent())
    thread.parent = DbParent()
    thread.keylist = {"CDI": 0.0}
    thread.script = [{"MAVMSG": "x"}]
    return thread


def _run_one_pass(monkeypatch, thread):
    def stop_after_sleep(_seconds):
        thread.getout = True

    monkeypatch.setattr(demo.time, "sleep", stop_after_sleep)
    thread.run()


def test_refresh_does_not_turn_another_writers_old_into_bad(monkeypatch):
    thread = _demo_on_real_db()
    database.write("CDI", 0.0)
    database.get_raw_item("CDI").old = True  # compute: input stale
    _run_one_pass(monkeypatch, thread)
    _, _, old, bad, fail, _ = database.read("CDI")
    assert (bad, fail) == (False, False)


def test_refresh_does_not_turn_bad_into_fail(monkeypatch):
    thread = _demo_on_real_db()
    database.write("CDI", 0.0)
    database.get_raw_item("CDI").bad = True
    _run_one_pass(monkeypatch, thread)
    _, _, old, bad, fail, _ = database.read("CDI")
    assert (bad, fail) == (True, False)


# One writer per key (AER-4475): demo runs alongside compute in the shipped
# default, so it must not write any key compute.yaml outputs. It used to write
# COURSE, CDI and GSI, which compute owns through its NAVSRC select.
def _compute_outputs():
    import yaml

    with open("src/fixgw/config/connections/compute.yaml") as f:
        functions = yaml.safe_load(f)["compute"]["functions"]
    return {f["output"] for f in functions if "output" in f}


def test_demo_writes_no_compute_output():
    thread = demo.MainThread(FakeParent())
    written = set(thread.keylist)
    for step in thread.script:
        written.update(k for k in step if k != "MAVMSG")
    outputs = _compute_outputs()
    assert {"COURSE", "CDI", "GSI"} <= outputs
    assert written & outputs == set()
