"""QA1's two X-Plane sessions (AER-4219), replayed through the engine (SYN4b,
AER-4299): KSSF RNAV 28 with the coded right turn to GANLE, and KDED RNAV 30
to the BARBS teardrop. The gate flies both recordings (``test_profiles.py``);
this module shows that their checks can fail, that the variation each
recording replays with is X-Plane's, and compares the KDED hold's inbound
leg, which the gate skips. QA's checks and verdicts on these sessions stay
QA's: nothing here judges the turn direction or the entry.
"""

import statistics

import pytest
import yaml

from fixgw.sim import checks
from fixgw.sim.profile import ProfileError, load_profile

from . import profile_gate, xplane_replay
from .xplane_replay import fly, load_trace, recording_args, xplane_sequences

KSSF = profile_gate.RECORDINGS_DIR / "qa1_kssf_r28_xplane_2026-11-01.yaml"
KDED = profile_gate.RECORDINGS_DIR / "qa1_kded_r30_xplane_2026-11-01.yaml"
DOTS_FS = 2.5


def w180(a):
    return (a + 180.0) % 360.0 - 180.0


def _trace(rec):
    return load_trace(rec.parent / yaml.safe_load(rec.read_text())["trace"])


def _perturbed(rec, change):
    rows = _trace(rec)
    for r in rows:
        change(r)
    return profile_gate.fly_recording(rec, trace=rows)


@pytest.mark.parametrize("rec, fix", [(KSSF, "BEFOR"), (KDED, "XEHMO")], ids=["kssf", "kded"])
def test_xplanes_course_off_by_2p5_deg_on_one_leg_fails_the_course_check(rec, fix):
    def change(r):
        if r["xp_wpt"] == fix:
            r["xp_crs"] += 2.5
    fails = profile_gate.failure_lines(_perturbed(rec, change))
    assert len(fails) == 1 and "FPLCRS vs EXTCRS (+-2.0 deg) FAILED" in fails[0]


@pytest.mark.parametrize("rec, fix", [(KSSF, "LANDD"), (KDED, "BARBS")], ids=["kssf", "kded"])
def test_xplanes_deflection_offset_on_one_leg_fails_the_cdi_check(rec, fix):
    # 0.15 of full scale: X-Plane's deflection sits near zero on these legs,
    # so reversing it alone would not reach the 0.1 bound.
    def change(r):
        if r["xp_wpt"] == fix:
            r["xp_hdef_dot"] += 0.15 * DOTS_FS
    fails = profile_gate.failure_lines(_perturbed(rec, change))
    assert len(fails) == 1 and "FPLCDI vs EXTCDI (+-0.1) FAILED" in fails[0]


@pytest.mark.parametrize("rec, fix, then", [(KSSF, "LANDD", "RW28"), (KDED, "YEBBU", "RW30")],
                         ids=["kssf", "kded"])
def test_xplane_skipping_a_fix_fails_the_sequence_check(rec, fix, then):
    def change(r):
        if r["xp_wpt"] == fix:
            r["xp_wpt"] = then
    fails = profile_gate.failure_lines(_perturbed(rec, change))
    assert any(": sequence " in f for f in fails)


def _xplane_variation(rec, magvar):
    """{fix: X-Plane's variation} per leg flown at ``magvar``: the median of
    X-Plane's course minus the engine's true course, where both are TO the
    same fix, outside 5 s of either navigator's sequence and outside the hold."""
    args = recording_args(rec)
    rows = _trace(rec) if rec != xplane_replay.FP8_RECORDING else load_trace()
    samples, events = fly(rows, args["profile"], args["pilot"], magvar)
    seq = [ev[0] for ev in events if ev[1] == "sequence"] + [x[0] for x in xplane_sequences(rows)]
    legs = {}
    for s in samples:
        if (s["state"] == 1 and s["phase"] != "HOLD" and s["wp_name"] == s["xp_wpt"] and s["xp_fromto"] == 1
                and not any(abs(s["t"] - t) <= 5 for t in seq)):
            legs.setdefault(s["wp_name"], []).append(w180(s["xp_crs"] - s["crs"]))
    return {k: magvar + statistics.median(v) for k, v in legs.items()}


def test_the_variation_method_recovers_the_bench_bus_magvar_on_fp8():
    # FP8 logged the bench bus, whose MAGVAR is X-Plane's own variation
    # (connections/xplane.yaml, group 17). Recovered from X-Plane's navigator
    # instead, it lands within 0.05 deg of it on every leg.
    per_leg = _xplane_variation(xplane_replay.FP8_RECORDING, xplane_replay.MAGVAR)
    assert len(per_leg) == 6
    assert all(abs(v - xplane_replay.MAGVAR) <= 0.05 for v in per_leg.values()), per_leg


@pytest.mark.parametrize("rec, legs, cifp", [
    (KSSF, ("GEARE", "BEFOR", "LANDD", "RW28"), -3.0),
    (KDED, ("CUGAK", "YEBBU", "RW30", "XEHMO", "BARBS"), 5.0),
], ids=["kssf", "kded"])
def test_each_recording_replays_at_xplanes_variation_not_the_cifps(rec, legs, cifp):
    # The sessions logged no bus. The recording's magvar is the median of the
    # per-leg variation recovered from X-Plane's navigator (the GANLE DF is
    # left out: it starts somewhere else in each navigator).
    magvar = yaml.safe_load(rec.read_text())["magvar"]
    per_leg = _xplane_variation(rec, cifp)
    assert set(legs) <= set(per_leg)
    assert abs(statistics.median(per_leg[k] for k in legs) - magvar) <= 0.05
    assert all(abs(per_leg[k] - magvar) <= 0.15 for k in legs), per_leg


def test_kded_hold_inbound_leg_matches_xplane():
    """Inside the BARBS hold, wherever the engine shows the inbound course
    (175) and X-Plane is TO the fix, the two agree. The gate skips the hold:
    X-Plane holds its hold course through the entry and the turns, and the
    engine turns the course with the aircraft (XPB-004)."""
    args = recording_args(KDED)
    samples, _ = fly(_trace(KDED), **args)
    # Rows are picked by the engine's own course, not by agreement, so an
    # inbound disagreement fails rather than drops out.
    inbound = [s for s in samples if s["phase"] == "HOLD" and s["xp_fromto"] == 1
               and abs(w180(s["crs"] - 175.0)) <= 0.5]
    assert len(inbound) > 500
    for s in inbound:
        assert abs(w180(s["crs"] - s["xp_crs"])) <= 2.0
        xp = s["xp_hdef_dot"] / DOTS_FS
        if abs(s["cdi"]) > 0.05 and abs(xp) > 0.05:
            assert (s["cdi"] > 0) == (xp > 0)
        assert abs(s["cdi"] - xp) <= 0.1


def test_skip_when_differ_compares_only_rows_where_the_keys_agree():
    c = {"key_pair": ["A", "B"], "tol": 0.1, "skip_when_differ": ["P", "Q"], "after_s": 0}
    rows = [(0.0, {"P": "X", "Q": "X", "A": 0, "B": 0}), (0.2, {"Q": "Y", "A": 9}),
            (0.4, {"P": "Y", "A": 0})]
    log = checks.Log.from_rows(rows, checks.keys_needed([c]))
    r = checks.evaluate([c], log, start_s=0)[0]
    assert r.passed and r.compared == 2
    assert not checks.evaluate([dict(c, skip_when_differ=None)], log, start_s=0)[0].passed


def test_skip_when_differ_needs_two_keys(tmp_path):
    raw = yaml.safe_load((profile_gate.PROFILES_DIR / "qa1-hold-teardrop-left-kded-r30.yaml").read_text())
    raw["cifp"]["file"] = str(profile_gate.PROFILES_DIR / raw["cifp"]["file"])
    raw["checks"] = [{"key_pair": ["FPLCRS", "REFCRS"], "tol_deg": 1, "skip_when_differ": ["WPNAME"]}]
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(ProfileError, match="skip_when_differ"):
        load_profile(str(p))
