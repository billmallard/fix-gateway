"""Unit tests for fixgw.plugins.flightplan.engine.Engine -- the flight-plan
navigation engine's pure-Python core (FP2, fix-gateway#23).

Spec: makerplane/briefs/flight_plan_plan.md section 3.3 (the maos-workspace
repo), Appendix C. Bill's ruling of 2026-09-08: CDI scaling is the full
DO-229 lateral behaviour including the approach phase -- there is no
VFR-advisory mode.
"""

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine


def wp(id, lat, lon, type=engine.TYPE_UNKNOWN, role=engine.ROLE_NONE):
    return engine.Waypoint(id, lat, lon, type, role)


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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    # Fly straight past B without ever entering the (small) d_ta window from
    # the previous cycle's position -- simulate a coarse timestep landing
    # just past the waypoint.
    out = e.update(0.0, route[1].lon + nm_to_deg_lon(0.01), 120.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ B -> C"


def test_last_waypoint_never_sequences_flips_to_from():
    e = engine.Engine()
    route = straight_route(3)
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    ack, msg = e.handle_command("1 DTO", (0.0, 0.0, True), dto_staged=("XYZ", 9.0, 9.0, engine.TYPE_FIX))
    assert ack == 1
    assert e.mode == "DIRECT"
    assert e.act_leg == 0
    assert e.count == 3  # plan still loaded


def test_dto_bare_matching_staged_slot_behaves_as_dto_k():
    e = engine.Engine()
    route = straight_route(3)
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    # Aircraft far behind A, off every leg's along-track window.
    ack, msg = e.handle_command("1 DTOX", (0.0, -nm_to_deg_lon(50), True))
    assert ack == 1
    assert e.act_leg == 1  # nearest waypoint is A (slot 1)
    assert e.from_point.lat == 0.0 and e.from_point.lon == pytest.approx(-nm_to_deg_lon(50))


def test_susp_freezes_sequencing_and_dtk():
    e = engine.Engine()
    route = straight_route(3)
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, nm_to_deg_lon(4.9), 120.0, 0.0, True, None, None, 1000.0)
    assert out["phase"] == "ENR"


# ---------------------------------------------------------------------------
# Full DO-229 approach behaviour
# ---------------------------------------------------------------------------


def approach_route():
    # IAF -> FAF -> MAP -> MAHP, 5nm IAF-FAF, 5nm FAF-MAP, 2nm MAP-MAHP.
    return [
        wp("IAF", 0.0, 0.0, type=engine.TYPE_FIX, role=engine.ROLE_IAF),
        wp("FAF", 0.0, nm_to_deg_lon(5.0), type=engine.TYPE_FIX, role=engine.ROLE_FAF),
        wp("MAP", 0.0, nm_to_deg_lon(10.0), type=engine.TYPE_MAPPOINT, role=engine.ROLE_MAP),
        wp("MAHP", 0.0, nm_to_deg_lon(12.0), type=engine.TYPE_FIX, role=engine.ROLE_MAHP),
    ]


def test_approach_armed_within_30nm_of_map_with_active_leg_at_or_before_faf():
    e = engine.Engine()
    route = approach_route()
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))  # TO = FAF, before it
    out = e.update(0.0, -nm_to_deg_lon(15), 120.0, 0.0, True, None, None, 1000.0)  # 25nm from MAP
    assert out["apr"] == engine.APR_ARMED
    assert out["phase"] == "TERM"
    assert out["cdiscale"] == pytest.approx(1.0)


def test_approach_not_armed_beyond_30nm_of_map():
    e = engine.Engine()
    route = approach_route()
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, -nm_to_deg_lon(40), 120.0, 0.0, True, None, None, 1000.0)  # 45nm from MAP
    assert out["apr"] == engine.APR_NONE


def test_approach_ramp_linear_1_0_to_0_3_over_2nm_before_faf():
    e = engine.Engine()
    route = approach_route()
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    faf_lon, map_lon = route[1].lon, route[2].lon
    # Direct-to a synthetic fix between the FAF and the MAP.
    mid = wp("MID", 0.0, (faf_lon + map_lon) / 2.0, type=engine.TYPE_FIX)
    route.insert(2, mid)
    e.load_route(route, "T", 2)  # re-load with MID inserted at slot 3

    ack, msg = e.handle_command("1 DTO 3", (0.0, 0.0, True))  # DTO to MID (slot 3)
    assert ack == 1
    out = e.update(0.0, mid.lon, 120.0, 0.0, True, None, None, 1000.0)
    assert out["apr"] != engine.APR_ACTIVE


def test_scale_manual_ceiling_is_minimum_of_phase_and_manual():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(200))]
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, nm_to_deg_lon(1), 120.0, 0.0, True, False, None, 1000.0)
    assert out["fail"] is True


def test_accuracy_above_hal_sets_integ_false_bad_loi_and_reverts_approach():
    e = engine.Engine()
    route = approach_route()
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
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
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    out = e.update(0.0, 0.0, 20.0, 0.0, True, None, None, 1000.0)
    assert out["wp_ete_bad"] is True

    out = e.update(0.0, 0.0, 90.0, 0.0, True, None, None, 1001.0)
    assert out["wp_ete_bad"] is False


def test_outputs_fail_after_5s_of_bad_position():
    e = engine.Engine()
    route = [wp("A", 0.0, 0.0), wp("B", 0.0, nm_to_deg_lon(5))]
    e.load_route(route, "T", 1)
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

    e.load_route([wp("A", 0, 0), wp("B", 0, 1)], "T", 1)
    assert e.handle_command("2 ACT 9", (0.0, 0.0, True)) == (-2, "BAD SLOT")
    assert e.handle_command("3 DTO 2", (0.0, 0.0, False)) == (-3, "NO POSITION")
    assert e.handle_command("x ACT 1", (0.0, 0.0, True)) == (-1, "PARSE")
    assert e.handle_command("4 FROB", (0.0, 0.0, True)) == (-4, "PARSE")
    assert e.handle_command("5 SCALE 9.9", (0.0, 0.0, True)) == (-5, "PARSE")

    ack, msg = e.handle_command("6 ACT 1", (0.0, 0.0, True))
    assert ack == 6 and msg == ""


def test_unknown_verb_rejected_never_ignored():
    e = engine.Engine()
    e.load_route([wp("A", 0, 0)], "T", 1)
    ack, msg = e.handle_command("1 BOGUS arg", (0.0, 0.0, True))
    assert ack == -1
    assert msg == "PARSE"


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------


def test_persistence_round_trip_including_approach_state():
    e = engine.Engine()
    route = approach_route()
    e.load_route(route, "RNAV1", 3)
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
    e.load_route(route, "T", 1)
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
# Budget: one cycle with 50 slots
# ---------------------------------------------------------------------------


def test_update_cycle_budget_with_50_slots():
    import time

    e = engine.Engine()
    route = straight_route(2)
    route = [wp(f"W{i}", 0.0, nm_to_deg_lon(i * 5)) for i in range(50)]
    e.load_route(route, "T", 1)
    e.handle_command("1 ACT 25", (0.0, 0.0, True))

    start = time.perf_counter()
    for i in range(50):
        e.update(0.0, nm_to_deg_lon(24 * 5 + 0.1), 120.0, 0.0, True, None, None, 1000.0 + i)
    elapsed_per_cycle = (time.perf_counter() - start) / 50
    assert elapsed_per_cycle < 0.010  # generous 10ms CI ceiling (spec target 2ms)
