"""QA1 (AER-4211): QA's own profiles -- hold entries from each AIM 5-3-8
sector in both turn directions, a missed approach whose coded turn is the
long way round, and a GPS loss of fix mid-leg and across a fly-by fix --
flown headless through the engine, plus the check-language pieces they need
(``gps_loss_s``, ``unless_flagged``, ``flagged``, ``tolerate_s``,
``around_keys``).

The checks, tolerances and expected values in the qa1 profiles are QA's.
The hold-entry expectation below is computed here from AIM FIG 5-3-8 and the
CIFP's coded inbound course, not from either navigator.
"""

from pathlib import Path

import pytest
import yaml

from fixgw.sim import checks, generator
from fixgw.sim.cifp import Cifp
from fixgw.sim.profile import ProfileError, load_profile

from . import engine_replay, plugin_replay, profile_gate

PROFILES = Path(__file__).resolve().parents[2] / "src/fixgw/sim/profiles"
QA1 = sorted(PROFILES.glob("qa1-*.yaml"))

# Open findings: the profile is right and the box disagrees with it. strict,
# so a fix turns the test red until the mark is removed.
OPEN = {
    "qa1-hold-teardrop-left-kded-r30": "AER-4211: FPLCRS 1.6 deg off the reference on the BARBS hold's "
                                       "outbound-end turn (5 nm legs); cross-track agrees to 0.014 nm",
    # F4 (AER-4211/AER-4223), fix-gateway#58: a dead GPS link stays unflagged ~25 s
    "qa1-gps-link-loss-mid-leg-ksmx-r30": "AER-4223 F4: guidance unflagged up to 25.4 s after LAT/LONG stop "
                                          "with the fix type left at 3D",
    "qa1-gps-position-loss-mid-leg-ksmx-r30": "AER-4223 F4: guidance unflagged up to 25.4 s after LAT/LONG "
                                              "stop while the status keeps saying 3D",
    "qa1-gps-status-loss-mid-leg-ksmx-r30": "AER-4223 F4: guidance never flagged while GPS_FIX_TYPE and "
                                            "GPS_ACCURACY_HORIZ are silent and position keeps coming",
    # F5 (AER-4223), also closed by fix-gateway#58: the first cycle after the fix returns runs on the LAT
    # write with the LONG from before the outage, unflagged
    "qa1-gps-loss-mid-leg-ksmx-r30": "AER-4223 F5: one unflagged cycle on new LAT + pre-outage LONG after "
                                     "the fix returns (FPLXTK 0.199 nm on a 0.3 nm final)",
}


@pytest.fixture(scope="module")
def flown():
    cache = {}

    def fly(path):
        if path not in cache:
            prof = load_profile(str(path))
            run = generator.generate(prof)
            cache[path] = (prof, run, profile_gate.replay_for(prof).replay(run.rows))
        return cache[path]
    return fly


@pytest.mark.parametrize("path", QA1, ids=[p.stem for p in QA1])
def test_every_qa1_profile_check_passes(flown, path):
    prof, _, rows = flown(path)
    log = checks.Log.from_rows(rows, checks.keys_needed(prof.checks))
    failed = [f"{r.label}: {r.detail}" for r in checks.evaluate(prof.checks, log) if not r.passed]
    if path.stem in OPEN:
        assert failed, f"{path.stem} passes now -- close the finding and drop it from OPEN"
        pytest.xfail(OPEN[path.stem])
    assert failed == []


# -- hold entry: the reference flies the AIM 5-3-8 entry ---------------------

def aim_entry(inbound_true, turn, arrival_true):
    """AIM FIG 5-3-8, right-hand pattern with inbound 360: direct for arrival
    headings 290 -> 360 -> 110, teardrop 110-180, parallel 180-290; a
    left-hand pattern is its mirror image."""
    a = (arrival_true - inbound_true) % 360.0
    if turn == "L":
        a = (360.0 - a) % 360.0
    if 110.0 <= a < 180.0:
        return "teardrop"
    if 180.0 <= a < 290.0:
        return "parallel"
    return "direct"


def _coded_hold(prof):
    cifp = Cifp(prof.cifp_file)
    hm = [r for r in cifp.approach_legs(prof.procedure["airport"], prof.procedure["approach"])
          if r.path_term == "HM"][-1]
    return hm.course_mag, hm.turn


HOLDS = [p for p in QA1 if p.stem.startswith("qa1-hold-")]


@pytest.mark.parametrize("path", HOLDS, ids=[p.stem for p in HOLDS])
def test_the_hold_entry_is_the_aim_entry_for_the_arrival(flown, path):
    prof, run, _ = flown(path)
    named = path.stem.split("-")[2]
    inbound_mag, turn = _coded_hold(prof)
    inbound_true = (inbound_mag - prof.magvar_deg) % 360.0
    k = next(i for i, (_, r) in enumerate(run.rows) if r["REFPT"] == "HM")
    arrival_true = run.rows[k - 1][1]["TRACK"]
    assert aim_entry(inbound_true, turn, arrival_true) == named
    # 20 s into the entry: a teardrop or parallel entry is a straight leg on
    # its AIM heading (magnetic, as REFCRS is); a direct entry is turning.
    outbound_mag = (inbound_mag + 180.0) % 360.0
    expect = {"parallel": outbound_mag,
              "teardrop": outbound_mag + (-30.0 if turn == "R" else 30.0)}
    t0 = run.rows[k][0]
    crs = next(r["REFCRS"] for t, r in run.rows[k:] if t >= t0 + 20.0)
    if named == "direct":
        assert all(abs(checks._wrap180(crs - c)) > 10.0 for c in (outbound_mag, outbound_mag - 30, outbound_mag + 30))
    else:
        assert abs(checks._wrap180(crs - expect[named])) < 1.0


# -- the missed approach's coded turn ------------------------------------------

def test_kssf_codes_a_right_turn_the_long_way_round(flown):
    # The scenario's premise, from the CIFP: the DF to GANLE is coded R while
    # GANLE lies to the left of the climb-out. And tier SYN's blind spot,
    # stated as a test: the generator turns the short way (left), so nothing
    # in this tier exercises the coded direction.
    prof, run, _ = flown(PROFILES / "qa1-missed-coded-turn-kssf-r28.yaml")
    df = next(r for r in Cifp(prof.cifp_file).approach_legs("KSSF", "R28") if r.path_term == "DF")
    assert (df.fix, df.turn) == ("GANLE", "R")
    k = next(i for i, (_, r) in enumerate(run.rows) if r["REFWPT"] == "GANLE")
    rolls = [r["ROLL"] for _, r in run.rows[k + 5:k + 40]]
    assert max(rolls) < -10.0


# -- gps_loss_s: what the generator publishes ----------------------------------

def test_gps_loss_publishes_what_gpsd_publishes_without_a_fix():
    run = generator.generate(str(PROFILES / "qa1-gps-loss-mid-leg-ksmx-r30.yaml"))
    t_on = next(e["t"] for e in run.timeline if e.get("id") == "loss_terminal" and e["what"] == "event")
    t_off = next(e["t"] for e in run.timeline if e.get("id") == "loss_terminal" and e["what"] == "done")
    assert t_off - t_on == pytest.approx(30.0, abs=0.21)
    during = [r for t, r in run.rows if t_on <= t < t_off]
    assert during and all(r["GPS_FIX_TYPE"] == 1 for r in during)
    for key in ("LAT", "LONG", "TRACK", "GS", "GPS_ACCURACY_HORIZ"):
        assert not any(key in r for r in during), key
    assert all(k in r for r in during for k in ("ALT", "HEAD", "REFCRS", "REFXTK"))
    after = next(r for t, r in run.rows if t >= t_off)
    assert after["GPS_FIX_TYPE"] == 3 and "LAT" in after


def test_the_engine_flags_its_guidance_without_a_fix_and_clears_it_after():
    run = generator.generate(str(PROFILES / "qa1-gps-loss-mid-leg-ksmx-r30.yaml"))
    rows = engine_replay.replay(run.rows)
    # the engine alone, updated on every row, so the flag follows the fix row for row
    flagged = [t for t, r in rows if isinstance(r["FPLCDI"], list)]
    no_fix = [t for t, r in run.rows if r["GPS_FIX_TYPE"] < 3]
    assert flagged == no_fix and len(no_fix) > 200


# -- gps_link_loss_s / gps_status_loss_s: what the generator publishes (AER-4223)

def _window(run, eid):
    t_on = next(e["t"] for e in run.timeline if e.get("id") == eid and e["what"] == "event")
    t_off = next(e["t"] for e in run.timeline if e.get("id") == eid and e["what"] == "done")
    return t_on, t_off, [r for t, r in run.rows if t_on <= t < t_off]


def test_a_dead_link_publishes_no_gps_key_at_all_and_leaves_the_fix_type_at_3d():
    run = generator.generate(str(PROFILES / "qa1-gps-link-loss-mid-leg-ksmx-r30.yaml"))
    t_on, t_off, during = _window(run, "link_terminal")
    assert t_off - t_on == pytest.approx(30.0, abs=0.21)
    for key in ("LAT", "LONG", "GS", "TRACK", "GPS_ACCURACY_HORIZ", "GPS_FIX_TYPE"):
        assert during and not any(key in r for r in during), key
    assert all(k in r for r in during for k in ("ALT", "HEAD", "REFCRS", "REFXTK"))
    # the last fix type written before the loss is 3, and so is the first after it
    before = [r["GPS_FIX_TYPE"] for t, r in run.rows if t < t_on]
    after = next(r for t, r in run.rows if t >= t_off)
    assert before[-1] == 3 and after["GPS_FIX_TYPE"] == 3 and "LAT" in after


def test_a_status_loss_stops_the_fix_type_and_accuracy_while_position_keeps_coming():
    run = generator.generate(str(PROFILES / "qa1-gps-status-loss-mid-leg-ksmx-r30.yaml"))
    t_on, t_off, during = _window(run, "status_terminal")
    assert t_off - t_on == pytest.approx(30.0, abs=0.21)
    assert during and not any("GPS_FIX_TYPE" in r or "GPS_ACCURACY_HORIZ" in r for r in during)
    assert all(k in r for r in during for k in ("LAT", "LONG", "GS", "TRACK"))


def test_a_position_loss_stops_lat_long_while_the_status_keeps_saying_3d():
    run = generator.generate(str(PROFILES / "qa1-gps-position-loss-mid-leg-ksmx-r30.yaml"))
    t_on, t_off, during = _window(run, "position_terminal")
    assert t_off - t_on == pytest.approx(30.0, abs=0.21)
    assert during and not any("LAT" in r or "LONG" in r for r in during)
    assert all(r["GPS_FIX_TYPE"] == 3 and "GPS_ACCURACY_HORIZ" in r for r in during)


def test_outside_a_gps_event_every_sensor_key_is_written_on_every_row():
    run = generator.generate(str(PROFILES / "qa1-gps-link-loss-mid-leg-ksmx-r30.yaml"))
    t_on, _, _ = _window(run, "link_terminal")
    assert all(set(generator.SENSOR_KEYS) <= set(r) for t, r in run.rows if t < t_on)


def test_the_gate_flies_a_profile_that_takes_a_gps_input_away_through_the_plugin():
    via = {p.stem: profile_gate.replay_for(load_profile(str(p))) for p in QA1}
    gps = {s for s in via if "gps" in s}
    assert gps == {"qa1-gps-loss-mid-leg-ksmx-r30", "qa1-gps-loss-across-fix-ksmx-r30",
                   "qa1-gps-link-loss-mid-leg-ksmx-r30", "qa1-gps-status-loss-mid-leg-ksmx-r30",
                   "qa1-gps-position-loss-mid-leg-ksmx-r30"}
    assert all(via[s] is plugin_replay for s in gps)
    assert all(via[s] is engine_replay for s in set(via) - gps)


# -- the check-language pieces, on hand-made logs ------------------------------

def _log(rows, keys):
    return checks.Log.from_rows([(i * 0.2, r) for i, r in enumerate(rows)], keys)


F = [1.0, 0, 0, 0, 1, 0]      # a value written failed


def test_unless_flagged_skips_only_rows_whose_engine_key_is_flagged():
    c = {"key_pair": ["A", "B"], "tol": 0.1, "unless_flagged": True, "after_s": 0}
    ok = _log([{"A": 0.0, "B": 0.0}, {"A": F, "B": 0.0}, {"A": 0.0, "B": 0.0}], {"A", "B"})
    r = checks.evaluate([c], ok)[0]
    assert r.passed and r.compared == 2 and r.flagged == 1
    bad = _log([{"A": 0.0, "B": 0.0}, {"A": 1.0, "B": 0.0}], {"A", "B"})
    assert not checks.evaluate([c], bad)[0].passed
    assert not checks.evaluate([dict(c, unless_flagged=False)], ok)[0].passed


def test_tolerate_s_forgives_one_row_and_not_two():
    c = {"key_pair": ["A", "B"], "tol": 0.1, "tolerate_s": 0.1, "after_s": 0}
    one = _log([{"A": 0, "B": 0}, {"A": 9, "B": 0}, {"A": 0, "B": 0}], {"A", "B"})
    two = _log([{"A": 0, "B": 0}, {"A": 9, "B": 0}, {"A": 9, "B": 0}, {"A": 0, "B": 0}], {"A", "B"})
    r1, r2 = checks.evaluate([c], one)[0], checks.evaluate([c], two)[0]
    assert r1.passed and r1.transients == 1
    assert not r2.passed and r2.failed == 2


def test_the_largest_error_is_over_the_rows_that_count_not_a_tolerated_transient():
    c = {"key_pair": ["A", "B"], "tol": 0.1, "tolerate_s": 0.1, "after_s": 0}
    log = _log([{"A": 0, "B": 0}, {"A": 150, "B": 0}, {"A": 0, "B": 0},
                {"A": 0.5, "B": 0}, {"A": 0.5, "B": 0}, {"A": 0, "B": 0}], {"A", "B"})
    r = checks.evaluate([c], log)[0]
    assert r.failed == 2 and r.transients == 1
    assert r.max_err == pytest.approx(0.5) and r.t_max == pytest.approx(0.6)
    assert "largest 150.0000 at t=0.2s" in r.detail


def _fix_log(fix, flags):
    return _log([{"GPS_FIX_TYPE": f, "X": (F if g else 0.0)} for f, g in zip(fix, flags)], {"GPS_FIX_TYPE", "X"})


FLAGGED = {"flagged": ["X"], "while_no_fix": True, "within_s": 0.2, "clear_within_s": 0.2, "after_s": 0}


def test_flagged_passes_a_flag_that_follows_the_fix_within_the_bounds():
    log = _fix_log([3, 3, 1, 1, 1, 3, 3, 3], [0, 0, 0, 1, 1, 1, 0, 0])
    assert checks.evaluate([FLAGGED], log)[0].passed


def test_flagged_fails_a_late_flag_a_sticky_flag_and_a_log_with_no_loss():
    late = _fix_log([3, 1, 1, 1, 3, 3], [0, 0, 0, 1, 0, 0])
    sticky = _fix_log([3, 1, 1, 3, 3, 3], [0, 0, 1, 1, 1, 1])
    none = _fix_log([3, 3, 3], [0, 0, 0])
    assert "not flagged" in checks.evaluate([FLAGGED], late)[0].detail
    assert "still flagged" in checks.evaluate([FLAGGED], sticky)[0].detail
    r = checks.evaluate([FLAGGED], none)[0]
    assert not r.passed and r.detail == "no loss of fix on this log"


def test_around_keys_picks_which_changes_open_the_except_window():
    rows = [{"A": 0, "B": 0, "WPNAME": "X", "REFWPT": "X"} for _ in range(100)]
    for r in rows[50:53]:
        r.update(WPNAME="", A=9)            # the box blanks its TO fix and is wrong
    log = checks.Log.from_rows([(i * 0.2, r) for i, r in enumerate(rows)], {"A", "B", "WPNAME", "REFWPT"})
    c = {"key_pair": ["A", "B"], "tol": 0.1, "except_s_around_sequence": 1, "after_s": 0}
    assert checks.evaluate([c], log)[0].passed                      # hidden by WPNAME's blank
    assert not checks.evaluate([dict(c, around_keys=["REFWPT"])], log)[0].passed


def test_recorder_files_carry_the_flags(tmp_path):
    p = tmp_path / "log.json"
    lines = ['{"frequency":"200","starttime":"2026-01-01T00:00:00"}',
             '{"FPLCDI":[0.1,0,0,0,0,0]}', '{"FPLCDI":[0.0,0,0,0,1,0]}', '{"FPLCDI":[0.0,0,0,1,0,0]}']
    p.write_text("".join(line + chr(10) for line in lines))
    log = checks.Log.from_recorder_files([str(p)], {"FPLCDI"})
    assert ["FPLCDI" in f for f in log.flags] == [False, True, True]


def test_the_log_keeps_when_each_key_was_last_written():
    log = _log([{"A": 1.0}, {}, {}, {"A": 1.0}], {"A"})
    assert [log.silent_s(i, "A") for i in range(4)] == pytest.approx([0.0, 0.2, 0.4, 0.0])
    assert log.silent_s(0, "B") == float("inf")


def test_a_recorder_snapshot_row_is_not_a_write(tmp_path):
    # data_recorder opens every file with a row of every key's current value;
    # a dead link must not look written at an hour boundary
    p = tmp_path / "log.json"
    lines = ['{"frequency":"200","starttime":"2026-01-01T00:00:00"}',
             '{"LAT":[1.0,0,0,0,0,0]}', '{}', '{"LAT":[2.0,0,0,0,0,0]}', '{}',
             '{"frequency":"200","starttime":"2026-01-01T01:00:00"}', '{"LAT":[2.0,0,0,0,0,0]}', '{}']
    p.write_text("".join(line + chr(10) for line in lines))
    log = checks.Log.from_recorder_files([str(p)], {"LAT"})
    assert log.silent_s(0, "LAT") == float("inf")       # the first row is a snapshot too
    assert [round(log.silent_s(i, "LAT"), 1) for i in range(2, 6)] == [0.0, 0.2, 0.4, 0.6]


def _silent_log(written, flags):
    # one row per 0.2 s; written: does the row carry a LAT write; flags: is X flagged
    rows = [dict(({"LAT": 1.0} if w else {}), X=(F if g else 0.0)) for w, g in zip(written, flags)]
    return _log(rows, {"LAT", "X"})


SILENT = {"flagged": ["X"], "while_silent": ["LAT"], "within_s": 0.6, "clear_within_s": 0.4, "after_s": 0}
W = [1] * 8 + [0] * 10 + [1] * 8


def test_flagged_while_silent_passes_a_flag_inside_the_bound():
    f = [0] * 8 + [0, 0, 1] + [1] * 7 + [1, 1, 0] + [0] * 5
    r = checks.evaluate([SILENT], _silent_log(W, f))[0]
    assert r.passed, r.detail


def test_flagged_while_silent_fails_a_late_flag_a_sticky_flag_and_a_log_with_no_silence():
    late = [0] * 8 + [0, 0, 0, 0, 1] + [1] * 5 + [0] * 8
    sticky = [0] * 8 + [0, 0, 1] + [1] * 15
    r = checks.evaluate([SILENT], _silent_log(W, late))[0]
    assert not r.passed and "not flagged, inputs silent 0.6 s" in r.detail
    assert "still flagged" in checks.evaluate([SILENT], _silent_log(W, sticky))[0].detail
    r = checks.evaluate([SILENT], _silent_log([1] * 10, [0] * 10))[0]
    assert not r.passed and r.detail == "no silence of 0.6 s on this log"


def test_flagged_while_silent_ignores_the_value_left_behind():
    # a dead link leaves a good-looking value: only the absence of writes counts
    rows = [dict(({"LAT": 1.0} if x else {}), GPS_FIX_TYPE=3, X=0.0) for x in [1] * 5 + [0] * 10]
    assert not checks.evaluate([SILENT], _log(rows, {"LAT", "X", "GPS_FIX_TYPE"}))[0].passed


def test_skip_when_silent_judges_only_rows_with_a_current_input():
    c = {"key_pair": ["A", "B"], "tol": 0.1, "skip_when_silent": {"LAT": 0.3}, "after_s": 0}
    rows = [{"LAT": 1.0, "A": 0, "B": 0}, {"A": 0, "B": 0}, {"A": 9, "B": 0}, {"A": 9, "B": 0},
            {"LAT": 1.0, "A": 0, "B": 0}]
    log = _log(rows, {"LAT", "A", "B"})
    r = checks.evaluate([c], log)[0]
    assert r.passed and r.compared == 3 and r.silent == 2
    assert not checks.evaluate([dict(c, skip_when_silent={"LAT": 0.5})], log)[0].passed


# -- profile validation ---------------------------------------------------------

def _profile(tmp_path, events=None, checks_=None):
    raw = yaml.safe_load((PROFILES / "qa1-gps-loss-mid-leg-ksmx-r30.yaml").read_text())
    raw["cifp"]["file"] = str(PROFILES / raw["cifp"]["file"])
    if events is not None:
        raw["events"], raw["end"] = events, {}
    if checks_ is not None:
        raw["checks"] = checks_
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(raw))
    return str(p)


def test_an_event_does_exactly_one_thing(tmp_path):
    two = [{"id": "x", "at": {"leg": "NIRLE-ELZUS"}, "do": {"gps_loss_s": 5, "gps_link_loss_s": 5}}]
    with pytest.raises(ProfileError, match="exactly one of offset_nm / hold_min / gps_loss_s / "
                                           "gps_link_loss_s / gps_status_loss_s / gps_position_loss_s"):
        load_profile(_profile(tmp_path, events=two))
    stray = [{"id": "x", "at": {"leg": "NIRLE-ELZUS"}, "do": {"gps_loss_s": 5, "hold_s": 1}}]
    with pytest.raises(ProfileError, match="hold_s goes with offset_nm"):
        load_profile(_profile(tmp_path, events=stray))


def test_a_flagged_check_names_its_keys_and_its_condition(tmp_path):
    with pytest.raises(ProfileError, match="a list of engine keys"):
        load_profile(_profile(tmp_path, checks_=[{"flagged": "FPLCDI", "while_no_fix": True}]))
    with pytest.raises(ProfileError, match="while_no_fix: true"):
        load_profile(_profile(tmp_path, checks_=[{"flagged": ["FPLCDI"]}]))


def test_a_silence_check_names_its_inputs_and_has_one_condition(tmp_path):
    with pytest.raises(ProfileError, match="a list of input keys"):
        load_profile(_profile(tmp_path, checks_=[{"flagged": ["FPLCDI"], "while_silent": "LAT"}]))
    with pytest.raises(ProfileError, match="exactly one condition"):
        load_profile(_profile(tmp_path, checks_=[{"flagged": ["FPLCDI"], "while_silent": ["LAT"],
                                                  "while_no_fix": True}]))
    with pytest.raises(ProfileError, match="while_silent goes with flagged"):
        load_profile(_profile(tmp_path, checks_=[{"key_pair": ["A", "B"], "while_silent": ["LAT"]}]))
    with pytest.raises(ProfileError, match="skip_when_silent"):
        load_profile(_profile(tmp_path, checks_=[{"key_pair": ["A", "B"], "skip_when_silent": ["LAT"]}]))
