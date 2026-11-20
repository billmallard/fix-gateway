"""HOLDFIX0 (AER-6165): an edit to the active plan keeps the active leg.

A commit that still contains the active leg -- its TO fix, the same FROM fix
in the slot before it, and the same path into it -- keeps that leg active at
its new slot, with its phase and any hold state. A commit that removes or
breaks it drops the activation, as every commit did before, with an FPLMSG
saying why (Bill, 2026-11-20, packet AER-6048). Scenario S1 is the spike's
(makerplane/briefs/hold_at_any_fix_spike.md): a hold inserted at the TO fix
ahead of the aircraft, then flown.
"""

import json

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine

from .routes import hold_circuit_points
from .routes import load as _load
from .test_plugin import make_config
from .test_plugin import write_route as _write_route

GS = 120.0


def wp(ident, lon, lat=0.0, **kw):
    return engine.Waypoint(ident, lat, lon, **kw)


# West to east along the equator, 30 nm apart. The hold is at B, inbound
# 090 (the leg A-B arrives on it: a direct entry), right turns, 5 nm legs.
A, B, C, D = wp("A", -0.5), wp("B", 0.0), wp("C", 0.5), wp("D", 1.0)
Z, X = wp("Z", -1.0), wp("X", 0.25, lat=0.05)
HOLD_B = wp("B", 0.0, pt="HM", crs=90.0, dst=5.0, turn="R")


def fresh(route):
    # waypoint_segments numbers each slot's sgi in place: every commit gets
    # its own slots, as one off the bus does.
    return [engine.Waypoint.from_dict(w.to_dict()) for w in route]


def load(e, route, name, seq):
    return _load(e, fresh(route), name, seq)


def write_route(database, route, seq):
    _write_route(database, fresh(route), seq=seq)


def update(e, lon, lat=0.0, t=1000.0):
    return e.update(lat, lon, GS, 0.0, True, None, None, t)


def active(route, verb, at_lon, at_lat=0.0):
    e = engine.Engine()
    load(e, route, "T", 1)
    ack, msg = e.handle_command(f"1 {verb}", (at_lat, at_lon, True))
    assert ack == 1, msg
    update(e, at_lon, at_lat)
    return e


def guidance(out):
    return {k: out[k] for k in ("state", "crs", "xtk", "tf", "wp_name", "wp_from", "wp_dis", "fr_lat", "fr_lon")}


# --- S1 -----------------------------------------------------------------

def test_s1_a_hold_inserted_at_the_to_fix_ahead_keeps_the_leg_and_is_flown():
    e = active([A, B, C], "ACT 2", -0.25)
    assert load(e, [A, B, HOLD_B, C], "T", 2) is None
    out = update(e, -0.2, t=1001.0)
    assert (e.mode, out["act_leg"], out["wp_name"], out["wp_from"]) == ("LEG", 2, "B", "A")
    assert (out["hold_slot"], out["hold_ent"], out["hold_commit"]) == (3, "DIRECT", False)

    # Into the hold: past B the hold slot is active, entered direct.
    out = update(e, 0.001, t=1002.0)
    assert out["act_leg"] == 3 and out["phase"] == "HOLD"
    assert (out["hold_slot"], out["hold_ent"], out["hold_commit"]) == (3, "DIRECT", True)
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT

    # And around it: the hold repeats until RESUME, then exits to C.
    turn_out, outbound, turn_in, past_fix = hold_circuit_points(0.0, 0.0, 90.0, 5.0, "R", GS)
    for t, p in enumerate((turn_out, outbound, turn_in, past_fix), start=1003):
        out = e.update(*p, GS, 0.0, True, None, None, float(t))
    assert out["act_leg"] == 3 and e._leg_phase == engine.LEGPHASE_TURN_OUT
    assert e.handle_command("2 RESUME", (*past_fix, True)) == (2, "HOLD EXIT ARMED")
    for t, p in enumerate((turn_out, outbound, turn_in, past_fix), start=1010):
        out = e.update(*p, GS, 0.0, True, None, None, float(t))
    assert (out["act_leg"], out["wp_name"]) == (4, "C")


# --- inserts and removals ----------------------------------------------

def test_an_insert_before_the_from_fix_moves_the_leg_and_changes_no_guidance():
    e = active([A, B, C, D], "ACT 3", 0.2)
    before = update(e, 0.25, t=1001.0)
    assert load(e, [Z, A, B, C, D], "T", 2) is None
    after = update(e, 0.25, t=1002.0)
    assert (before["act_leg"], after["act_leg"]) == (3, 4)
    assert guidance(after) == guidance(before)


def test_an_insert_after_the_to_fix_keeps_the_slot_and_names_the_new_next_fix():
    e = active([A, B, C, D], "ACT 3", 0.2)
    before = update(e, 0.25, t=1001.0)
    assert load(e, [A, B, C, X, D], "T", 2) is None
    after = update(e, 0.25, t=1002.0)
    assert after["act_leg"] == 3 and after["wp_next"] == "X"
    assert guidance(after) == guidance(before)


def test_an_insert_between_the_from_and_the_to_fix_drops_the_leg():
    e = active([A, B, C, D], "ACT 3", 0.2)
    assert load(e, [A, B, X, C, D], "T", 2) == engine.ACTIVE_LEG_CHANGED_REASON
    assert (e.mode, e.act_leg) == ("NONE", 0)
    assert update(e, 0.25, t=1001.0)["state"] == engine.STATE_NONE


def test_removing_the_to_fix_drops_the_leg():
    e = active([A, B, C, D], "ACT 3", 0.2)
    assert load(e, [A, B, D], "T", 2) == engine.ACTIVE_LEG_REMOVED_REASON
    assert (e.mode, e.act_leg) == ("NONE", 0)


def test_removing_the_from_fix_drops_the_leg():
    e = active([A, B, C, D], "ACT 3", 0.2)
    assert load(e, [A, C, D], "T", 2) == engine.ACTIVE_LEG_CHANGED_REASON
    assert e.mode == "NONE"


def test_moving_the_to_fix_drops_the_leg():
    e = active([A, B, C, D], "ACT 3", 0.2)
    moved = wp("C", 0.5, lat=0.01)
    assert load(e, [A, B, moved, D], "T", 2) == engine.ACTIVE_LEG_REMOVED_REASON


def test_clearing_the_plan_drops_the_leg():
    e = active([A, B, C], "ACT 2", -0.25)
    assert load(e, [], "", 2) == engine.ACTIVE_LEG_REMOVED_REASON
    assert e.mode == "NONE"


def test_the_first_leg_is_flown_from_the_aircraft_so_an_insert_before_it_drops_it():
    e = active([B, C], "ACT 1", -0.25)
    assert load(e, [B, C, D], "T", 2) is None and e.act_leg == 1
    assert load(e, [A, B, C, D], "T", 3) == engine.ACTIVE_LEG_CHANGED_REASON


def test_a_changed_path_into_the_to_fix_drops_the_leg():
    e = active([A, B, C, D], "ACT 3", 0.2)
    cf = wp("C", 0.5, pt="CF", crs=80.0)
    assert load(e, [A, B, cf, D], "T", 2) == engine.ACTIVE_LEG_CHANGED_REASON
    # A course flown into the fix is the leg: a new course is a new leg.
    e = active([A, B, cf, D], "ACT 3", 0.2)
    assert load(e, [A, B, cf, D], "T", 2) is None
    assert load(e, [A, B, wp("C", 0.5, pt="CF", crs=100.0), D], "T", 3) == engine.ACTIVE_LEG_CHANGED_REASON


def test_a_procedure_if_merged_onto_the_to_fix_keeps_the_leg():
    # The editor drops a plain waypoint where a procedure's IF starts at
    # the same fix (proc_load.insert). IF and TF are one great circle.
    e = active([A, B, C], "ACT 3", 0.2)
    before = update(e, 0.25, t=1001.0)
    iaf = wp("C", 0.5, pt="IF", flags=engine.FLAG_IAF | engine.FLAG_FROM_PROCEDURE, alt="+3000")
    assert load(e, [A, B, iaf, wp("FAF", 0.7, pt="TF", flags=engine.FLAG_FAF)], "T", 2) is None
    after = update(e, 0.25, t=1002.0)
    assert after["act_leg"] == 3 and guidance(after) == guidance(before)


def test_a_repeated_leg_is_not_guessed_at():
    e = active([A, B, C, A, B], "ACT 2", -0.25)
    assert load(e, [Z, A, B, C, A, B], "T", 2) == engine.ACTIVE_LEG_AMBIGUOUS_REASON
    assert e.mode == "NONE"


def test_a_rejected_commit_leaves_the_active_leg_untouched():
    e = active([A, B, C, D], "ACT 3", 0.2)
    before = e.to_persisted_dict()
    with pytest.raises(engine.RouteRejected):
        load(e, [Z, A, B, C, wp("ARC", 1.0, pt="RF")], "T", 2)
    assert e.to_persisted_dict() == before


def test_an_inactive_plan_stays_inactive():
    e = engine.Engine()
    load(e, [A, B, C], "T", 1)
    assert load(e, [Z, A, B, C], "T", 2) is None
    assert (e.mode, e.act_leg) == ("NONE", 0)


# --- a hold being flown -------------------------------------------------

def _in_the_hold():
    e = active([A, B, HOLD_B, C], "ACT 2", -0.25)
    update(e, 0.001, t=1001.0)
    turn_out, outbound, turn_in, past_fix = hold_circuit_points(0.0, 0.0, 90.0, 5.0, "R", GS)
    e.update(*turn_out, GS, 0.0, True, None, None, 1002.0)
    assert (e.act_leg, e._leg_phase) == (3, engine.LEGPHASE_OUTBOUND)
    return e, (turn_out, outbound, turn_in, past_fix)


def test_a_re_commit_mid_hold_keeps_the_hold_where_it_is():
    e, (turn_out, outbound, turn_in, past_fix) = _in_the_hold()
    probe = geo.destination_point(*turn_out, 270.0, 1.0)
    before = e.update(*probe, GS, 0.0, True, None, None, 1003.0)
    held = (e._leg_phase, e._hold_entry, e._hold_r_nm, e._hold_entry_nm)

    assert load(e, [Z, A, B, HOLD_B, C, D], "T", 2) is None
    after = e.update(*probe, GS, 0.0, True, None, None, 1004.0)
    assert after["act_leg"] == 4 and after["phase"] == "HOLD"
    assert (e._leg_phase, e._hold_entry, e._hold_r_nm, e._hold_entry_nm) == held
    assert guidance(after) == guidance(before)
    assert (after["hold_slot"], after["hold_commit"]) == (4, True)

    # Still holding, and RESUME still exits to the slot after it.
    for t, p in enumerate((outbound, turn_in, past_fix), start=1005):
        e.update(*p, GS, 0.0, True, None, None, float(t))
    assert (e.act_leg, e._leg_phase) == (4, engine.LEGPHASE_TURN_OUT)
    e.handle_command("2 RESUME", (*past_fix, True))
    for t, p in enumerate((turn_out, outbound, turn_in, past_fix), start=1010):
        out = e.update(*p, GS, 0.0, True, None, None, float(t))
    assert (out["act_leg"], out["wp_name"]) == (5, "C")


def test_an_armed_hold_exit_survives_a_re_commit():
    e, _ = _in_the_hold()
    e.handle_command("2 RESUME", (0.0, 0.0, True))
    assert load(e, [Z, A, B, HOLD_B, C], "T", 2) is None
    assert e._hold_exit_requested and e.act_leg == 4


def test_a_new_hold_leg_length_applies_to_the_hold_being_flown():
    # Leg length is read from the route every cycle, so it is not part of
    # the leg: the hold keeps its place and flies the new length.
    e, _ = _in_the_hold()
    longer = wp("B", 0.0, pt="HM", crs=90.0, dst=8.0, turn="R")
    assert load(e, [A, B, longer, C], "T", 2) is None
    assert (e.act_leg, e._leg_phase) == (3, engine.LEGPHASE_OUTBOUND)


def test_a_hold_with_a_new_course_or_turn_is_a_new_leg():
    for changed in (wp("B", 0.0, pt="HM", crs=270.0, dst=5.0, turn="R"),
                    wp("B", 0.0, pt="HM", crs=90.0, dst=5.0, turn="L")):
        e, _ = _in_the_hold()
        assert load(e, [A, B, changed, C], "T", 2) == engine.ACTIVE_LEG_CHANGED_REASON
        assert e.mode == "NONE"


# --- Direct-To ----------------------------------------------------------

def test_a_direct_to_a_plan_fix_is_kept_where_its_fix_is():
    e = active([A, B, C, D], "DTO 3", -0.2, at_lat=0.1)
    fr = (e.from_point.lat, e.from_point.lon)
    before = update(e, 0.0, lat=0.08, t=1001.0)
    # Between the slot before and the fix: a LEG would break, a direct-to
    # was never flown from the slot before.
    assert load(e, [Z, A, B, X, C, D], "T", 2) is None
    after = update(e, 0.0, lat=0.08, t=1002.0)
    assert (e.mode, after["act_leg"], after["state"]) == ("DIRECT", 5, engine.STATE_DIRECT)
    assert (e.from_point.lat, e.from_point.lon) == fr
    assert guidance(after) == guidance(before)

    assert load(e, [Z, A, B, X, D], "T", 3) == engine.ACTIVE_LEG_REMOVED_REASON
    assert e.mode == "NONE"


def test_a_direct_to_a_fix_with_a_hold_on_it_stays_on_the_fix_leg():
    e = active([A, B, HOLD_B, C], "DTO 2", -0.2, at_lat=0.1)
    assert load(e, [Z, A, B, HOLD_B, C], "T", 2) is None
    assert (e.mode, e.act_leg, e.route[e.act_leg - 1].pt) == ("DIRECT", 3, "TF")


def test_a_direct_to_a_repeated_fix_keeps_the_copy_after_the_same_fix():
    e = active([A, B, C, B, D], "DTO 4", 0.3, at_lat=0.1)
    assert load(e, [Z, A, B, C, B, D], "T", 2) is None
    assert e.act_leg == 5


def test_an_off_route_direct_to_is_kept_through_any_commit():
    e = engine.Engine()
    load(e, [A, B], "T", 1)
    assert e.handle_command("1 DTO", (0.1, -0.2, True), ("OFF", 0.3, 0.3, 0))[0] == 1
    to = e.to_point.to_dict()
    assert load(e, [C, D], "T", 2) is None
    assert (e.mode, e.act_leg, e.to_point.to_dict()) == ("DIRECT", 0, to)
    assert update(e, 0.0, lat=0.15, t=1001.0)["wp_name"] == "OFF"


# --- a fly-by turn in progress ------------------------------------------

def _in_the_turn():
    # A-B east, then B-N north-east: a 45 deg fly-by turn at B.
    n = wp("N", 0.5, lat=0.5)
    e = active([A, B, n], "ACT 2", -0.25)
    d_ta = engine.turn_anticipation_nm(GS, 45.0)
    update(e, -(d_ta * 0.5) / 60.0, t=1001.0)
    assert e._turning and e._turn_arc["leg"] == 2
    return e, n


def test_a_turn_in_progress_survives_a_commit_that_keeps_the_next_leg():
    e, n = _in_the_turn()
    arc = dict(e._turn_arc)
    ta = e.turn_anticipations(GS, 0.0)
    assert load(e, [Z, A, B, n], "T", 2) is None
    assert e._turning and e._turn_arc == dict(arc, leg=3)
    assert e.turn_anticipations(GS, 0.0) == [0.0] + ta


def test_a_turn_in_progress_goes_when_the_next_leg_changes():
    e, n = _in_the_turn()
    assert load(e, [A, B, D, n], "T", 2) is None
    assert e.act_leg == 2 and not e._turning and e._turn_arc is None


# --- persistence ----------------------------------------------------------

def test_a_kept_leg_persists_at_its_new_slot():
    e, (turn_out, outbound, _, _) = _in_the_hold()
    load(e, [Z, A, B, HOLD_B, C], "T", 2)
    saved = json.loads(json.dumps(e.to_persisted_dict()))
    assert (saved["act_leg"], saved["seq"], saved["leg_phase"]) == (4, 2, engine.LEGPHASE_OUTBOUND)

    restored = engine.Engine()
    restored.restore_from_dict(saved)
    probe = geo.destination_point(*turn_out, 270.0, 1.0)
    a = e.update(*probe, GS, 0.0, True, None, None, 1010.0)
    b = restored.update(*probe, GS, 0.0, True, None, None, 1010.0)
    assert guidance(b) == guidance(a) and b["act_leg"] == 4 and b["phase"] == "HOLD"


# --- through the bus ----------------------------------------------------

def _plugin_flying(database, tmp_path, route):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    pl.thread._restore()   # no state file: persistence opens
    database.write("GPS_FIX_TYPE", 3)
    database.write("GS", GS)
    database.write("MAGVAR", 0.0)
    write_route(database, route, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", 0.2)
    database.write("FPLCMD", "1 ACT 3")
    pl.thread._run_update_cycle()
    assert database.read("FPLACTLEG")[0] == 3
    return pl


def test_a_commit_through_the_bus_keeps_the_leg_and_republishes_its_slot_at_once(database, tmp_path):
    pl = _plugin_flying(database, tmp_path, [A, B, C, D])
    database.write("FPLMSG", "")
    write_route(database, [Z, A, B, C, D], seq=2)
    # No update cycle called: the commit itself publishes the new slot.
    assert database.read("FPLLDSEQ")[0] == 2
    assert database.read("FPLACTLEG")[0] == 4
    assert database.read("FPLSTATE")[0] == engine.STATE_LEG
    assert database.read("WPNAME")[0] == "C"
    assert database.read("FPLMSG")[0] == ""
    assert json.loads((tmp_path / "flightplan_state.json").read_text())["act_leg"] == 4
    assert pl.thread.engine.act_leg == 4


def test_a_commit_that_drops_the_leg_says_why_after_it_is_loaded(database, tmp_path):
    # pyEfis's FPL page takes an FPLMSG that lands while a commit is still
    # unanswered (FPLLDSEQ behind FPLSEQ) as that commit's refusal (G61).
    _plugin_flying(database, tmp_path, [A, B, C, D])
    seen = []

    def watch(key, value, udata):
        if isinstance(value, tuple):
            seen.append((key, value[0]))

    for key in ("FPLLDSEQ", "FPLMSG"):
        database.callback_add("holdfix0", key, watch, None)
    try:
        write_route(database, [A, B, D], seq=2)
    finally:
        for key in ("FPLLDSEQ", "FPLMSG"):
            database.callback_del("holdfix0", key, watch, None)
    assert seen == [("FPLLDSEQ", 2), ("FPLMSG", engine.ACTIVE_LEG_REMOVED_REASON)]
    assert database.read("FPLSTATE")[0] == engine.STATE_NONE
    assert database.read("FPLACTLEG")[0] == 0


def test_a_to_fix_recoded_fly_over_or_map_is_a_new_leg():
    # Fly-over and MAP change where the fix sequences, so they are part of
    # the leg; the other flags (IAF, FAF, ...) are read every cycle.
    for flags in (engine.FLAG_FLYOVER, engine.FLAG_MAP):
        e = active([A, B, C, D], "ACT 3", 0.2)
        assert load(e, [A, B, wp("C", 0.5, flags=flags), D], "T", 2) == engine.ACTIVE_LEG_CHANGED_REASON
    e = active([A, B, C, D], "ACT 3", 0.2)
    assert load(e, [A, B, wp("C", 0.5, flags=engine.FLAG_FAF), D], "T", 2) is None


def test_a_turned_back_short_watch_moves_with_the_leg():
    # A 174 deg reversal at B is flown as a fly-over, and the closest
    # approach to B is tracked in case the aircraft turns back short of it
    # (TURN2b). The record is keyed by the active slot.
    back_west = wp("W", -0.5, lat=0.05)
    e = active([A, B, back_west], "ACT 2", -0.25)
    update(e, -0.1, t=1001.0)
    key, dist = e._reversal_closest
    assert key[0] == 2
    assert load(e, [Z, A, B, back_west], "T", 2) is None
    assert e._reversal_closest == ((3,) + key[1:], dist)
    assert load(e, [Z, A, B, D], "T", 3) is None
    assert e._reversal_closest is None
