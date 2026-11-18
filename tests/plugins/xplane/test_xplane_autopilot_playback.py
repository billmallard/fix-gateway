"""Playback of a real X-Plane session through the autopilot adapter (AFC2).

`data/afc2_c172_bf3a33402db4_raw.jsonl` is the adapter's own raw log
(`autopilot: raw_log:`) from sim-bench session bf3a33402db4: X-Plane 12.4.3,
the Laminar C172 G1000 (GFC-700), en route on V27 near KSBA, 2026-11-18. One
JSON line per change of any of the adapter's datarefs. In it the driver
couples the autopilot (ROL, HDG, then GPS), and the bench sequencer presses
HDG, NAV on a silent radio (refused, nothing sent), VS to ALTSEL, an IAS
climb, NAV, LVL, NAV again and DISC. The disconnect trips the driver's servo
guard, which pauses the sim and ends the log.

`data/afc2_c172_bf3a33402db4_bus.json` is what the live gateway published
during the same session, from the bench bus log: each key's value changes,
fresh flag only. Replaying the raw log must publish the same changes in the
same order, at the same times. The test needs no X-Plane.
"""

import json
from pathlib import Path

import pytest

import fixgw.plugin as plugin_base
from fixgw.plugins.xplane import autopilot as ap

DATA = Path(__file__).parent / "data"
RAW = DATA / "afc2_c172_bf3a33402db4_raw.jsonl"
BUS = DATA / "afc2_c172_bf3a33402db4_bus.json"
INDEX = {name: ap.AP_RREF_BASE + i for i, (name, _d) in enumerate(ap.DATAREFS)}
KEYS = ("APENG", "FDENG", "APLAT", "APLATARM", "APVERT", "APVERTARM", "APDISC",
        "APCAPS")


def bus_text(key, value):
    return str(value) if not isinstance(value, str) else value


@pytest.fixture
def replay(database):
    rows = [json.loads(line) for line in RAW.read_text().splitlines()]
    t0 = rows[0]["t"]
    clock = [1000.0]
    parent = plugin_base.PluginBase("xplane", {}, {})
    adapter = ap.Autopilot(parent, lambda packet: None, clock=lambda: clock[0])
    seen = {k: [] for k in KEYS}

    def record(key, value, udata):
        if value[2]:                        # old: not published by the source
            return
        s = seen[key]
        text = bus_text(key, value[0])
        if not s or s[-1][1] != text:
            s.append((clock[0] - 1000.0, text))

    for key in KEYS:                        # the adapter's writes only
        database.callback_add("t", key, record, None)
    state = {}
    for row in rows:
        clock[0] = 1000.0 + row["t"] - t0
        state.update(row["refs"])
        for name, value in state.items():   # X-Plane sends every dataref
            adapter.feed(INDEX[name], value)
        adapter.publish()
    return seen, json.loads(BUS.read_text())["transitions"]


def test_the_raw_log_covers_every_dataref():
    first = json.loads(RAW.read_text().splitlines()[0])
    assert set(first["refs"]) == {name for name, _d in ap.DATAREFS}


@pytest.mark.parametrize("key", KEYS)
def test_replay_publishes_what_the_bench_published(replay, key):
    seen, bus = replay
    assert [v for _t, v in seen[key]] == [v for _t, v in bus[key]]


@pytest.mark.parametrize("key", KEYS)
def test_replay_changes_land_when_they_did_on_the_bench(replay, key):
    seen, bus = replay
    for (t_replay, v), (t_bench, _v) in zip(seen[key][1:], bus[key][1:]):
        assert abs(t_replay - t_bench) < 0.3, (key, v, t_replay, t_bench)


def test_the_session_flew_every_mode_in_the_done_when(replay):
    """HDG to NAV capture, VS to ALT, an IAS climb, LVL and a disconnect."""
    seen, _bus = replay
    lat = [v for _t, v in seen["APLAT"]]
    vert = [v for _t, v in seen["APVERT"]]
    assert lat == ["", "ROL", "HDG", "GPS", "HDG", "GPS", "ROL", "GPS"]
    assert vert == ["", "PIT", "ALT", "VS", "ALT", "IAS", "ALT", "PIT", "ALT"]
    assert [v for _t, v in seen["APENG"]] == ["False", "True", "False"]
    assert [v for _t, v in seen["APDISC"]] == ["False", "True"]


def test_fd_commands_are_absolute_attitude():
    """In steady ALT hold the commanded pitch sits near the aircraft's own
    pitch (bench state logs: FDPITCH 2.84 against PITCH 3.10, 6.40 against
    6.59), so X-Plane's flight_director_pitch_deg is the attitude, not an
    error. Here: it ranges over the climb attitudes, not around zero."""
    rows = [json.loads(line) for line in RAW.read_text().splitlines()]
    pitches = [r["refs"]["fd_pitch"] for r in rows if "fd_pitch" in r["refs"]]
    assert max(pitches) > 5.0
