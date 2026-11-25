import json
import math

import pytest

import fixgw.plugin as plugin_base
import fixgw.plugins.gpsd as gpsd_plugin


class FakeLog:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)


class FakeItem:
    def __init__(self, parent, key):
        self.parent, self.key = parent, key

    def set(self, value, bad=None):
        self.parent.writes.append((self.key, value))
        self.parent.bad[self.key] = bad


class FakeParent:
    def __init__(self):
        self.log = FakeLog()
        self.writes = []
        self.bad = {}

    def db_write(self, key, value):
        self.writes.append((key, value))

    def db_get_item(self, key):
        return FakeItem(self, key)


def test_main_thread_logs_connect_failure(monkeypatch):
    parent = FakeParent()
    thread = gpsd_plugin.MainThread(parent)
    monkeypatch.setattr(gpsd_plugin.gpsd2, "connect", lambda: (_ for _ in ()).throw(RuntimeError()))

    thread.run()

    assert parent.log.errors == ["Can't connect to gpsd"]


class FakeStream:
    """gpsd's side of the socket: answers each ?POLL with the next report.

    The plugin and py-gpsd2 both talk to gpsd through gpsd2.gpsd_stream, so
    driving the stream exercises the library's parsing as well as ours.
    """

    def __init__(self, thread, reports):
        self.thread = thread
        self.reports = list(reports)

    def write(self, text):
        pass

    def flush(self):
        pass

    def readline(self):
        report = self.reports.pop(0)
        if not self.reports:
            self.thread.getout = True
        return json.dumps(report) + "\n"


def poll(tpv, satellites=4, active=1):
    return {
        "class": "POLL",
        "active": active,
        "tpv": [dict(tpv, **{"class": "TPV"})],
        "sky": [{"class": "SKY", "satellites": [{"used": True}] * satellites}],
    }


def run_thread(monkeypatch, reports):
    parent = FakeParent()
    thread = gpsd_plugin.MainThread(parent)
    stream = FakeStream(thread, reports)
    monkeypatch.setattr(gpsd_plugin.gpsd2, "connect", lambda: None)
    monkeypatch.setattr(gpsd_plugin.gpsd2, "gpsd_stream", stream, raising=False)
    monkeypatch.setattr(gpsd_plugin.time, "sleep", lambda _seconds: None)
    thread.run()
    return parent


FULL_3D = {
    "mode": 3,
    "lat": 41.0,
    "lon": -84.0,
    "track": 222.0,
    "speed": 60.0,
    "alt": 1000.0,
    "epx": 1.0,
    "epy": 1.5,
    "epv": 2.0,
    "eps": 3.0,
}


def test_main_thread_writes_2d_and_3d_fix_data_and_warning_path(monkeypatch):
    reports = [
        poll({"mode": 1}, active=0),
        poll(
            {
                "mode": 2,
                "lat": 40.0,
                "lon": -83.0,
                "track": 123.4,
                "speed": 50.0,
                "epx": 2.0,
                "epy": 2.0,
            },
            satellites=9,
        ),
        poll(FULL_3D, satellites=10),
    ]

    parent = run_thread(monkeypatch, reports)

    assert parent.log.warnings == ["gpsd warning: GPS not active"]
    assert ("GPS_SATS_VISIBLE", 9) in parent.writes
    assert ("GPS_FIX_TYPE", 2) in parent.writes
    assert ("LAT", 40.0) in parent.writes
    assert ("LONG", -83.0) in parent.writes
    assert ("TRACK", 123.4) in parent.writes
    assert ("GS", 50.0 * 1.94384) in parent.writes
    assert ("GPS_ACCURACY_HORIZ", math.hypot(2.0, 2.0) * 3.28084) in parent.writes
    assert ("GPS_SATS_VISIBLE", 10) in parent.writes
    assert ("GPS_FIX_TYPE", 3) in parent.writes
    assert ("TALT", 1000.0 * 3.28084) in parent.writes
    assert ("GPS_ACCURACY_HORIZ", math.hypot(1.0, 1.5) * 3.28084) in parent.writes
    assert ("GPS_ACCURACY_VERTICAL", 2.0 * 3.28084) in parent.writes
    assert ("GPS_ACCURACY_SPEED", 3.0 * 1.94384) in parent.writes


def test_main_thread_writes_satellite_status_without_position_for_no_fix(monkeypatch):
    parent = run_thread(monkeypatch, [poll({"mode": 1})])

    assert parent.writes == [("GPS_SATS_VISIBLE", 4), ("GPS_FIX_TYPE", 1)]


def test_main_thread_sleeps_between_polls(monkeypatch):
    # AER-6406: the sleep sat after the loop, so the thread polled gpsd as
    # fast as gpsd answered. Every poll must be followed by a pause.
    events = []
    parent = FakeParent()
    parent.db_write = lambda key, value: events.append("write")

    class CountingStream(FakeStream):
        def readline(self):
            events.append("poll")
            return super().readline()

    thread = gpsd_plugin.MainThread(parent)
    stream = CountingStream(thread, [poll(FULL_3D)] * 3)
    monkeypatch.setattr(gpsd_plugin.gpsd2, "connect", lambda: None)
    monkeypatch.setattr(gpsd_plugin.gpsd2, "gpsd_stream", stream, raising=False)
    monkeypatch.setattr(
        gpsd_plugin.time, "sleep", lambda seconds: events.append(("sleep", seconds))
    )

    thread.run()

    polls = [i for i, e in enumerate(events) if e == "poll"]
    sleeps = [i for i, e in enumerate(events) if isinstance(e, tuple)]
    assert len(polls) == 3
    assert len(sleeps) == 3
    # a pause between each pair of polls, not only after the last one
    assert all(polls[n] < sleeps[n] < polls[n + 1] for n in range(2))
    assert all(events[i][1] > 0 for i in sleeps)


@pytest.mark.parametrize(
    "missing, key",
    [
        ("epx", "GPS_ACCURACY_HORIZ"),
        ("epy", "GPS_ACCURACY_HORIZ"),
        ("alt", "TALT"),
        ("epv", "GPS_ACCURACY_VERTICAL"),
        ("eps", "GPS_ACCURACY_SPEED"),
        ("lat", "LAT"),
        ("lon", "LONG"),
        ("track", "TRACK"),
        ("speed", "GS"),
    ],
)
def test_absent_tpv_field_writes_nothing(monkeypatch, missing, key):
    # AER-6406: py-gpsd2 0.1.0 turns an absent TPV field into 0, and 0 ft of
    # GPS_ACCURACY_HORIZ is a perfect fix to the flight plan engine and the
    # best NACp to gdl90_out. An absent field writes nothing, so the key goes
    # old by its tol, the same silence every other source gives.
    tpv = {k: v for k, v in FULL_3D.items() if k != missing}

    parent = run_thread(monkeypatch, [poll(tpv)])

    written = dict(parent.writes)
    assert key not in written
    # the rest of the report is still published
    assert written["GPS_FIX_TYPE"] == 3
    assert len(written) == 9


def horizontal_accuracy_m(parent):
    return [v for k, v in parent.writes if k == "GPS_ACCURACY_HORIZ"][-1] / 3.28084


def test_horizontal_accuracy_covers_the_larger_axis(monkeypatch):
    # ACC1 (AER-6592): the key is a horizontal radius (AER-6461). epx is the
    # longitude error only, and the plugin wrote it alone, so a fix 30 m
    # uncertain in latitude read as 10 m to gdl90_out and the HAL check.
    tpv = dict(FULL_3D, epx=10.0, epy=30.0)

    parent = run_thread(monkeypatch, [poll(tpv)])

    assert horizontal_accuracy_m(parent) >= 30.0
    assert horizontal_accuracy_m(parent) == pytest.approx(math.hypot(10.0, 30.0))


def test_horizontal_accuracy_takes_gpsd_eph_when_present(monkeypatch):
    # eph is gpsd's own 2-D figure, from the receiver or from hdop.
    tpv = dict(FULL_3D, epx=10.0, epy=30.0, eph=40.0)

    parent = run_thread(monkeypatch, [poll(tpv)])

    assert horizontal_accuracy_m(parent) == pytest.approx(40.0)


def test_eph_alone_writes_horizontal_accuracy(monkeypatch):
    tpv = {k: v for k, v in FULL_3D.items() if k not in ("epx", "epy")}
    tpv["eph"] = 12.0

    parent = run_thread(monkeypatch, [poll(tpv)])

    assert horizontal_accuracy_m(parent) == pytest.approx(12.0)


def test_talt_is_bad_in_a_2d_fix_and_good_in_a_3d_fix(monkeypatch):
    # In a 2-D fix gpsd's height is held or invented: TALT goes out bad, so
    # the SVS camera does not fly on it (Elon's review on AER-6458).
    parent = run_thread(monkeypatch, [poll(dict(FULL_3D, mode=2, altMSL=300.0))])
    assert dict(parent.writes)["TALT"] == pytest.approx(300.0 * 3.28084)
    assert parent.bad["TALT"] is True
    parent = run_thread(monkeypatch, [poll(dict(FULL_3D, altMSL=300.0))])
    assert parent.bad["TALT"] is False


def test_talt_prefers_altmsl_over_the_deprecated_alt(monkeypatch):
    # gpsd 3.20+ sends altMSL and calls alt deprecated; TALT is MSL, so it
    # takes altMSL when gpsd sends one (GALT1/TALTSRC1, AER-6458).
    tpv = dict(FULL_3D, alt=1000.0, altMSL=970.0)
    parent = run_thread(monkeypatch, [poll(tpv)])
    written = dict(parent.writes)
    assert written["TALT"] == pytest.approx(970.0 * 3.28084)
    assert "GPS_ELLIPSOID_ALT" not in written


def test_present_zero_is_still_written(monkeypatch):
    # A real 0 (stationary, on the ellipsoid) is a value, not an absence.
    tpv = dict(FULL_3D, speed=0.0, alt=0.0, track=0.0)

    parent = run_thread(monkeypatch, [poll(tpv)])

    assert ("GS", 0.0) in parent.writes
    assert ("TALT", 0.0) in parent.writes
    assert ("TRACK", 0.0) in parent.writes


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

    monkeypatch.setattr(gpsd_plugin, "MainThread", DummyThread)
    plugin = gpsd_plugin.Plugin("gpsd", {}, {})

    plugin.run()
    plugin.stop()

    assert plugin.thread.started is True
    assert plugin.thread.stopped is True
    assert plugin.get_status() == gpsd_plugin.OrderedDict()

    failing = gpsd_plugin.Plugin("gpsd", {}, {})
    failing.thread.alive = True
    with pytest.raises(plugin_base.PluginFail):
        failing.stop()
