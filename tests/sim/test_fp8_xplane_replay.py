"""FP8 playback fixture (AER-809, billmallard/pyEfis#190): the real X-Plane
flight of 2026-10-29, replayed through the engine, against X-Plane's own
navigator recorded in the same trace.

Acceptance from the brief (section 6): DTK within 2 deg, CDI the same sign
and within 0.1 of full scale at 1 nm cross-track, sequencing point within
0.3 nm; LNAV 2 nm before the FAF, the ramp, 0.3 to the MAP, no sequence at
the MAP, auto-SUSP, RESUME to the missed approach with TERM scaling.

X-Plane's deviation is ``gps_hdef_dot``: 2.5 dots is full scale.

Every allowance made here for X-Plane cites its entry in
``src/fixgw/sim/XPLANE_BEHAVIORS.md`` (XPB-NNN).
"""

import pytest

from .xplane_replay import build_route, fly, load_bench_bus, load_trace, nm, xplane_sequences

DOTS_FS = 2.5

# The sequences the live bench engine (fix-gateway 2666bc4) made on its own.
# RW30 -> HANNU and HANNU -> WINCH followed the orchestrator's RESUMEs, which
# the replay re-derives from engine state rather than the wall clock.
AUTO_SEQUENCES = ("PAMJI -> CIMSO", "CIMSO -> ANIBY", "ANIBY -> RW30", "WINCH -> WINCH")


def w180(a):
    return (a + 180.0) % 360.0 - 180.0


@pytest.fixture(scope="module")
def flown():
    rows = load_trace()
    samples, events = fly(rows)
    wps = {w.id: w for w in build_route()[0] if w.id}
    return rows, samples, events, wps


def _seq(events):
    return {ev[2]: (ev[3], ev[4], ev[0]) for ev in events if ev[1] == "sequence"}


def live_bench_sequences():
    """{"A -> B": (lat, lon)} wherever FPLACTLEG changed in the bench bus log."""
    out = {}
    bus = load_bench_bus()
    for a, b in zip(bus, bus[1:]):
        if b["FPLACTLEG"] != a["FPLACTLEG"]:
            out[f"{a['WPNAME']} -> {b['WPNAME']}"] = (float(b["LAT"]), float(b["LONG"]))
    return out


def test_replay_lands_on_the_live_bench_sequence_points(flown):
    # The replay must sequence where the live engine did: that is what makes
    # the trace a faithful stand-in for the flight.
    _, _, events, _ = flown
    seq = _seq(events)
    live = live_bench_sequences()
    for name in AUTO_SEQUENCES:
        # 0.04 nm: the bus log is 1 Hz (0.03 nm at 110 kt), the trace 5 Hz
        assert nm(*live[name], *seq[name][:2]) <= 0.04, name


def test_sequence_points_within_0p3_nm_of_xplane(flown):
    # XPB-001: X-Plane switches about 0.2 nm before each of these fixes, even
    # the collinear ones. 0.3 nm is the FP8 brief's bound, not fitted to it.
    # Not compared: RW30 -> HANNU, where the engine sequences on the
    # orchestrator's RESUME (XPB-005), and HANNU -> WINCH, where X-Plane
    # switches the waypoint abeam the fix (XPB-001).
    rows, _, events, _ = flown
    seq = _seq(events)
    xp = {f"{a} -> {b}": (lat, lon) for _, a, b, lat, lon in xplane_sequences(rows)}
    for name in ("PAMJI -> CIMSO", "CIMSO -> ANIBY", "ANIBY -> RW30"):
        assert nm(*seq[name][:2], *xp[name]) <= 0.3, name


def test_lnav_two_nm_before_the_faf_then_the_ramp(flown):
    _, samples, events, wps = flown
    faf = wps["CIMSO"]
    lnav = next(ev for ev in events if ev[1] == "phase" and ev[2].endswith("-> LNAV"))
    assert 1.95 <= nm(lnav[3], lnav[4], faf.lat, faf.lon) <= 2.05
    to_faf = [s for s in samples if s["wp_name"] == "CIMSO" and s["phase"] == "LNAV"]
    for d, scale in ((1.0, 0.65), (0.5, 0.475)):
        s = min(to_faf, key=lambda s: abs(nm(s["lat"], s["lon"], faf.lat, faf.lon) - d))
        assert s["cdiscale"] == pytest.approx(scale, abs=0.01), d
    final = {round(s["cdiscale"], 3) for s in samples if s["wp_name"] in ("ANIBY", "RW30") and s["state"] == 1}
    assert final == {0.3}


def test_no_sequence_at_the_map_auto_susp_then_from(flown):
    _, samples, events, wps = flown
    map_wp = wps["RW30"]
    susp = next(ev for ev in events if ev[1] == "state" and ev[2] == "1 -> 3")
    assert nm(susp[3], susp[4], map_wp.lat, map_wp.lon) <= 0.05
    resume_t = next(ev[0] for ev in events if ev[1] == "cmd" and ev[2].startswith("RESUME"))
    held = [s for s in samples if susp[0] <= s["t"] < resume_t]
    assert held and all(s["wp_name"] == "RW30" and s["state"] == 3 for s in held)
    assert held[-1]["tf"] == 2  # FROM past the MAP


def test_resume_flies_the_missed_with_term_scaling(flown):
    _, samples, events, _ = flown
    resume_t = next(ev[0] for ev in events if ev[1] == "cmd" and ev[2].startswith("RESUME"))
    after = [s for s in samples if resume_t < s["t"] <= resume_t + 60]
    assert all(s["apr"] == 3 and s["cdiscale"] == 1.0 and s["phase"] == "TERM" for s in after)
    assert after[-1]["wp_name"] == "HANNU"


def test_susp_does_not_sequence_past_hannu(flown):
    _, samples, events, wps = flown
    t_susp = next(ev[0] for ev in events if ev[1] == "cmd" and ev[2].startswith("SUSP"))
    past = next(ev for ev in events if ev[1] == "past HANNU suspended")
    assert "wp HANNU" in past[2] and "tf 2" in past[2]
    h = wps["HANNU"]
    held = [s for s in samples if t_susp < s["t"] < past[0]]
    assert all(s["wp_name"] == "HANNU" for s in held)
    assert nm(past[3], past[4], h.lat, h.lon) <= 0.1  # FROM right at the fix, not before it
    after = [s for s in samples if past[0] < s["t"] <= past[0] + 5]
    assert all(s["wp_name"] == "WINCH" and s["state"] == 1 for s in after)


def _shared(samples, events, rows, wps):
    """Rows where both systems guide to the same fix on the same leg: the
    same active waypoint, not within 5 s of either system's sequence (XPB-001),
    and not in the last 0.5 nm to WINCH, where X-Plane already shows its hold
    course (XPB-004)."""
    seq_t = [ev[0] for ev in events if ev[1] == "sequence"] + [x[0] for x in xplane_sequences(rows)]
    w = wps["WINCH"]
    return [s for s in samples
            if s["state"] == 1 and s["phase"] != "HOLD" and s["wp_name"] == s["xp_wpt"]
            and not any(abs(s["t"] - t) <= 5 for t in seq_t)
            and not (s["wp_name"] == "WINCH" and nm(s["lat"], s["lon"], w.lat, w.lon) < 0.5)]


def test_dtk_within_2_deg_of_xplane(flown):
    rows, samples, events, wps = flown
    shared = _shared(samples, events, rows, wps)
    assert len(shared) > 3000
    worst = max(abs(w180(s["crs"] - s["xp_crs"])) for s in shared)
    assert worst <= 2.0


def test_cdi_matches_xplane_at_1_nm_cross_track(flown):
    rows, samples, events, wps = flown
    off = [s for s in _shared(samples, events, rows, wps) if abs(s["xtk"]) >= 0.9]
    assert {s["xtk"] > 0 for s in off} == {True, False}  # both offsets flown
    for s in off:
        xp = s["xp_hdef_dot"] / DOTS_FS
        assert (s["cdi"] > 0) == (xp > 0)
        assert abs(s["cdi"] - xp) <= 0.1


def test_hold_inbound_leg_matches_xplane(flown):
    """Inside the WINCH hold, wherever the engine shows the inbound course
    (121) and X-Plane is TO the fix, the two agree. X-Plane holds its hold
    course through the entry and the turns; the engine flies the AIM entry
    and turns the course with the aircraft (fix-gateway#49, Bill's call on
    AER-4076), so those stretches are not compared (XPB-004)."""
    _, samples, _, _ = flown
    # XPB-004: rows are picked by agreement within 5 deg, so an inbound
    # disagreement over 5 deg is dropped, not failed; only the row count
    # guards it (QA, AER-4375; selecting on the engine's own course is
    # fix-gateway#89).
    inbound = [s for s in samples if s["phase"] == "HOLD" and s["xp_fromto"] == 1
               and abs(w180(s["crs"] - s["xp_crs"])) <= 5.0]
    assert len(inbound) > 500
    for s in inbound:
        assert abs(w180(s["crs"] - s["xp_crs"])) <= 2.0
        xp = s["xp_hdef_dot"] / DOTS_FS
        # XPB-004: sign only off centre (both beyond 0.05 of full scale); the
        # 0.1 bound below still applies to every row.
        if abs(s["cdi"]) > 0.05 and abs(xp) > 0.05:
            assert (s["cdi"] > 0) == (xp > 0)
        assert abs(s["cdi"] - xp) <= 0.1
