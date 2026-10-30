"""Unit tests for fixgw.plugins.flightplan.engine.Engine -- the flight-plan
navigation engine's pure-Python core (FP2, fix-gateway#23).

Spec: makerplane/briefs/flight_plan_plan.md section 3.3 (the maos-workspace
repo), Appendix C. Bill's ruling of 2026-09-08: CDI scaling is the full
DO-229 lateral behaviour including the approach phase -- there is no
VFR-advisory mode.
"""

import math

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine

from .routes import hold_circuit_points, load

# A hold is built on WGS-84 and its course read on the sphere between the
# points it was built with (AER-4218): within 0.193 deg of the coded course,
# the worst case being here at the equator.
HOLD_CRS_DEG = 0.2


def wp(id, lat, lon, type=engine.TYPE_UNKNOWN, flags=0):
    return engine.Waypoint(id, lat, lon, type, flags=flags)


def nm_to_deg_lon(nm):
    # Along the equator, for building synthetic straight-line routes.
    return nm / geo.distance_nm(0.0, 0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Turn anticipation table (spec section 3.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "gs,delta,expected",
    [
        (120, 90, 0.637),
        (90, 90, 0.477),
        (120, 45, 0.264),
        (150, 120, 1.378),
    ],
)
def test_turn_anticipation_table(gs, delta, expected):
    assert engine.turn_anticipation_nm(gs, delta) == pytest.approx(expected, abs=1e-3)


def test_turn_anticipation_caps_delta_at_120():
    assert engine.turn_anticipation_nm(150, 170) == engine.turn_anticipation_nm(150, 120)


def test_turn_anticipation_floor_and_cap():
    assert engine.turn_anticipation_nm(1, 1) == pytest.approx(0.1)
    assert engine.turn_anticipation_nm(100000, 120) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# A synthetic straight-line 3/4-waypoint track: sequencing, TO/FROM, DTO,
# DTOX, SUSP.
# ---------------------------------------------------------------------------


def straight_route(n=4, spacing_nm=60.04):
    step = nm_to_deg_lon(spacing_nm)
    names = ["A", "B", "C", "D", "E"]
    return [wp(names[i], 0.0, i * step) for i in range(n)]


def test_sequencing_within_turn_anticipation_distance_advances_leg():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    ack, msg = e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert ack == 1
    assert e.mode == "LEG" and e.act_leg == 2

    step = nm_to_deg_lon(60.04)
    b_lon = route[1].lon
    # First cycle also posts the once-per-activation "no accuracy source"
    # message (no integrity_key data supplied here) -- consume it before
    # asserting on sequencing-specific messages below.
    e.update(0.0, 0.0, 120.0, 0.0, True, None, None, 999.0)

    # 0.5 nm before B: straight route -> delta=0 -> d_ta floor 0.1nm -> no seq
    out = e.update(0.0, b_lon - nm_to_deg_lon(0.5), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2
    assert out.get("msg") is None

    # 0.05 nm before B: inside d_ta -> sequence to C
    out = e.update(0.0, b_lon - nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1001.0)
    assert out["act_leg"] == 3
    assert out["wp_from"] == "B"
    assert out["wp_name"] == "C"
    assert out.get("msg") == "SEQ B -> C"


def test_sequencing_abeam_backstop_when_atd_goes_negative():
    # A very large course change (delta capped 120) still sequences once atd
    # goes negative even if the aircraft blew through the turn-anticipation
    # window (e.g. autopilot overshoot) -- the "or when the along-track
    # distance goes negative" backstop.
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(60.04)), wp("C", 5.0, nm_to_deg_lon(60.04))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    # Fly straight past B without ever entering the (small) d_ta window from
    # the previous cycle's position -- simulate a coarse timestep landing
    # just past the waypoint.
    out = e.update(0.0, route[1].lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ B -> C"


def _turn_route(b_flags=0):
    # A -> B east, then a 90 degree left turn to C: 0.637 nm of anticipation
    # at 120 kt if B is flown fly-by.
    b_lon = nm_to_deg_lon(60.04)
    return [wp("A", 0.0, 0.0), wp("B", 0.0, b_lon, flags=b_flags), wp("C", 1.0, b_lon)]


@pytest.mark.parametrize("b_flags,seq_early", [(0, True), (engine.FLAG_FLYOVER, False)])
def test_coded_flyover_fix_is_not_turn_anticipated(b_flags, seq_early):
    # fix-gateway#44: FPLfFLAGS 0x01 outside the FAF..MAP run is fly-over --
    # abeam-only, like the FAF. The fly-by row is the control: same route,
    # same position, inside its 0.637 nm d_ta.
    e = engine.Engine()
    route = _turn_route(b_flags)
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, route[1].lon - nm_to_deg_lon(0.3), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == (3 if seq_early else 2)

    out = e.update(0.0, route[1].lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1001.0)
    assert out["act_leg"] == 3


def test_leg_into_a_hold_at_its_to_fix_is_not_turn_anticipated():
    # fix-gateway#44: TF WINCH then HM WINCH (KSMX RNAV 30 missed). The HM
    # sits on the TO fix, so the bearing to it is a zero-length leg's, not a
    # turn -- the engine used to enter the hold ~0.25 nm early from it.
    # Abeam-only, in both directions, like every other Tier-2 transition.
    e = engine.Engine()
    b_lon = nm_to_deg_lon(20.0)
    route = [
        wp("A", 0.0, 0.0),
        wp("B", 0.0, b_lon),
        hold_wp("B", 0.0, b_lon, crs=270.0, dst=4.0, turn="L"),
    ]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    for i, nm_before in enumerate((0.6, 0.3, 0.1, 0.02)):
        out = e.update(0.0, b_lon - nm_to_deg_lon(nm_before), 120.0, 0.0, True, None, None, 1000.0 + i)
        assert out["act_leg"] == 2, nm_before

    out = e.update(0.0, b_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1010.0)
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ B -> B"


def test_hold_coded_at_the_fix_just_sequenced_starts_its_entry_at_once():
    # fix-gateway#44, exposed once the leg into it stopped sequencing early:
    # TF B then HM B means B is already reached when the HM goes active. It
    # used to start on a zero-length leg from B to B, whose bearing is
    # meaningless (the FP8 replay showed FPLCRS 348.1 with outbound 301.4).
    # The entry is picked from the TF leg's own track into B: 090, the
    # outbound course of a 270 left hold, is a teardrop (fix-gateway#45),
    # 30 deg off the outbound course toward the holding side (south).
    e = engine.Engine()
    b_lon = nm_to_deg_lon(20.0)
    route = [
        wp("A", 0.0, 0.0),
        wp("B", 0.0, b_lon),
        hold_wp("B", 0.0, b_lon, crs=270.0, dst=4.0, turn="L"),
    ]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, b_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert (e._leg_phase, e._hold_entry) == (engine.LEGPHASE_ENTRY, engine.HOLD_ENTRY_TEARDROP)
    assert out["crs"] == pytest.approx(120.0, abs=HOLD_CRS_DEG)
    assert out["xtk"] == pytest.approx(-0.01 * math.sin(math.radians(30.0)), abs=1e-4)
    assert out.get("phase") == "HOLD"


def test_hold_with_its_own_inbound_leg_still_flies_to_the_fix_first():
    # The coincident-fix start above must not apply when the hold slot is
    # the leg to its fix (A -> HM HOLD): there the fix is not yet reached.
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    out = e.update(0.0, -nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2
    assert e._leg_phase is None


def test_last_waypoint_never_sequences_flips_to_from():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    c_lon = route[2].lon

    out = e.update(0.0, c_lon - nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3 and out["tf"] == engine.TF_TO

    out = e.update(0.0, c_lon + nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1001.0)
    assert out["act_leg"] == 3  # no advance -- there is no slot 4
    assert out["tf"] == engine.TF_FROM
    assert out.get("msg") is None


def test_direct_to_slot_resumes_normal_sequencing_on_arrival():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    ack, msg = e.handle_command("1 DTO 2", (0.0, -nm_to_deg_lon(5), True))
    assert ack == 1
    assert e.mode == "DIRECT" and e.act_leg == 2

    b_lon = route[1].lon
    out = e.update(0.0, b_lon - nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert out["state"] == engine.STATE_LEG  # DIRECT -> LEG once resumed into the plan


def test_off_route_direct_to_leaves_actleg_zero_plan_stays_loaded():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    ack, msg = e.handle_command("1 DTO", (0.0, 0.0, True), dto_staged=("XYZ", 9.0, 9.0, engine.TYPE_FIX))
    assert ack == 1
    assert e.mode == "DIRECT"
    assert e.act_leg == 0
    assert e.count == 3  # plan still loaded


def test_dto_bare_matching_staged_slot_behaves_as_dto_k():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    c = route[2]
    ack, msg = e.handle_command(
        "1 DTO", (0.0, 0.0, True), dto_staged=(c.id, c.lat + 0.00001, c.lon - 0.00002, engine.TYPE_UNKNOWN)
    )
    assert ack == 1
    assert e.act_leg == 3
    assert e.mode == "DIRECT"


def test_dtox_reactivates_nearest_leg_by_cross_track():
    e = engine.Engine()
    route = straight_route(4)  # A B C D
    load(e, route, "T", 1)
    # Aircraft abeam the B-C leg (slightly north of it).
    mid_lon = (route[1].lon + route[2].lon) / 2.0
    ack, msg = e.handle_command("1 DTOX", (0.01, mid_lon, True))
    assert ack == 1
    assert e.mode == "LEG"
    assert e.act_leg == 3  # TO = C, the leg B->C
    assert e.from_point.id == "B"


def test_dtox_falls_back_to_nearest_waypoint_when_no_leg_window_contains_aircraft():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    # Aircraft far behind A, off every leg's along-track window.
    ack, msg = e.handle_command("1 DTOX", (0.0, -nm_to_deg_lon(50), True))
    assert ack == 1
    assert e.act_leg == 1  # nearest waypoint is A (slot 1)
    assert e.from_point.lat == 0.0 and e.from_point.lon == pytest.approx(-nm_to_deg_lon(50))


def test_susp_freezes_sequencing_and_dtk():
    e = engine.Engine()
    route = straight_route(3)
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.handle_command("2 SUSP", (0.0, 0.0, True))
    assert e.suspended is True

    b_lon = route[1].lon
    # Well within sequencing distance, but suspended -- must not sequence.
    out = e.update(0.0, b_lon - nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2
    assert out["state"] == engine.STATE_SUSP

    ack, msg = e.handle_command("3 RESUME", (0.0, 0.0, True))
    assert ack == 3
    assert e.suspended is False


# ---------------------------------------------------------------------------
# CDI scale / flight phase (ENR/TERM, no approach marked)
# ---------------------------------------------------------------------------


def test_enr_term_transition_at_30nm_from_airport_departure_or_destination():
    e = engine.Engine()
    route = [
        wp("KSBA", 34.42621, -119.84037, type=engine.TYPE_AIRPORT),
        wp("KSMX", 34.89892, -120.45758, type=engine.TYPE_AIRPORT),
    ]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))

    # Far from both -- ENR/2.0.
    out = e.update(30.0, -100.0, 120.0, 0.0, True, None, None, 1000.0)
    assert out["phase"] == "ENR" and out["cdiscale"] == pytest.approx(2.0)

    # Within 30nm of the destination -- TERM/1.0.
    near_lat, near_lon = geo.destination_point(route[1].lat, route[1].lon, 0.0, 10.0)
    out = e.update(near_lat, near_lon, 120.0, 0.0, True, None, None, 1001.0)
    assert out["phase"] == "TERM" and out["cdiscale"] == pytest.approx(1.0)


def test_no_term_scaling_when_endpoint_not_typed_airport():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(5))]  # neither typed airport
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, nm_to_deg_lon(4.9), 120.0, 0.0, True, None, None, 1000.0)
    assert out["phase"] == "ENR"


# ---------------------------------------------------------------------------
# Full DO-229 approach behaviour
# ---------------------------------------------------------------------------


def approach_route():
    # IAF -> FAF -> MAP -> MAHP, 5nm IAF-FAF, 5nm FAF-MAP, 2nm MAP-MAHP.
    return [
        wp("IAF", 0.0, 0.0, type=engine.TYPE_FIX, flags=engine.FLAG_IAF),
        wp("FAF", 0.0, nm_to_deg_lon(5.0), type=engine.TYPE_FIX, flags=engine.FLAG_FAF),
        wp("MAP", 0.0, nm_to_deg_lon(10.0), type=engine.TYPE_MAPPOINT, flags=engine.FLAG_MAP),
        wp("MAHP", 0.0, nm_to_deg_lon(12.0), type=engine.TYPE_FIX, flags=engine.FLAG_MAHP),
    ]


def test_approach_armed_within_30nm_of_map_with_active_leg_at_or_before_faf():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))  # TO = FAF, before it
    out = e.update(0.0, -nm_to_deg_lon(15), 120.0, 0.0, True, None, None, 1000.0)  # 25nm from MAP
    assert out["apr"] == engine.APR_ARMED
    assert out["phase"] == "TERM"
    assert out["cdiscale"] == pytest.approx(1.0)


def test_approach_not_armed_beyond_30nm_of_map():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, -nm_to_deg_lon(40), 120.0, 0.0, True, None, None, 1000.0)  # 45nm from MAP
    assert out["apr"] == engine.APR_NONE


def test_approach_ramp_linear_1_0_to_0_3_over_2nm_before_faf():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    faf_lon = route[1].lon

    # Exactly 2nm before the FAF: ramp starts, scale == 1.0, apr active.
    out = e.update(0.0, faf_lon - nm_to_deg_lon(2.0), 120.0, 0.0, True, None, None, 1000.0)
    assert out["apr"] == engine.APR_ACTIVE
    assert out["phase"] == "LNAV"
    assert out["cdiscale"] == pytest.approx(1.0, abs=1e-3)

    # 1nm before the FAF: spec's explicit assertion -- scale == 0.65.
    out = e.update(0.0, faf_lon - nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1001.0)
    assert out["cdiscale"] == pytest.approx(0.65, abs=1e-3)

    # Just before the FAF itself: scale approaches 0.3.
    out = e.update(0.0, faf_lon - nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1002.0)
    assert out["cdiscale"] == pytest.approx(0.3, abs=0.01)


def test_scale_holds_0_3_from_faf_to_map_fly_over_no_turn_anticipation():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    faf_lon = route[1].lon

    # Just past the FAF -- abeam rule sequences immediately (fly-over, no
    # turn-anticipation window), even though the course change to MAP is 0
    # here and would floor to 0.1nm anyway; use a real course change to prove
    # it truly ignores turn anticipation.
    out = e.update(0.0, faf_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3  # sequenced onto the FAF->MAP leg
    assert out["cdiscale"] == pytest.approx(0.3, abs=1e-6)
    assert out["apr"] == engine.APR_ACTIVE


def test_map_no_auto_sequence_auto_suspend_to_from_scale_stays_0_3():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))  # already on the FAF->MAP leg
    map_lon = route[2].lon

    out = e.update(0.0, map_lon - nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert out["tf"] == engine.TF_TO
    assert out["state"] == engine.STATE_DIRECT or out["state"] == engine.STATE_LEG

    out = e.update(0.0, map_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1001.0)
    assert out["act_leg"] == 3  # no advance past the MAP
    assert out["tf"] == engine.TF_FROM
    assert out["state"] == engine.STATE_SUSP
    assert out["cdiscale"] == pytest.approx(0.3)
    assert out["apr"] == engine.APR_ACTIVE


def test_resume_at_map_sets_missed_term_scale_and_sequences_to_mahp():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    map_lon = route[2].lon
    e.update(0.0, map_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert e._map_suspended is True

    ack, msg = e.handle_command("2 RESUME", (0.0, map_lon + nm_to_deg_lon(0.01), True))
    assert ack == 2
    assert msg == "SEQ MAP -> MAHP"
    assert e.act_leg == 4
    assert e.suspended is False

    out = e.update(0.0, map_lon + nm_to_deg_lon(0.5), 120.0, 0.0, True, None, None, 1001.0)
    assert out["apr"] == engine.APR_MISSED
    assert out["cdiscale"] == pytest.approx(1.0)  # TERM
    assert out["wp_name"] == "MAHP"
    assert out["wp_from"] == "MAP"


def test_resume_at_map_with_no_mahp_posts_no_missed_approach_message():
    e = engine.Engine()
    route = approach_route()[:3]  # no MAHP slot after the MAP
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    map_lon = route[2].lon
    e.update(0.0, map_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)

    ack, msg = e.handle_command("2 RESUME", (0.0, map_lon + nm_to_deg_lon(0.01), True))
    assert ack == 2
    assert msg == "NO MISSED APPROACH LEGS"
    assert e.act_leg == 3  # stays FROM the MAP, no slot to advance to

    out = e.update(0.0, map_lon + nm_to_deg_lon(0.5), 120.0, 0.0, True, None, None, 1001.0)
    # "guidance stays FROM the MAP" means the extended FAF->MAP course keeps
    # being flown (TF=FROM, WPNAME=MAP) -- the leg's own endpoints (FROM=FAF)
    # are unchanged, since there is nothing to sequence onto.
    assert out["wp_from"] == "FAF"
    assert out["wp_name"] == "MAP"
    assert out["tf"] == engine.TF_FROM
    assert out["apr"] == engine.APR_MISSED


def test_off_route_direct_to_drops_approach_back_to_armed_or_none():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    faf_lon = route[1].lon
    out = e.update(0.0, faf_lon - nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)
    assert out["apr"] == engine.APR_ACTIVE

    e.handle_command("2 DTO", (0.0, 0.0, True), dto_staged=("OFF", 9.0, 9.0, engine.TYPE_FIX))
    out = e.update(0.0, faf_lon - nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1001.0)
    assert out["apr"] in (engine.APR_ARMED, engine.APR_NONE)
    assert out["apr"] != engine.APR_ACTIVE


def test_act_before_faf_drops_approach_back_to_armed():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    faf_lon = route[1].lon
    e.update(0.0, faf_lon - nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)
    assert e.apr == engine.APR_ACTIVE

    e.handle_command("2 ACT 1", (0.0, 0.0, True))  # IAF, before the FAF
    out = e.update(0.0, 0.0, 120.0, 0.0, True, None, None, 1001.0)
    assert out["apr"] == engine.APR_ARMED


def test_direct_to_between_faf_and_map_does_not_activate_approach_scaling():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    faf_lon, map_lon = route[1].lon, route[2].lon
    # Direct-to a synthetic fix between the FAF and the MAP.
    mid = wp("MID", 0.0, (faf_lon + map_lon) / 2.0, type=engine.TYPE_FIX)
    route.insert(2, mid)
    load(e, route, "T", 2)  # re-load with MID inserted at slot 3

    ack, msg = e.handle_command("1 DTO 3", (0.0, 0.0, True))  # DTO to MID (slot 3)
    assert ack == 1
    out = e.update(0.0, mid.lon, 120.0, 0.0, True, None, None, 1000.0)
    assert out["apr"] != engine.APR_ACTIVE


def test_scale_manual_ceiling_is_minimum_of_phase_and_manual():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(200))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.handle_command("2 SCALE 0.3", (0.0, 0.0, True))

    out = e.update(50.0, -100.0, 120.0, 0.0, True, None, None, 1000.0)  # ENR normally 2.0
    assert out["cdiscale"] == pytest.approx(0.3)
    assert out["phase"] == "0.30 NM"

    e.handle_command("3 SCALE AUTO", (0.0, 0.0, True))
    out = e.update(50.0, -100.0, 120.0, 0.0, True, None, None, 1001.0)
    assert out["cdiscale"] == pytest.approx(2.0)
    assert out["phase"] == "ENR"


def test_scale_manual_ceiling_does_not_widen_a_tighter_phase_scale():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))  # on the fly-over FAF->MAP leg, 0.3nm
    e.handle_command("2 SCALE 2.0", (0.0, 0.0, True))
    out = e.update(0.0, route[1].lon + nm_to_deg_lon(0.5), 120.0, 0.0, True, None, None, 1000.0)
    assert out["cdiscale"] == pytest.approx(0.3)  # min(0.3, 2.0)


# ---------------------------------------------------------------------------
# Integrity gate
# ---------------------------------------------------------------------------


def test_fix_type_below_3d_fails_guidance():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(5))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, nm_to_deg_lon(1), 120.0, 0.0, True, False, None, 1000.0)
    assert out["fail"] is True


def test_accuracy_above_hal_sets_integ_false_bad_loi_and_reverts_approach():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    faf_lon = route[1].lon
    out = e.update(0.0, faf_lon - nm_to_deg_lon(1.0), 120.0, 0.0, True, True, 0.05, 1000.0)
    assert out["apr"] == engine.APR_ACTIVE  # good accuracy: still active

    # LNAV HAL is 0.3nm; 0.5nm accuracy exceeds it.
    out = e.update(0.0, faf_lon - nm_to_deg_lon(1.0), 120.0, 0.0, True, True, 0.5, 1001.0)
    assert out["integ"] is False
    assert out["bad"] is True
    assert out["phase"] == "LOI"
    assert out["apr"] == engine.APR_ARMED
    assert out["cdiscale"] == pytest.approx(1.0)


def test_no_accuracy_source_posts_no_integrity_data_once_per_activation():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(5))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))

    out = e.update(0.0, nm_to_deg_lon(1), 120.0, 0.0, True, None, None, 1000.0)
    assert out["integ"] is True
    assert out.get("msg") == "NO INTEGRITY DATA"

    out = e.update(0.0, nm_to_deg_lon(2), 120.0, 0.0, True, None, None, 1001.0)
    assert out.get("msg") is None  # not spammed every cycle


# ---------------------------------------------------------------------------
# WPETE / staleness
# ---------------------------------------------------------------------------


def test_wpete_bad_under_30kt():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(5))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, 0.0, 20.0, 0.0, True, None, None, 1000.0)
    assert out["wp_ete_bad"] is True

    out = e.update(0.0, 0.0, 90.0, 0.0, True, None, None, 1001.0)
    assert out["wp_ete_bad"] is False


def test_outputs_fail_after_5s_of_bad_position():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(5))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))

    out = e.update(0.0, 0.0, 120.0, 0.0, True, None, None, 1000.0)
    assert out.get("fail", False) is False

    out = e.update(0.0, 0.0, 120.0, 0.0, False, None, None, 1004.0)  # 4s stale
    assert out.get("fail", False) is False

    out = e.update(0.0, 0.0, 120.0, 0.0, False, None, None, 1006.0)  # 6s stale
    assert out["fail"] is True


# ---------------------------------------------------------------------------
# Commands: parse / ack / reject
# ---------------------------------------------------------------------------


def test_command_ack_and_reject_reasons():
    e = engine.Engine()
    assert e.handle_command("1 ACT 1", (0.0, 0.0, True)) == (-1, "NO PLAN")

    load(e, [wp("A", 0, 0), wp("B", 0, 1)], "T", 1)
    assert e.handle_command("2 ACT 9", (0.0, 0.0, True)) == (-2, "BAD SLOT")
    assert e.handle_command("3 DTO 2", (0.0, 0.0, False)) == (-3, "NO POSITION")
    assert e.handle_command("x ACT 1", (0.0, 0.0, True)) == (-1, "PARSE")
    assert e.handle_command("4 FROB", (0.0, 0.0, True)) == (-4, "PARSE")
    assert e.handle_command("5 SCALE 9.9", (0.0, 0.0, True)) == (-5, "PARSE")

    ack, msg = e.handle_command("6 ACT 1", (0.0, 0.0, True))
    assert ack == 6 and msg == ""


def test_unknown_verb_rejected_never_ignored():
    e = engine.Engine()
    load(e, [wp("A", 0, 0)], "T", 1)
    ack, msg = e.handle_command("1 BOGUS arg", (0.0, 0.0, True))
    assert ack == -1
    assert msg == "PARSE"


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


def test_persistence_round_trip_including_approach_state():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "RNAV1", 3)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    e.handle_command("2 SCALE 1.0", (0.0, 0.0, True))
    snapshot = e.to_persisted_dict()

    e2 = engine.Engine()
    e2.restore_from_dict(snapshot)
    assert e2.route_name == "RNAV1"
    assert e2.seq == 3
    assert e2.act_leg == 3
    assert e2.mode == "LEG"
    assert e2.scale_manual == pytest.approx(1.0)
    assert [w.id for w in e2.route] == [w.id for w in route]
    assert e2.to_point.id == "MAP"
    assert e2.from_point.id == "FAF"


def test_persistence_round_trip_preserves_missed_approach_state():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    map_lon = route[2].lon
    e.update(0.0, map_lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    e.handle_command("2 RESUME", (0.0, map_lon + nm_to_deg_lon(0.01), True))

    snapshot = e.to_persisted_dict()
    e2 = engine.Engine()
    e2.restore_from_dict(snapshot)
    out = e2.update(0.0, map_lon + nm_to_deg_lon(0.5), 120.0, 0.0, True, None, None, 1001.0)
    assert out["apr"] == engine.APR_MISSED


# ---------------------------------------------------------------------------
# Budget: one cycle with 100 slots (PA3 raised the block from 50 to 100)
# ---------------------------------------------------------------------------


def test_update_cycle_budget_with_100_slots():
    import time

    e = engine.Engine()
    route = [wp(f"W{i}", 0.0, nm_to_deg_lon(i * 5)) for i in range(100)]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 50", (0.0, 0.0, True))

    start = time.perf_counter()
    for i in range(50):
        e.update(0.0, nm_to_deg_lon(49 * 5 + 0.1), 120.0, 0.0, True, None, None, 1000.0 + i)
    elapsed_per_cycle = (time.perf_counter() - start) / 50
    assert elapsed_per_cycle < 0.010  # generous 10ms CI ceiling (spec target 2ms)


# ---------------------------------------------------------------------------
# PA3: leg model -- new per-slot fields and block-level provenance
# ---------------------------------------------------------------------------


def test_leg_fields_round_trip_through_waypoint_dict():
    w = engine.Waypoint(
        "CFFIX", 1.0, 2.0, type=engine.TYPE_FIX, pt="CF", crs=270.0, dst=4.2,
        alt="B2900,4000", spd=180, sgi=3, flags=engine.FLAG_FAF,
    )
    w2 = engine.Waypoint.from_dict(w.to_dict())
    assert w2.pt == "CF" and w2.crs == 270.0 and w2.dst == 4.2
    assert w2.alt == "B2900,4000" and w2.spd == 180
    assert w2.sgi == 3 and w2.flags == engine.FLAG_FAF


def test_waypoint_defaults_to_enroute_tf_point():
    # A bare point (no procedure data) is still a valid leg -- FPLfPT
    # defaults to "TF", the pre-PA3 implicit behaviour.
    w = engine.Waypoint("A", 0.0, 0.0)
    assert w.pt == "TF"
    assert w.sgi == 0  # unassigned until the route's segment table says otherwise (RS1)
    assert w.flags == 0


def test_load_route_carries_block_level_provenance():
    e = engine.Engine()
    load(e, [wp("A", 0.0, 0.0)], "T", 1, aprtype="ILS", dbcyc="2609")
    assert e.aprtype == "ILS"
    assert e.dbcyc == "2609"


def test_persistence_round_trip_preserves_leg_fields_and_provenance():
    e = engine.Engine()
    route = approach_route()
    load(e, route, "RNAV1", 3, aprtype="ILS", dbcyc="2609")
    snapshot = e.to_persisted_dict()

    e2 = engine.Engine()
    e2.restore_from_dict(snapshot)
    assert e2.aprtype == "ILS" and e2.dbcyc == "2609"
    assert [w.flags for w in e2.route] == [w.flags for w in route]


# ---------------------------------------------------------------------------
# PA4: whole-procedure rejection of unsupported leg types (guardrail 1) --
# the most important test in this item, per the brief, is negative.
# ---------------------------------------------------------------------------


def test_unsupported_leg_reason_names_terminator_and_ident():
    # FC (track from a fix, fixed distance) is still outside PT_SUPPORTED
    # even after PA13 -- unlike RF/HM/etc, nothing phases it in.
    route = [wp("A", 0.0, 0.0), engine.Waypoint("FCFIX", 0.0, nm_to_deg_lon(5.0), pt="FC")]
    assert engine.unsupported_leg_reason(route) == "UNSUPP FC FCFIX"


def test_unsupported_leg_reason_none_for_an_all_supported_route():
    route = [wp("A", 0.0, 0.0), engine.Waypoint("B", 0.0, nm_to_deg_lon(5.0), pt="CF")]
    assert engine.unsupported_leg_reason(route) is None


def test_load_route_rejects_whole_route_containing_an_unsupported_terminator():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), engine.Waypoint("FCFIX", 0.0, nm_to_deg_lon(5.0), pt="FC")]
    with pytest.raises(engine.RouteRejected) as exc:
        load(e, route, "BAD", 2)
    assert exc.value.reason == "UNSUPP FC FCFIX"
    assert e.count == 0
    assert e.route_name == ""


def test_load_route_rejects_hold_with_no_turn_direction_coded():
    # HM is Tier 2 (PA13) but still needs its type-specific fields --
    # guardrail 1 extends to "supported type, incomplete data" the same
    # way it covers a wholly unrecognized terminator (never invent a turn
    # direction the data didn't carry).
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), engine.Waypoint("HOLD", 0.0, nm_to_deg_lon(5.0), pt="HM", crs=90.0, dst=4.0)]
    with pytest.raises(engine.RouteRejected) as exc:
        load(e, route, "BAD", 2)
    assert exc.value.reason == "BADTURN HM HOLD"
    assert e.count == 0
    assert e.route_name == ""


def test_load_route_rejection_leaves_previously_loaded_route_and_activation_untouched():
    e = engine.Engine()
    load(e, straight_route(3), "GOOD", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))

    bad = [wp("A", 0.0, 0.0), engine.Waypoint("ARC", 0.0, nm_to_deg_lon(5.0), pt="RF")]
    with pytest.raises(engine.RouteRejected):
        load(e, bad, "BAD", 2)

    assert e.route_name == "GOOD"
    assert e.seq == 1
    assert e.act_leg == 2
    assert e.mode == "LEG"


@pytest.mark.parametrize("pt", ["IF", "TF", "CF", "DF", "VA", "VM", "FM", "VI"])
def test_every_supported_leg_type_loads_without_rejection(pt):
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), engine.Waypoint("B", 0.0, nm_to_deg_lon(5.0), pt=pt, crs=90.0)]
    load(e, route, "T", 1)  # must not raise
    assert e.count == 2


# ---------------------------------------------------------------------------
# PA4: CF (course to fix) -- the one Tier-1 terminator that flies a
# published course rather than the bearing from the previous fix.
# ---------------------------------------------------------------------------


def test_cf_leg_flies_published_course_not_bearing_from_previous_fix():
    e = engine.Engine()
    route = [
        wp("A", 0.0, -1.0),  # well off the CF leg's north-south course line
        engine.Waypoint("CFFIX", 0.0, 0.0, type=engine.TYPE_FIX, pt="CF", crs=0.0),  # fly 000 into the fix
    ]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, -1.0, True))

    # Aircraft south of the fix, exactly on the published course's line
    # (lon 0) -- not on the great-circle bearing from A, which runs east.
    out = e.update(-0.5, 0.0, 120.0, 0.0, True, None, None, 1000.0)
    assert out["xtk"] == pytest.approx(0.0, abs=1e-6)
    assert out["crs"] == pytest.approx(0.0, abs=0.1)
    assert out["tf"] == engine.TF_TO


def test_cf_leg_course_is_converted_from_magnetic_with_magvar():
    e = engine.Engine()
    route = [
        wp("A", 0.0, -1.0),
        engine.Waypoint("CFFIX", 0.0, 0.0, type=engine.TYPE_FIX, pt="CF", crs=10.0),  # 010 magnetic
    ]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, -1.0, True))
    # 10 deg east variation: true course = magnetic - magvar = 0 (due north)
    out = e.update(-0.5, 0.0, 120.0, 10.0, True, None, None, 1000.0)
    assert out["xtk"] == pytest.approx(0.0, abs=1e-6)
    assert out["crs"] == pytest.approx(10.0, abs=0.1)  # displayed back out as magnetic


# ---------------------------------------------------------------------------
# PA4: vector legs (VA/VM/FM/VI) -- SUSP annunciated VECTORS, guardrail 4.
# ---------------------------------------------------------------------------


def vector_route():
    return [
        wp("RW33", 0.0, 0.0, type=engine.TYPE_AIRPORT),
        engine.Waypoint("VEC", 0.0, nm_to_deg_lon(5.0), type=engine.TYPE_MAPPOINT, pt="VA", crs=330.0),
        wp("ENRTRY", 0.0, nm_to_deg_lon(25.0), type=engine.TYPE_FIX),
    ]


def test_activating_a_vector_leg_suspends_and_annunciates_vectors_no_invented_course():
    e = engine.Engine()
    route = vector_route()
    load(e, route, "T", 1)
    ack, msg = e.handle_command("1 ACT 2", (0.0, 0.0, True))
    assert ack == 1

    out = e.update(0.0, nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)
    assert out["state"] == engine.STATE_SUSP
    assert out["phase"] == "VECTORS"
    assert out["crs"] == 0.0 and out["xtk"] == 0.0 and out["tf"] == engine.TF_OFF
    assert out["wp_name"] == "VEC"
    assert e.suspended is True


def test_sequencing_onto_a_vector_leg_mid_flight_also_suspends():
    e = engine.Engine()
    route = straight_route(2)
    route.append(engine.Waypoint("VEC", 0.0, route[1].lon + nm_to_deg_lon(60.04), pt="VM", crs=270.0))
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.update(0.0, 0.0, 120.0, 0.0, True, None, None, 999.0)

    b_lon = route[1].lon
    out = e.update(0.0, b_lon - nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert out["state"] == engine.STATE_SUSP
    assert out["phase"] == "VECTORS"


def test_resume_from_vectors_sequences_onto_next_leg_direct_from_aircraft_position():
    e = engine.Engine()
    route = vector_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.update(0.0, nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)
    assert e.suspended is True

    ack, msg = e.handle_command("2 RESUME", (1.0, nm_to_deg_lon(6.0), True))
    assert ack == 2
    assert msg == "RESUME NAV -> ENRTRY"
    assert e.act_leg == 3
    assert e.mode == "LEG"
    assert e.suspended is False
    assert e.from_point.lat == 1.0 and e.from_point.lon == pytest.approx(nm_to_deg_lon(6.0))
    assert e.to_point.id == "ENRTRY"


def test_resume_from_vectors_with_no_next_leg_stays_suspended():
    e = engine.Engine()
    route = vector_route()[:2]  # nothing after the vector leg
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.update(0.0, nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)

    ack, msg = e.handle_command("2 RESUME", (0.0, nm_to_deg_lon(1.0), True))
    assert ack == 2
    assert msg == "NO NEXT LEG"
    assert e.act_leg == 2
    assert e.suspended is True  # nothing safe to fly toward -- stay suspended


def test_resume_from_vectors_requires_position():
    e = engine.Engine()
    route = vector_route()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.update(0.0, nm_to_deg_lon(1.0), 120.0, 0.0, True, None, None, 1000.0)

    ack, msg = e.handle_command("2 RESUME", (0.0, 0.0, False))
    assert ack == -2
    assert msg == "NO POSITION"
    assert e.suspended is True


# ---------------------------------------------------------------------------
# PA13 -- Tier-2 leg types: RF/AF arcs, CA/FA altitude-terminated legs,
# HM/HF/HA holds, PI procedure turns.
# ---------------------------------------------------------------------------

CTR = (34.0, -120.0)  # a real-ish arc center, away from the (0,0) "unset" sentinel


def arc_wp(id, lat, lon, radius_nm, turn, ctrlat=CTR[0], ctrlon=CTR[1]):
    return engine.Waypoint(id, lat, lon, pt="RF", dst=radius_nm, turn=turn, ctrlat=ctrlat, ctrlon=ctrlon)


def hold_wp(id, lat, lon, crs, dst, turn, pt="HM", alt=""):
    return engine.Waypoint(id, lat, lon, pt=pt, crs=crs, dst=dst, turn=turn, alt=alt)


# --- Load-time validation: guardrail 1 extends to incomplete Tier-2 data ---


def test_tier2_happy_path_routes_all_load_without_rejection():
    to_lat, to_lon = geo.destination_point(*CTR, 90.0, 10.0)
    a_lat, a_lon = geo.destination_point(*CTR, 0.0, 10.0)
    cases = {
        "RF": [wp("A", a_lat, a_lon), arc_wp("ARC", to_lat, to_lon, 10.0, "R")],
        "AF": [wp("A", a_lat, a_lon), arc_wp("ARC", to_lat, to_lon, 10.0, "L")],
        "CA": [wp("A", 0.0, 0.0), engine.Waypoint("LVL", 0.1, 0.1, pt="CA", crs=90.0, alt="+3500")],
        "FA": [wp("A", 0.0, 0.0), engine.Waypoint("FIX", 0.1, 0.1, pt="FA", crs=90.0, alt="+3500")],
        "HM": [wp("A", 0.0, 0.0), hold_wp("HOLD", 0.0, 1.0, crs=90.0, dst=5.0, turn="R", pt="HM")],
        "HF": [wp("A", 0.0, 0.0), hold_wp("HOLD", 0.0, 1.0, crs=90.0, dst=5.0, turn="L", pt="HF")],
        "HA": [wp("A", 0.0, 0.0), hold_wp("HOLD", 0.0, 1.0, crs=90.0, dst=5.0, turn="R", pt="HA", alt="+3000")],
        "PI": [wp("A", 0.0, 0.0), hold_wp("PT", 0.0, 1.0, crs=250.0, dst=10.0, turn="R", pt="PI")],
    }
    for label, route in cases.items():
        assert engine.unsupported_leg_reason(route) is None, label


@pytest.mark.parametrize(
    "leg,expected_reason",
    [
        (arc_wp("ARC", 0.0, 1.0, 10.0, turn=""), "BADTURN"),
        (arc_wp("ARC", 0.0, 1.0, 0.0, turn="R"), "BADRADIUS"),
        (engine.Waypoint("ARC", 0.0, 1.0, pt="RF", dst=10.0, turn="R", ctrlat=0.0, ctrlon=0.0), "BADCENTER"),
        (engine.Waypoint("LVL", 0.1, 0.1, pt="CA", crs=90.0, alt=""), "BADALT"),
        (engine.Waypoint("LVL", 0.1, 0.1, pt="CA", crs=90.0, alt="garbage"), "BADALT"),
        (engine.Waypoint("FIX", 0.1, 0.1, pt="FA", crs=90.0, alt=""), "BADALT"),
        (hold_wp("HOLD", 0.0, 1.0, crs=90.0, dst=5.0, turn="", pt="HM"), "BADTURN"),
        (hold_wp("HOLD", 0.0, 1.0, crs=90.0, dst=0.0, turn="R", pt="HF"), "BADLEGLEN"),
        (hold_wp("HOLD", 0.0, 1.0, crs=90.0, dst=5.0, turn="R", pt="HA", alt=""), "BADALT"),
        (hold_wp("PT", 0.0, 1.0, crs=250.0, dst=10.0, turn="", pt="PI"), "BADTURN"),
        (hold_wp("PT", 0.0, 1.0, crs=250.0, dst=0.0, turn="R", pt="PI"), "BADLEGLEN"),
    ],
)
def test_tier2_leg_missing_required_field_rejects_whole_route(leg, expected_reason):
    route = [wp("A", 0.0, 0.0), leg]
    reason = engine.unsupported_leg_reason(route)
    assert reason is not None and reason.startswith(expected_reason)


# --- RF/AF arcs: real circular geometry, not a straight-line trick ---------


def test_arc_geometry_xtk_sign_matches_turn_direction_atd_independent_of_radial_distance():
    e = engine.Engine()
    to_wp_r = arc_wp("ARC", *geo.destination_point(*CTR, 90.0, 10.0), 10.0, "R")

    on_lat, on_lon = geo.destination_point(*CTR, 45.0, 10.0)
    _, atd_on, xtk_on, dtk_on = e._arc_geometry(to_wp_r, on_lat, on_lon)
    assert xtk_on == pytest.approx(0.0, abs=1e-9)
    assert atd_on == pytest.approx(7.854, abs=1e-3)
    assert dtk_on == pytest.approx(135.0)

    out_lat, out_lon = geo.destination_point(*CTR, 45.0, 11.0)  # 1nm outside the arc
    _, atd_out, xtk_out, _ = e._arc_geometry(to_wp_r, out_lat, out_lon)
    assert xtk_out == pytest.approx(-1.0, abs=1e-3)  # outside a right (CW) turn is left of course
    assert atd_out == pytest.approx(atd_on, abs=1e-6)  # progress is angular, not radial

    in_lat, in_lon = geo.destination_point(*CTR, 45.0, 9.0)  # 1nm inside
    _, _, xtk_in, _ = e._arc_geometry(to_wp_r, in_lat, in_lon)
    assert xtk_in == pytest.approx(1.0, abs=1e-3)  # inside a right turn is right of course

    to_wp_l = arc_wp("ARC", *geo.destination_point(*CTR, 90.0, 10.0), 10.0, "L")
    _, _, xtk_out_l, _ = e._arc_geometry(to_wp_l, out_lat, out_lon)
    assert xtk_out_l == pytest.approx(1.0, abs=1e-3)  # sign flips for the opposite turn direction


def test_arc_sequences_to_next_leg_exactly_at_exit_radial():
    e = engine.Engine()
    a_lat, a_lon = geo.destination_point(*CTR, 0.0, 10.0)
    to_lat, to_lon = geo.destination_point(*CTR, 90.0, 10.0)
    route = [wp("A", a_lat, a_lon), arc_wp("ARCFIX", to_lat, to_lon, 10.0, "R"), wp("B", *CTR)]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (a_lat, a_lon, True))

    before_lat, before_lon = geo.destination_point(*CTR, 89.9, 10.0)
    out = e.update(before_lat, before_lon, 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2
    assert out["wp_dis"] == pytest.approx(10.0 * math.radians(0.1), abs=1e-4)

    after_lat, after_lon = geo.destination_point(*CTR, 90.1, 10.0)
    out2 = e.update(after_lat, after_lon, 120.0, 0.0, True, None, None, 1001.0)
    assert out2["act_leg"] == 3
    assert out2.get("msg") == "SEQ ARCFIX -> B"


# --- CA/FA: altitude-terminated, never a distance guess --------------------


def test_ca_leg_holds_course_and_terminates_only_on_altitude():
    e = engine.Engine()
    lvl_lat, lvl_lon = geo.destination_point(0.0, 0.0, 90.0, 10.0)
    route = [wp("A", 0.0, 0.0), engine.Waypoint("CALVL", lvl_lat, lvl_lon, pt="CA", crs=90.0, alt="+3000"), wp("B", 0.0, nm_to_deg_lon(30.0))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))

    mid_lat, mid_lon = geo.destination_point(0.0, 0.0, 90.0, 3.0)
    below = e.update(mid_lat, mid_lon, 120.0, 0.0, True, None, None, 1000.0, alt_ft=2000.0)
    assert below["act_leg"] == 2
    assert below["crs"] == pytest.approx(90.0)  # the published course, not a bearing to the level-off estimate

    no_alt_data = e.update(mid_lat, mid_lon, 120.0, 0.0, True, None, None, 1001.0, alt_ft=None)
    assert no_alt_data["act_leg"] == 2  # no altitude input -- never a guessed termination

    above = e.update(mid_lat, mid_lon, 120.0, 0.0, True, None, None, 1002.0, alt_ft=3200.0)
    assert above["act_leg"] == 3
    assert above.get("msg") == "SEQ ALT -> B"  # no ident invented for where it terminated


def test_fa_leg_flies_to_fix_then_holds_course_to_altitude():
    e = engine.Engine()
    fix_lat, fix_lon = geo.destination_point(0.0, 0.0, 90.0, 10.0)
    route = [wp("A", 0.0, 0.0), engine.Waypoint("FAFIX", fix_lat, fix_lon, pt="FA", crs=90.0, alt="+3000"), wp("B", 0.0, nm_to_deg_lon(30.0))]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))

    before_lat, before_lon = geo.destination_point(fix_lat, fix_lon, 90.0, -0.05)
    approaching = e.update(before_lat, before_lon, 120.0, 0.0, True, None, None, 1000.0, alt_ft=1000.0)
    assert approaching["act_leg"] == 2
    assert e._leg_phase is None  # still a plain fix arrival, not altitude-terminated yet

    past_lat, past_lon = geo.destination_point(fix_lat, fix_lon, 90.0, 0.05)
    just_past = e.update(past_lat, past_lon, 120.0, 0.0, True, None, None, 1001.0, alt_ft=1000.0)
    assert just_past["act_leg"] == 2  # not sequenced away -- now holding course past the fix
    assert e._leg_phase == engine.LEGPHASE_FA_COURSE

    further_lat, further_lon = geo.destination_point(fix_lat, fix_lon, 90.0, 2.0)
    done = e.update(further_lat, further_lon, 120.0, 0.0, True, None, None, 1002.0, alt_ft=3500.0)
    assert done["act_leg"] == 3
    assert done.get("msg") == "SEQ ALT -> B"


# --- HM/HF/HA holds and PI procedure turns: outbound/inbound state machine -


def _hold_route(pt, turn="R", alt=""):
    return [
        wp("A", 0.0, -nm_to_deg_lon(20.0)),
        hold_wp("HOLD", 0.0, 0.0, crs=90.0, dst=5.0, turn=turn, pt=pt, alt=alt),
        wp("B", 0.0, nm_to_deg_lon(20.0)),
    ]


def _circuit(gs=120.0, turn="R"):
    # The _hold_route hold: fix (0, 0), inbound 090, 5 nm legs.
    return hold_circuit_points(0.0, 0.0, 90.0, 5.0, turn, gs)


def _fly_circuit(e, t0, alt_ft=None, turn="R"):
    """TURN_OUT -> OUTBOUND -> TURN_IN -> INBOUND, then the fix passage."""
    past_turn_out, past_outbound, past_turn_in, past_fix = _circuit(turn=turn)
    for i, (phase, pos) in enumerate(((engine.LEGPHASE_OUTBOUND, past_turn_out),
                                      (engine.LEGPHASE_TURN_IN, past_outbound),
                                      (engine.LEGPHASE_INBOUND, past_turn_in))):
        e.update(*pos, 120.0, 0.0, True, None, None, t0 + i, alt_ft=alt_ft)
        assert e._leg_phase == phase
    return e.update(*past_fix, 120.0, 0.0, True, None, None, t0 + 3, alt_ft=alt_ft)


def test_hm_hold_repeats_until_resume_arms_exit_at_next_fix_passage():
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))

    # Arriving on the inbound course itself: a direct entry, straight into
    # the turn outbound.
    entry = e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    assert (e._leg_phase, e._hold_entry) == (engine.LEGPHASE_TURN_OUT, engine.HOLD_ENTRY_DIRECT)
    assert entry["act_leg"] == 2
    assert entry.get("phase") == "HOLD"

    no_exit = _fly_circuit(e, 1001.0)
    assert no_exit["act_leg"] == 2  # HM: no RESUME yet -- go around again
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT

    ack, msg = e.handle_command("2 RESUME", (0.0, nm_to_deg_lon(0.05), True))
    assert ack == 2 and msg == "HOLD EXIT ARMED"
    assert e.suspended is False

    exited = _fly_circuit(e, 1010.0)
    assert exited["act_leg"] == 3
    assert exited.get("msg") == "SEQ HOLD -> B"
    assert e._leg_phase is None
    assert e._hold_exit_requested is False


def test_hf_hold_exits_automatically_after_one_circuit():
    e = engine.Engine()
    load(e, _hold_route("HF", turn="L"), "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    out = _fly_circuit(e, 1001.0, turn="L")
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ HOLD -> B"


def test_ha_hold_repeats_until_altitude_then_exits():
    e = engine.Engine()
    load(e, _hold_route("HA", alt="+3000"), "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0, alt_ft=2000.0)

    below = _fly_circuit(e, 1001.0, alt_ft=2500.0)
    assert below["act_leg"] == 2  # below the coded altitude -- go around again
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT

    above = _fly_circuit(e, 1010.0, alt_ft=3200.0)
    assert above["act_leg"] == 3
    assert above.get("msg") == "SEQ HOLD -> B"


# --- Hold entries and the racetrack (fix-gateway#45) -----------------------


@pytest.mark.parametrize(
    "turn,arrival,expected",
    [
        # Right hold, inbound 090: the holding side is south, the outbound
        # course 270. Turning from 270 toward the south (200..270) is the
        # teardrop's 70 deg; toward the north up to 110 deg (270..020) the
        # parallel's; the other 180 deg (020..200, around through 090) direct.
        ("R", 90.0, "DIRECT"),
        ("R", 270.0, "TEARDROP"),
        ("R", 240.0, "TEARDROP"),
        ("R", 200.0, "TEARDROP"),
        ("R", 199.0, "DIRECT"),
        ("R", 300.0, "PARALLEL"),
        ("R", 20.0, "PARALLEL"),
        ("R", 21.0, "DIRECT"),
        # Left hold, inbound 090: the mirror image, holding side north.
        ("L", 90.0, "DIRECT"),
        ("L", 270.0, "TEARDROP"),
        ("L", 300.0, "TEARDROP"),
        ("L", 340.0, "TEARDROP"),
        ("L", 341.0, "DIRECT"),
        ("L", 240.0, "PARALLEL"),
        ("L", 160.0, "PARALLEL"),
        ("L", 159.0, "DIRECT"),
    ],
)
def test_hold_entry_sectors_follow_aim_5_3_8(turn, arrival, expected):
    assert engine.hold_entry(90.0, turn, arrival) == expected


def _hold_from(arrival_true, pt="HM", turn="R"):
    """_hold_route's hold, reached from 20 nm out on arrival_true."""
    a_lat, a_lon = geo.destination_point(0.0, 0.0, arrival_true + 180.0, 20.0)
    return [
        wp("A", a_lat, a_lon),
        hold_wp("HOLD", 0.0, 0.0, crs=90.0, dst=5.0, turn=turn, pt=pt),
        wp("B", 0.0, nm_to_deg_lon(20.0)),
    ], (a_lat, a_lon)


def test_parallel_entry_flies_the_outbound_course_one_minute_then_returns_inbound():
    # Right hold inbound 090, arriving on 300: the parallel sector. AIM
    # 5-3-8: parallel the holding course outbound on the non-holding side
    # for one minute (2.0 nm at 120 kt), turn back and return to the fix.
    e = engine.Engine()
    route, a = _hold_from(300.0)
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (*a, True))

    at_fix = geo.destination_point(0.0, 0.0, 300.0, 0.01)
    out = e.update(*at_fix, 120.0, 0.0, True, None, None, 1000.0)
    assert (e._leg_phase, e._hold_entry) == (engine.LEGPHASE_ENTRY, engine.HOLD_ENTRY_PARALLEL)
    assert out["crs"] == pytest.approx(270.0, abs=0.01)
    assert out.get("phase") == "HOLD"

    e.update(*geo.destination_point(0.0, 0.0, 270.0, 1.9), 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_ENTRY
    back = e.update(*geo.destination_point(0.0, 0.0, 270.0, 2.05), 120.0, 0.0, True, None, None, 1002.0)
    assert e._leg_phase == engine.LEGPHASE_INBOUND
    assert back["crs"] == pytest.approx(90.0, abs=0.01)
    assert back["xtk"] == pytest.approx(0.0, abs=1e-6)  # still on the inbound course line

    # The return reaches the fix like any inbound leg, then the racetrack.
    e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1003.0)
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT
    assert e._hold_entry == engine.HOLD_ENTRY_PARALLEL


def test_teardrop_entry_flies_30_degrees_toward_the_holding_side():
    # Right hold inbound 090 (holding side south), arriving on 240.
    e = engine.Engine()
    route, a = _hold_from(240.0)
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (*a, True))
    out = e.update(*geo.destination_point(0.0, 0.0, 240.0, 0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert (e._leg_phase, e._hold_entry) == (engine.LEGPHASE_ENTRY, engine.HOLD_ENTRY_TEARDROP)
    assert out["crs"] == pytest.approx(240.0, abs=HOLD_CRS_DEG)
    e.update(*geo.destination_point(0.0, 0.0, 240.0, 2.05), 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_INBOUND


def test_hf_hold_after_a_parallel_entry_exits_at_the_return_to_the_fix():
    # The entry is the course reversal: established inbound at the fix,
    # an HF (hold in lieu of a procedure turn) has nothing left to fly.
    e = engine.Engine()
    route, a = _hold_from(300.0, pt="HF")
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (*a, True))
    e.update(*geo.destination_point(0.0, 0.0, 300.0, 0.01), 120.0, 0.0, True, None, None, 1000.0)
    e.update(*geo.destination_point(0.0, 0.0, 270.0, 2.05), 120.0, 0.0, True, None, None, 1001.0)
    out = e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1002.0)
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ HOLD -> B"


def _on_turn_out(e):
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    e.update(0.0, nm_to_deg_lon(0.001), 120.0, 0.0, True, None, None, 1000.0)
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT


def test_turn_outbound_is_guided_round_a_standard_rate_half_circle():
    # Right hold inbound 090 at 120 kt: R = 120 / 188.5 nm, centred R south
    # of the fix. A quarter of the way round the aircraft heads south.
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    _on_turn_out(e)
    r = 120.0 / 188.5
    c_lat, c_lon = geo.destination_point_wgs84(0.0, 0.0, 180.0, r)
    on_lat, on_lon = geo.destination_point_wgs84(c_lat, c_lon, 90.0, r)
    out = e.update(on_lat, on_lon, 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT
    assert out["crs"] == pytest.approx(180.0, abs=HOLD_CRS_DEG)
    assert out["xtk"] == pytest.approx(0.0, abs=1e-6)
    wide = e.update(*geo.destination_point_wgs84(c_lat, c_lon, 90.0, r + 0.1), 120.0, 0.0, True, None, None, 1002.0)
    # outside a right turn is left of track; read on the sphere, a WGS-84
    # distance is up to 0.6% long or short (meridian radius 3420 nm here)
    assert wide["xtk"] == pytest.approx(-0.1, rel=0.006)


def test_outbound_leg_is_the_reciprocal_course_on_the_racetrack_side():
    # The outbound leg is flown on the reciprocal course two turn radii
    # off the inbound course line: a pilot flying the reciprocal heading
    # from the end of the turn sees the needle centred, and the inbound
    # course extended reads two radii right of track (south of a westbound
    # leg is left, so the inbound line to the north is right).
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    _on_turn_out(e)
    past_turn_out, _, _, _ = _circuit()
    e.update(*past_turn_out, 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_OUTBOUND
    r = 120.0 / 188.5
    on_track = e.update(*geo.destination_point_wgs84(0.0, 0.0, 180.0, 2.0 * r), 120.0, 0.0, True, None, None, 1002.0)
    assert on_track["crs"] == pytest.approx(270.0, abs=HOLD_CRS_DEG)
    assert on_track["xtk"] == pytest.approx(0.0, abs=1e-4)
    on_inbound_line = e.update(*geo.destination_point(0.0, 0.0, 270.0, 1.0), 120.0, 0.0, True, None, None, 1003.0)
    assert on_inbound_line["xtk"] == pytest.approx(2.0 * r, rel=0.006)  # the sphere's scale, as above


def test_outbound_leg_ends_at_the_coded_along_track_distance_from_the_fix():
    # FAA-H-8083-16B ch. 3: for a GPS hold "the end of the outbound leg is
    # determined by the ATD". 5.0 nm coded: still outbound at 4.95 nm along
    # track, the turn inbound at 5.05 nm -- wherever the aircraft is across
    # track, so a pilot flying a heading gets the same cue.
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    _on_turn_out(e)
    past_turn_out, _, _, _ = _circuit()
    e.update(*past_turn_out, 120.0, 0.0, True, None, None, 1001.0)
    for across_nm in (0.0, 1.27, 2.5):
        e._leg_phase = engine.LEGPHASE_OUTBOUND
        lat, lon = geo.destination_point(*geo.destination_point(0.0, 0.0, 270.0, 4.95), 180.0, across_nm)
        e.update(lat, lon, 120.0, 0.0, True, None, None, 1002.0)
        assert e._leg_phase == engine.LEGPHASE_OUTBOUND, across_nm
        lat, lon = geo.destination_point(*geo.destination_point(0.0, 0.0, 270.0, 5.05), 180.0, across_nm)
        e.update(lat, lon, 120.0, 0.0, True, None, None, 1003.0)
        assert e._leg_phase == engine.LEGPHASE_TURN_IN, across_nm


def test_hold_as_the_last_slot_keeps_flying_the_racetrack():
    e = engine.Engine()
    load(e, _hold_route("HF")[:2], "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT
    out = _fly_circuit(e, 1001.0)
    assert out["act_leg"] == 2  # nothing to exit to, even for an HF
    assert e._leg_phase == engine.LEGPHASE_TURN_OUT


def test_pi_outbound_target_offsets_45_degrees_by_turn_direction():
    e = engine.Engine()
    to_wp_r = hold_wp("PT", 0.0, 0.0, crs=90.0, dst=5.0, turn="R", pt="PI")
    target_r = e._hold_outbound_target(to_wp_r, 0.0)
    assert geo.bearing_deg(0.0, 0.0, target_r.lat, target_r.lon) == pytest.approx(315.0, abs=1e-6)

    to_wp_l = hold_wp("PT", 0.0, 0.0, crs=90.0, dst=5.0, turn="L", pt="PI")
    target_l = e._hold_outbound_target(to_wp_l, 0.0)
    assert geo.bearing_deg(0.0, 0.0, target_l.lat, target_l.lon) == pytest.approx(225.0, abs=1e-6)


def test_pi_procedure_turn_exits_automatically_after_one_turn():
    e = engine.Engine()
    route = [
        wp("A", 0.0, -nm_to_deg_lon(20.0)),
        hold_wp("PT", 0.0, 0.0, crs=90.0, dst=5.0, turn="R", pt="PI"),
        wp("B", 0.0, nm_to_deg_lon(20.0)),
    ]
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    entry = e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    assert entry.get("phase") == "PTURN"

    to_wp = hold_wp("PT", 0.0, 0.0, crs=90.0, dst=5.0, turn="R", pt="PI")
    target = e._hold_outbound_target(to_wp, 0.0)
    past_lat, past_lon = geo.destination_point(target.lat, target.lon, geo.bearing_deg(0.0, 0.0, target.lat, target.lon), 0.05)
    e.update(past_lat, past_lon, 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_INBOUND

    out = e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1002.0)
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ PT -> B"


# --- Persistence round-trip includes the Tier-2 sub-leg state --------------


def test_persistence_round_trips_leg_phase_and_hold_exit_requested():
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    e.update(0.0, nm_to_deg_lon(0.05), 120.0, 0.0, True, None, None, 1000.0)
    e._hold_exit_requested = True

    d = e.to_persisted_dict()
    e2 = engine.Engine()
    e2.restore_from_dict(d)
    assert e2._leg_phase == engine.LEGPHASE_TURN_OUT
    assert e2._hold_exit_requested is True
    assert e2._hold_entry == engine.HOLD_ENTRY_DIRECT
    assert e2._hold_r_nm == pytest.approx(120.0 / 188.5)
    assert e2._hold_entry_nm == pytest.approx(2.0)


def test_state_file_from_before_hold_entries_resumes_the_hold():
    # A pre-#45 file has leg_phase OUTBOUND and none of the hold fields: the
    # radius is re-derived from the GS of the first cycle after restore.
    e = engine.Engine()
    load(e, _hold_route("HM"), "T", 1)
    e.handle_command("1 ACT 2", (0.0, -nm_to_deg_lon(20.0), True))
    d = e.to_persisted_dict()
    d["leg_phase"] = engine.LEGPHASE_OUTBOUND
    for k in ("hold_entry", "hold_r_nm", "hold_entry_nm"):
        del d[k]
    e2 = engine.Engine()
    e2.restore_from_dict(d)
    r = 120.0 / 188.5
    out = e2.update(*geo.destination_point_wgs84(0.0, 0.0, 180.0, 2.0 * r), 120.0, 0.0, True, None, None, 1000.0)
    assert e2._leg_phase == engine.LEGPHASE_OUTBOUND
    assert out["crs"] == pytest.approx(270.0, abs=HOLD_CRS_DEG)
    assert out["xtk"] == pytest.approx(0.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Past the TO: DTK is the leg course extended through the TO, never its
# reciprocal (fix-gateway#35). A mid-latitude leg on a 304-ish course (the
# RZS -> KSMX geometry the FP7 HSI check found it on), so meridian
# convergence is real and the course is not a cardinal one.
# ---------------------------------------------------------------------------

PAST_TO_LAT, PAST_TO_LON = 34.6, -120.0
PAST_TO_COURSE = 304.0


def diagonal_route(specs):
    """specs: [(id, nm from the first point, type, flags)] along a great
    circle leaving (PAST_TO_LAT, PAST_TO_LON) on PAST_TO_COURSE."""
    out = []
    for id, nm, type, flags in specs:
        lat, lon = geo.destination_point(PAST_TO_LAT, PAST_TO_LON, PAST_TO_COURSE, nm)
        out.append(wp(id, lat, lon, type=type, flags=flags))
    return out


def inbound_course(fr, to):
    """The leg's true course as it arrives at the TO (final bearing)."""
    return geo.wrap360(geo.bearing_deg(to.lat, to.lon, fr.lat, fr.lon) + 180.0)


def past_to(fr, to, past_nm, right_nm=0.0):
    """A point past_nm beyond the TO along the extended leg, then right_nm
    to the right of it (positive = right of the extended track)."""
    crs = inbound_course(fr, to)
    lat, lon = geo.destination_point(to.lat, to.lon, crs, past_nm)
    if right_nm:
        lat, lon = geo.destination_point(lat, lon, geo.wrap360(crs + 90.0), right_nm)
    return lat, lon


def angle_diff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def assert_extended_course(out, fr, to, right):
    assert out["tf"] == engine.TF_FROM
    assert angle_diff(out["crs"], inbound_course(fr, to)) < 0.01
    if right:
        # Right of the extended track: XTK positive, needle deflects left
        # (fly left) against a pointer on the inbound course.
        assert out["xtk"] > 0.0
        assert out["cdi"] < 0.0


@pytest.mark.parametrize("right", [False, True])
def test_past_last_waypoint_course_continues_through_it(right):
    route = diagonal_route([
        ("A", 0.0, engine.TYPE_FIX, 0),
        ("B", 20.0, engine.TYPE_FIX, 0),
        ("C", 40.0, engine.TYPE_FIX, 0),
    ])
    e = engine.Engine()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (route[1].lat, route[1].lon, True))
    lat, lon = past_to(route[1], route[2], 0.5, 0.3 if right else 0.0)
    out = e.update(lat, lon, 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert_extended_course(out, route[1], route[2], right)


@pytest.mark.parametrize("right", [False, True])
def test_past_map_auto_susp_course_continues_along_final(right):
    route = diagonal_route([
        ("IAF", 0.0, engine.TYPE_FIX, engine.FLAG_IAF),
        ("FAF", 5.0, engine.TYPE_FIX, engine.FLAG_FAF),
        ("MAP", 10.0, engine.TYPE_MAPPOINT, engine.FLAG_MAP),
        ("MAHP", 12.0, engine.TYPE_FIX, engine.FLAG_MAHP),
    ])
    route[3] = wp("MAHP", *geo.destination_point(route[2].lat, route[2].lon, 34.0, 2.0),
                  type=engine.TYPE_FIX, flags=engine.FLAG_MAHP)  # missed turns off final
    e = engine.Engine()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 3", (route[1].lat, route[1].lon, True))
    lat, lon = past_to(route[1], route[2], 0.5, 0.15 if right else 0.0)
    out = e.update(lat, lon, 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert out["state"] == engine.STATE_SUSP
    assert out["cdiscale"] == pytest.approx(0.3)
    assert_extended_course(out, route[1], route[2], right)


@pytest.mark.parametrize("right", [False, True])
def test_suspended_past_intermediate_waypoint_course_continues_through_it(right):
    route = diagonal_route([
        ("A", 0.0, engine.TYPE_FIX, 0),
        ("B", 20.0, engine.TYPE_FIX, 0),
        ("C", 40.0, engine.TYPE_FIX, 0),
    ])
    e = engine.Engine()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (route[0].lat, route[0].lon, True))
    e.handle_command("2 SUSP", (route[0].lat, route[0].lon, True))
    lat, lon = past_to(route[0], route[1], 0.5, 0.3 if right else 0.0)
    out = e.update(lat, lon, 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2  # suspended: B is not sequenced
    assert out["state"] == engine.STATE_SUSP
    assert_extended_course(out, route[0], route[1], right)


def test_desired_track_is_continuous_through_the_to_waypoint():
    # Straddling the TO by a hair either side, DTK must not jump.
    route = diagonal_route([("A", 0.0, 0, 0), ("B", 20.0, 0, 0)])
    fr, to = route
    crs = inbound_course(fr, to)
    for d in (-0.01, -0.0001, 0.0001, 0.01, 5.0):
        lat, lon = geo.destination_point(to.lat, to.lon, crs, d)
        dtk = geo.desired_track_true(fr.lat, fr.lon, to.lat, to.lon, lat, lon)
        assert angle_diff(dtk, crs) < 0.1, d
