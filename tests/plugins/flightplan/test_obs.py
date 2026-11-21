"""GPS OBS mode (OBS2, AER-6084): a pilot course through the active
waypoint.

`FPLCMD OBS <deg>` enters OBS or changes its course; `FPLCMD OBS OFF` leaves
it. In OBS (`FPLSTATE = 4`) `FPLCRS` is the pilot's course, the needle
measures from the course line through the TO waypoint, TO/FROM follows the
geometry, and nothing sequences. OBS lasts until the pilot cancels it or
activates something else (Bill, packet AER-6048: Garmin's rule). Brief:
makerplane/briefs/gps_obs_mode.md; guidance table row in
makerplane/briefs/flight_plan_plan.md section 3.3.1.

The route is the brief's example, "Cleared to TONIE, intercept the 270
course inbound": FLOUT, TONIE, OSH, with TONIE active on a 310 course from
FLOUT. The coordinates are synthetic; MAGVAR is -12 (12 deg east), so the
magnetic courses here are 12 deg less than the true ones.
"""

import json

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine

from .routes import load

MAGVAR = -12.0
GS = 120.0
TONIE = (34.70, -120.20)
FLOUT = geo.destination_point(*TONIE, 310.0 + 12.0 + 180.0, 20.0)   # TONIE on 310 M from FLOUT
OSH = geo.destination_point(*TONIE, 40.0, 25.0)


def tonie_route():
    return [
        engine.Waypoint("FLOUT", *FLOUT),
        engine.Waypoint("TONIE", *TONIE),
        engine.Waypoint("OSH", *OSH),
    ]


def on_course(course_mag, nm_before, off_nm=0.0):
    """A point on the course line through TONIE, nm_before the fix (negative
    past it), off_nm right of the course."""
    true = course_mag - MAGVAR
    p = geo.destination_point(*TONIE, true + 180.0, nm_before)
    if off_nm:
        p = geo.destination_point(*p, true + 90.0, off_nm)
    return p


def obs_xtk(course_mag, lat, lon):
    """Cross-track from the course line through TONIE, worked out here from
    two points on that line, not from the engine."""
    true = course_mag - MAGVAR
    a = geo.destination_point(*TONIE, true + 180.0, 10.0)
    return geo.cross_track_nm(*a, *TONIE, lat, lon)


def update(e, pos, t=1000.0, gs=GS):
    return e.update(pos[0], pos[1], gs, MAGVAR, True, True, None, t)


def cmd(e, text, pos):
    return e.handle_command(text, (pos[0], pos[1], True))


def tonie_active(pos=None):
    """TONIE active on the leg from FLOUT, the aircraft 15 nm out on it."""
    e = engine.Engine()
    load(e, tonie_route(), "OBS", 1)
    pos = pos or geo.destination_point(*TONIE, 310.0 - MAGVAR + 180.0, 15.0)
    cmd(e, "1 ACT 2", pos)
    update(e, pos)
    return e, pos


# ---------------------------------------------------------------------------
# Entering OBS and changing the course
# ---------------------------------------------------------------------------

def test_obs_takes_the_pilot_course_through_the_to_waypoint():
    e, pos = tonie_active()
    assert cmd(e, "2 OBS 270", pos) == (2, "OBS 270")
    out = update(e, pos)
    assert out["state"] == engine.STATE_OBS == 4
    assert out["crs"] == pytest.approx(270.0, abs=1e-9)
    assert out["wp_name"] == "TONIE" and out["act_leg"] == 2
    # The leg is no longer from FLOUT: the course line has no FROM fix.
    assert out["wp_from"] == ""
    assert out["xtk"] == pytest.approx(obs_xtk(270.0, *pos), abs=1e-6)
    # 15 nm out on the 310 course the aircraft is south of the 270 line:
    # left of it, so the needle points right.
    assert out["xtk"] < -5.0 and out["cdi"] > 0.0
    assert out["tf"] == engine.TF_TO


def test_obs_course_change_moves_the_line():
    e, pos = tonie_active()
    cmd(e, "2 OBS 270", pos)
    update(e, pos)
    assert cmd(e, "3 OBS 250.5", pos) == (3, "OBS 250.5")
    out = update(e, pos)
    assert out["state"] == engine.STATE_OBS
    assert out["crs"] == pytest.approx(250.5, abs=1e-9)
    assert out["xtk"] == pytest.approx(obs_xtk(250.5, *pos), abs=1e-6)


def test_obs_course_wraps_360_to_000():
    e, pos = tonie_active()
    assert cmd(e, "2 OBS 360", pos) == (2, "OBS 0")
    assert update(e, pos)["crs"] == pytest.approx(0.0, abs=1e-9)


def test_obs_centres_the_needle_on_the_course_line():
    e, _ = tonie_active()
    pos = on_course(270.0, 6.0)
    cmd(e, "2 OBS 270", pos)
    out = update(e, pos)
    assert out["xtk"] == pytest.approx(0.0, abs=1e-6)
    assert out["wp_dis"] == pytest.approx(6.0, abs=1e-6)


def test_obs_from_susp_takes_over_and_obs_off_does_not_return_to_susp():
    e, pos = tonie_active()
    cmd(e, "2 SUSP", pos)
    assert update(e, pos)["state"] == engine.STATE_SUSP
    assert cmd(e, "3 OBS 270", pos)[0] == 3
    assert update(e, pos)["state"] == engine.STATE_OBS
    assert cmd(e, "4 OBS OFF", pos)[0] == 4
    assert update(e, pos)["state"] == engine.STATE_LEG


def test_obs_in_a_fly_by_turn_drops_the_turn():
    e, _ = tonie_active()
    # Inside TONIE's turn anticipation for the turn onto OSH: the course has
    # moved to the next leg.
    pos = geo.destination_point(*TONIE, 310.0 - MAGVAR + 180.0, 0.5)
    out = update(e, pos)
    assert e._turning
    cmd(e, "2 OBS 270", pos)
    out = update(e, pos)
    assert not e._turning and e._turn_arc is None
    assert out["crs"] == pytest.approx(270.0, abs=1e-9)
    assert out["wp_name"] == "TONIE"


# ---------------------------------------------------------------------------
# TO, then FROM over the fix, and no sequencing
# ---------------------------------------------------------------------------

def test_obs_flips_to_from_past_the_fix_and_does_not_sequence():
    e, _ = tonie_active()
    pos = on_course(270.0, 1.0)
    cmd(e, "2 OBS 270", pos)
    out = update(e, pos)
    assert out["tf"] == engine.TF_TO
    t = 1000.0
    for nm in (0.5, 0.05, -0.05, -0.5, -3.0, -10.0):
        t += 15.0
        out = update(e, on_course(270.0, nm), t)
        assert "msg" not in out
        assert out["act_leg"] == 2 and out["wp_name"] == "TONIE"
        assert out["state"] == engine.STATE_OBS
        assert out["crs"] == pytest.approx(270.0, abs=1e-9)
        assert out["tf"] == (engine.TF_TO if nm > 0 else engine.TF_FROM)
        assert out["xtk"] == pytest.approx(0.0, abs=1e-6)
    assert out["wp_dis"] == pytest.approx(10.0, abs=1e-6)


def test_obs_from_side_cross_track_keeps_its_sense():
    # Outbound on the 270 course past TONIE, 0.4 nm right of the line: the
    # needle still reads the aircraft right of the course (VOR-like).
    e, _ = tonie_active()
    pos = on_course(270.0, -4.0, off_nm=0.4)
    cmd(e, "2 OBS 270", pos)
    out = update(e, pos)
    assert out["tf"] == engine.TF_FROM
    assert out["xtk"] == pytest.approx(0.4, abs=1e-3)
    assert out["cdi"] < 0.0


def test_obs_publishes_no_turn_anticipation_at_the_active_fix():
    e, _ = tonie_active()
    pos = on_course(270.0, 3.0)
    assert e.turn_anticipations(GS, MAGVAR)[1] > 0.0  # TONIE's turn onto OSH, before OBS
    cmd(e, "2 OBS 270", pos)
    update(e, pos)
    assert e.turn_anticipations(GS, MAGVAR)[1] == 0.0


def test_obs_never_activates_the_approach_at_the_faf():
    # Normal mode is required for the final approach course (GNS 430W p.198):
    # an OBS course to the FAF does not ramp to approach scaling.
    d5 = 5.0 / 60.0
    route = [
        engine.Waypoint("IAF", 0.0, 0.0, type=engine.TYPE_FIX, flags=engine.FLAG_IAF),
        engine.Waypoint("FAF", 0.0, d5, type=engine.TYPE_FIX, flags=engine.FLAG_FAF),
        engine.Waypoint("MAP", 0.0, 2 * d5, type=engine.TYPE_MAPPOINT, flags=engine.FLAG_MAP),
    ]
    e = engine.Engine()
    load(e, route, "APR", 1)
    pos = (0.0, d5 - 1.0 / 60.0)   # 1 nm before the FAF
    e.handle_command("1 ACT 2", (pos[0], pos[1], True))
    out = e.update(*pos, GS, 0.0, True, True, None, 1000.0)
    assert out["apr"] == engine.APR_ACTIVE and out["cdiscale"] < 1.0
    e.handle_command("2 OBS 90", (pos[0], pos[1], True))
    out = e.update(*pos, GS, 0.0, True, True, None, 1001.0)
    assert out["state"] == engine.STATE_OBS
    assert out["apr"] == engine.APR_ARMED and out["cdiscale"] == 1.0


# ---------------------------------------------------------------------------
# Leaving OBS
# ---------------------------------------------------------------------------

def test_obs_off_past_the_fix_sequences_to_the_next_leg():
    e, _ = tonie_active()
    pos = on_course(270.0, 2.0)
    cmd(e, "2 OBS 270", pos)
    update(e, pos)
    pos = on_course(270.0, -3.0)
    update(e, pos, 1100.0)
    assert cmd(e, "3 OBS OFF", pos) == (3, "SEQ TONIE -> OSH")
    out = update(e, pos, 1101.0)
    assert out["state"] == engine.STATE_LEG
    assert out["act_leg"] == 3 and out["wp_name"] == "OSH" and out["wp_from"] == "TONIE"
    leg_true = geo.desired_track_true(*TONIE, *OSH, *pos)
    assert out["crs"] == pytest.approx(geo.wrap360(leg_true + MAGVAR), abs=1e-6)
    assert out["xtk"] == pytest.approx(geo.cross_track_nm(*TONIE, *OSH, *pos), abs=1e-6)


def test_obs_off_short_of_the_fix_keeps_the_obs_course_into_it_then_sequences():
    # Cancelling OBS re-enables sequencing; the next waypoint comes up "once
    # the aircraft has crossed the present active-to waypoint" (GNS 430W
    # p.198), and the course into it stays the one set in OBS (G1000 p.62).
    e, _ = tonie_active()
    pos = on_course(270.0, 4.0, off_nm=0.2)
    cmd(e, "2 OBS 270", pos)
    before = update(e, pos)
    assert cmd(e, "3 OBS OFF", pos) == (3, "OBS OFF")
    out = update(e, pos, 1001.0)
    assert out["state"] == engine.STATE_LEG
    assert out["act_leg"] == 2 and out["wp_name"] == "TONIE"
    assert out["xtk"] == pytest.approx(before["xtk"], abs=1e-6)
    assert out["crs"] == pytest.approx(270.0, abs=0.05)
    # Flown on, it sequences at the fix as any leg does.
    t = 1001.0
    for nm in (2.0, 1.0, 0.3, 0.0, -0.3):
        t += 10.0
        out = update(e, on_course(270.0, nm), t)
        if out["act_leg"] == 3:
            break
    assert out["act_leg"] == 3 and out["wp_name"] == "OSH"


def test_obs_off_past_the_last_waypoint_stays_on_it_from():
    e = engine.Engine()
    load(e, tonie_route()[:2], "OBS", 1)
    pos = on_course(270.0, 5.0)
    cmd(e, "1 ACT 2", pos)
    cmd(e, "2 OBS 270", pos)
    pos = on_course(270.0, -2.0)
    update(e, pos)
    assert cmd(e, "3 OBS OFF", pos) == (3, "OBS OFF")
    out = update(e, pos, 1001.0)
    assert out["state"] == engine.STATE_LEG
    assert out["wp_name"] == "TONIE" and out["tf"] == engine.TF_FROM
    assert out["xtk"] == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("verb", ["1 ACT 3", "1 DTO 2", "1 DTO 3", "1 DTOX"])
def test_act_dto_and_dtox_leave_obs(verb):
    e, pos = tonie_active()
    cmd(e, "2 OBS 270", pos)
    update(e, pos)
    ack, _ = cmd(e, verb.replace("1 ", "3 ", 1), pos)
    assert ack == 3
    out = update(e, pos, 1001.0)
    assert out["state"] in (engine.STATE_LEG, engine.STATE_DIRECT)
    assert e.obs_crs is None


def test_a_commit_that_keeps_the_active_leg_keeps_obs():
    # OBS lasts until the pilot cancels it (Garmin's rule, Bill on packet
    # AER-6048), and an edit that keeps the active leg cancels nothing
    # (HOLDFIX0).
    e, pos = tonie_active()
    cmd(e, "2 OBS 270", pos)
    assert load(e, tonie_route(), "OBS", 2) is None
    assert e.obs_crs == 270.0
    out = update(e, pos)
    assert out["state"] == engine.STATE_OBS and out["crs"] == 270.0


def test_a_commit_that_drops_the_active_leg_leaves_obs():
    e, pos = tonie_active()
    cmd(e, "2 OBS 270", pos)
    to = e.act_leg
    route = [wp for i, wp in enumerate(tonie_route(), start=1) if i != to]
    assert load(e, route, "OBS", 2) == engine.ACTIVE_LEG_REMOVED_REASON
    assert e.obs_crs is None
    assert update(e, pos)["state"] == engine.STATE_NONE


def test_obs_off_out_of_obs_is_refused():
    e, pos = tonie_active()
    assert cmd(e, "2 OBS OFF", pos) == (-2, "NOT IN OBS")


@pytest.mark.parametrize("verb", ["SUSP", "RESUME"])
def test_susp_and_resume_are_refused_in_obs(verb):
    # OBS already holds the waypoint; OBS OFF is the way out of it.
    e, pos = tonie_active()
    cmd(e, "2 OBS 270", pos)
    assert cmd(e, f"3 {verb}", pos) == (-3, "IN OBS")
    assert update(e, pos)["state"] == engine.STATE_OBS


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("arg", ["", "ABC", "-1", "361", "nan", "inf"])
def test_obs_with_a_bad_course_is_refused(arg):
    e, pos = tonie_active()
    assert cmd(e, f"2 OBS {arg}".strip(), pos) == (-2, "PARSE")
    assert update(e, pos)["state"] == engine.STATE_LEG


def test_obs_with_no_plan_active_is_refused():
    e = engine.Engine()
    load(e, tonie_route(), "OBS", 1)
    assert cmd(e, "1 OBS 270", TONIE) == (-1, "OBS N/A NO WPT")
    assert engine.Engine().handle_command("1 OBS 270", (0.0, 0.0, True)) == (-1, "OBS N/A NO WPT")


def test_obs_with_no_position_is_refused():
    e, pos = tonie_active()
    assert e.handle_command("2 OBS 270", (pos[0], pos[1], False)) == (-2, "OBS N/A NO POSITION")
    assert e.handle_command("3 OBS 270", None) == (-3, "OBS N/A NO POSITION")
    assert e.obs_crs is None


def _approach_route():
    d5 = 5.0 / 60.0
    return [
        engine.Waypoint("IAF", 0.0, 0.0, type=engine.TYPE_FIX, flags=engine.FLAG_IAF),
        engine.Waypoint("FAF", 0.0, d5, type=engine.TYPE_FIX, flags=engine.FLAG_FAF),
        engine.Waypoint("STEP", 0.0, 1.5 * d5, type=engine.TYPE_FIX),
        engine.Waypoint("MAP", 0.0, 2 * d5, type=engine.TYPE_MAPPOINT, flags=engine.FLAG_MAP),
        engine.Waypoint("MAHP", 0.0, 3 * d5, type=engine.TYPE_FIX, flags=engine.FLAG_MAHP),
    ]


@pytest.mark.parametrize("slot", [3, 4])
def test_obs_on_the_final_approach_course_is_refused(slot):
    # GNS 430W p.198 / G1000 p.467: "Cannot be set for final approach course".
    e = engine.Engine()
    load(e, _approach_route(), "APR", 1)
    e.handle_command(f"1 ACT {slot}", (0.0, 0.1, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.1, True)) == (-2, "OBS N/A FINAL APPROACH")


def test_obs_to_the_faf_and_after_the_missed_is_allowed():
    e = engine.Engine()
    load(e, _approach_route(), "APR", 1)
    e.handle_command("1 ACT 2", (0.0, 0.01, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.01, True))[0] == 2
    e.handle_command("3 ACT 5", (0.0, 0.2, True))
    assert e.handle_command("4 OBS 90", (0.0, 0.2, True))[0] == 4


def test_obs_at_the_map_auto_suspend_is_refused():
    e = engine.Engine()
    load(e, _approach_route(), "APR", 1)
    e.handle_command("1 ACT 4", (0.0, 0.16, True))
    out = e.update(0.0, 2 * 5.0 / 60.0 + 0.002, GS, 0.0, True, True, None, 1000.0)
    assert out["susp_map"] == 1
    assert e.handle_command("2 OBS 90", (0.0, 0.17, True)) == (-2, "OBS N/A FINAL APPROACH")


def test_obs_on_a_map_with_no_faf_coded_is_refused():
    e = engine.Engine()
    route = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("MAP", 0.0, 0.1, flags=engine.FLAG_MAP)]
    load(e, route, "M", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.0, True)) == (-2, "OBS N/A FINAL APPROACH")


@pytest.mark.parametrize("pt,reason", [("HM", "OBS N/A HOLD"), ("HF", "OBS N/A HOLD"), ("HA", "OBS N/A HOLD"),
                                       ("PI", "OBS N/A PROC TURN")])
def test_obs_on_a_coded_hold_or_procedure_turn_is_refused(pt, reason):
    # "Cannot be set for ... published holding patterns" (GNS 430W p.198);
    # the GTN offers SUSP on leg types that do not support OBS (p.3-102).
    e = engine.Engine()
    route = [
        engine.Waypoint("A", 0.0, 0.0),
        engine.Waypoint("HLD", 0.0, 0.1, pt=pt, crs=90.0, dst=4.0, turn="R", alt="+3000"),
        engine.Waypoint("B", 0.0, 0.3),
    ]
    load(e, route, "H", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.0, True)) == (-2, reason)


@pytest.mark.parametrize("pt", sorted(engine.PT_VECTOR))
def test_obs_on_a_vector_leg_is_refused(pt):
    e = engine.Engine()
    route = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("V", 0.0, 0.1, pt=pt, crs=90.0, alt="+3000"),
             engine.Waypoint("B", 0.0, 0.3)]
    load(e, route, "V", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.0, True)) == (-2, "OBS N/A VECTORS")


def test_obs_on_an_altitude_terminated_leg_with_no_fix_is_refused():
    # A CA ends wherever the climb does, and an FA past its fix holds a
    # course to an altitude: neither has a waypoint to hold (GNS 430W p.186,
    # "OBS not available ... no destination waypoint").
    e = engine.Engine()
    route = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("", 0.0, 0.0, pt="CA", crs=90.0, alt="+3000"),
             engine.Waypoint("B", 0.0, 0.3)]
    load(e, route, "CA", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.0, True)) == (-2, "OBS N/A NO FIX")

    e = engine.Engine()
    route = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("F", 0.0, 0.1, pt="FA", crs=90.0, alt="+3000"),
             engine.Waypoint("B", 0.0, 0.3)]
    load(e, route, "FA", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert e.handle_command("2 OBS 90", (0.0, 0.0, True))[0] == 2   # on the way to F: a fix to hold
    e.handle_command("3 OBS OFF", (0.0, 0.0, True))
    e.update(0.0, 0.1005, GS, 0.0, True, True, None, 1000.0, 1000.0)   # past F, below the altitude
    assert e._leg_phase == engine.LEGPHASE_FA_COURSE
    assert e.handle_command("4 OBS 90", (0.0, 0.1005, True)) == (-4, "OBS N/A NO FIX")


# ---------------------------------------------------------------------------
# Off-route Direct-To and persistence
# ---------------------------------------------------------------------------

def test_obs_on_an_off_route_direct_to():
    e = engine.Engine()
    pos = on_course(270.0, 5.0)
    e.handle_command("1 DTO", (pos[0], pos[1], True), ("TONIE", TONIE[0], TONIE[1], engine.TYPE_FIX))
    assert cmd(e, "2 OBS 270", pos)[0] == 2
    out = update(e, on_course(270.0, -1.0))
    assert out["state"] == engine.STATE_OBS and out["tf"] == engine.TF_FROM and out["act_leg"] == 0
    assert cmd(e, "3 OBS OFF", on_course(270.0, -1.0)) == (3, "OBS OFF")
    assert update(e, on_course(270.0, -1.0), 1001.0)["state"] == engine.STATE_DIRECT


def test_obs_survives_a_restart():
    e, _ = tonie_active()
    pos = on_course(270.0, -2.0, off_nm=0.3)
    cmd(e, "2 OBS 270", pos)
    before = update(e, pos)
    saved = e.to_persisted_dict()

    e2 = engine.Engine()
    e2.restore_from_dict(saved)
    after = update(e2, pos)
    assert after["state"] == engine.STATE_OBS
    for k in ("crs", "xtk"):
        assert after[k] == pytest.approx(before[k]), k
    for k in ("tf", "act_leg", "wp_name", "wp_from"):
        assert after[k] == before[k], k
    assert cmd(e2, "3 OBS OFF", pos) == (3, "SEQ TONIE -> OSH")


def test_a_state_file_from_before_obs_restores_out_of_obs():
    e, pos = tonie_active()
    saved = e.to_persisted_dict()
    saved.pop("obs_crs", None)
    e2 = engine.Engine()
    e2.restore_from_dict(saved)
    assert e2.obs_crs is None
    assert update(e2, pos)["state"] == engine.STATE_LEG


# ---------------------------------------------------------------------------
# Through the bus: FPLSTATE 4 reaches the database, and survives a restart
# ---------------------------------------------------------------------------

def _plugin_with_tonie_active(database, tmp_path):
    from .test_plugin import make_config, write_route
    import fixgw.plugins.flightplan as flightplan

    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    pl.thread._restore()
    database.write("GPS_FIX_TYPE", 3)   # a 3-D fix, so the integrity gate passes
    write_route(database, tonie_route(), seq=1)
    pos = on_course(270.0, 3.0)
    database.write("LAT", pos[0])
    database.write("LONG", pos[1])
    database.write("GS", GS)
    database.write("MAGVAR", MAGVAR)
    database.write("FPLCMD", "1 ACT 2")
    return pl


def test_obs_reaches_the_bus_as_fplstate_4(database, tmp_path):
    pl = _plugin_with_tonie_active(database, tmp_path)
    database.write("FPLCMD", "2 OBS 270")
    pl.thread._run_update_cycle()
    assert database.read("FPLCMDACK")[0] == 2
    assert database.read("FPLMSG")[0] == "OBS 270"
    assert database.read("FPLSTATE")[0] == engine.STATE_OBS == 4
    assert database.read("FPLCRS")[0] == pytest.approx(270.0, abs=0.01)
    assert database.read("FPLXTK")[0] == pytest.approx(0.0, abs=0.01)
    assert database.read("WPNAME")[0] == "TONIE"

    database.write("FPLCMD", "3 OBS OFF")
    pl.thread._run_update_cycle()
    assert database.read("FPLCMDACK")[0] == 3
    assert database.read("FPLSTATE")[0] == engine.STATE_LEG


def test_obs_survives_a_gateway_restart(database, tmp_path):
    import fixgw.plugins.flightplan as flightplan
    from .test_plugin import make_config

    pl = _plugin_with_tonie_active(database, tmp_path)
    database.write("FPLCMD", "2 OBS 270")
    pl.thread._run_update_cycle()
    saved = json.loads((tmp_path / "flightplan_state.json").read_text())
    assert saved["obs_crs"] == 270.0
    # The gateway goes down: the first plugin hears nothing more.
    for key, fn in (("FPLSEQ", pl.thread._on_fplseq), ("FPLCMD", pl.thread._on_fplcmd),
                    ("LAT", pl.thread._on_position), ("LONG", pl.thread._on_position),
                    ("GPS_FIX_TYPE", pl.thread._on_fix_type)):
        pl.db_callback_del(key, fn)

    database.write("FPLSTATE", 0)
    pl2 = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    pl2.thread._restore()
    pos = on_course(270.0, -1.0)   # a live position after the restart, past TONIE
    database.write("LAT", pos[0])
    database.write("LONG", pos[1])
    pl2.thread._run_update_cycle()
    assert pl2.thread.engine.obs_crs == 270.0
    assert database.read("FPLSTATE")[0] == engine.STATE_OBS
    assert database.read("FPLCRS")[0] == pytest.approx(270.0, abs=0.01)
    assert database.read("FPLTF")[0] == engine.TF_FROM and database.read("WPNAME")[0] == "TONIE"
