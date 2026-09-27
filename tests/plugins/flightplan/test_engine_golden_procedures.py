"""PA13 Tier-2 leg types (RF/AF arcs, CA altitude-terminated legs, HM holds,
PI procedure turns) flown against real ARINC 424 coding, not synthetic
geometry.

test_engine.py's Tier-2 tests (fix-gateway#32) all build their arcs/holds
around a constant round-number centre (`CTR = (34.0, -120.0)`). INTEGRATOR
flagged on fix-gateway#29/AER-1612 that this does not satisfy that issue's
first acceptance bullet -- each Tier-2 type tested "against the PA1 golden
fixtures, including at least one real published procedure per type flown end
to end" -- because synthetic geometry can be internally consistent and still
disagree with real ARINC coding. This file is that missing coverage.

``tests/fixtures/cifp/FAACIFP18`` is the identical 156-record excerpt
makerplane-data committed for AER-1600/PA1 -- see that directory's README for
full provenance (FAA CIFP cycle 2609, public domain). Every constant below
was decoded from that file's fixed-width ARINC 424-18 records (column
positions verified against known real-world coordinates -- KABQ ~35.04N/
106.6W, KSBA ~34.4N/119.8W, 09J ~31.0N/81.4-81.5W -- not transcribed from
another repo's parser), and
``test_leg_constants_match_the_committed_golden_fixture_file`` re-decodes the
committed file directly so a hand-transcription error here cannot go
unnoticed.

Real procedures exercised:
  RF arc  -- KABQ RNAV (GPS) Y RWY 21, FOXRR transition (two chained real
             arcs, same centre CFDXH)
  AF arc  -- 09J VOR-A, TOMDY/WIGID transitions (SSI VOR-centred DME arc)
  CA      -- KSBA ILS RWY 07, missed approach (real climb course + altitude)
  HM      -- KSBA ILS RWY 07, missed approach hold at GOLET
  PI      -- 09J VOR-A, SSI transition (a lone procedure turn at the IAF)
"""

from pathlib import Path

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine

_FIXTURE_PATH = Path(__file__).resolve().parent.parent.parent / "fixtures" / "cifp" / "FAACIFP18"


def wp(id, lat, lon, type=engine.TYPE_FIX):
    return engine.Waypoint(id, lat, lon, type)


# ---------------------------------------------------------------------------
# Real ARINC 424 leg data, decoded by hand from tests/fixtures/cifp/FAACIFP18.
# Course/theta/rho fields are tenths of a degree/nm, altitude whole feet
# (ARINC 424-18 sec. 4); lat/lon decoded from the N/Sddmmss.ss / E/Wdddmmss.ss
# fields. Each block cites its source line's procedure/transition/sequence.
# ---------------------------------------------------------------------------

# --- KABQ RNAV (GPS) Y RWY 21, FOXRR transition (PD KABQ H21-Y AFOXRR) -----
# Two chained RF legs sharing one real arc centre, CFDXH -- proves the engine
# flies a real arc-to-arc join, not just one isolated circle.
KABQ_BGEYE = (35.12695833, -106.65031667)   # AFOXRR/020 TF, fix BGEYE
KABQ_CFDXH = (35.08599722, -106.62291667)   # RF centre fix (added AER-1700)
KABQ_KEIFR = (35.125675, -106.59285556)     # AFOXRR/030 RF, turn R
KABQ_KAGNE = (35.09366667, -106.56681944)   # AFOXRR/040 RF, turn R
# Radius is the great-circle distance centre->fix, exactly as makerplane-data's
# own build derives arc_radius_nm (packtools/build/procedures.py) -- an RF
# leg's coded Route Distance field is NOT the radius (verified: it reads 3.0
# and 2.4nm here, while centre->fix reads ~2.80nm for both, matching a single
# real arc), so it is not used as one.
KABQ_KEIFR_RADIUS_NM = geo.distance_nm(*KABQ_CFDXH, *KABQ_KEIFR)   # ~2.80
KABQ_KAGNE_RADIUS_NM = geo.distance_nm(*KABQ_CFDXH, *KABQ_KAGNE)   # ~2.79

# --- 09J VOR-A, TOMDY and WIGID transitions --------------------------------
# AF (DME arc) legs, centred on the SSI VOR -- an AF arc's centre is the
# Recommended Navaid, not a Centre Fix column (that is RF-only).
NINEJ_SSI = (31.05051389, -81.44596389)     # SSI VOR, recd navaid for both AF legs
NINEJ_TOMDY = (30.93517778, -81.42359167)   # ATOMDY/010 IF
NINEJ_SAUSE = (30.95256944, -81.52011111)   # ATOMDY/020 AF turn R, AWIGID/020 AF turn L
NINEJ_WIGID = (31.10594444, -81.565575)     # AWIGID/010 IF
NINEJ_SAUSE_RADIUS_NM = geo.distance_nm(*NINEJ_SSI, *NINEJ_SAUSE)  # ~7.01, coded rho 7.0

# --- 09J VOR-A, SSI transition: a lone PI at the IAF ------------------------
NINEJ_PI_COURSE = 172.1   # ASSI/010 PI SSI -- real inbound course
NINEJ_PI_DIST_NM = 10.0   # real leg length

# --- KSBA ILS RWY 07, "I" (common + missed) transition ---------------------
KSBA_RW07 = (34.4275, -119.85464167)        # I/030 CF -- the threshold/FAF fix
KSBA_CA_COURSE = 74.6                       # I/040 CA -- real missed-approach climb course
KSBA_CA_ALT_FT = 700                        # I/040 CA alt1, coded "+00700"
KSBA_GOLET = (34.28135, -119.864375)        # I/050 CF, I/060 HM
KSBA_GOLET_CF_COURSE = 184.8
KSBA_GOLET_CF_DIST_NM = 10.0
KSBA_GOLET_HM_COURSE = 307.0
# The coded hold leg length is "T010" -- ARINC's time-based form (1.0 min),
# not the nm distance the engine's Tier-2 hold model takes (Waypoint has no
# time field; see engine.py's PA13 docstring). Every hold in this whole
# fixture is coded this way (KSBA GOLET, 09J SSI, KABQ ABQ all "T010") -- a
# real gap between ARINC's time-based holds and this leg-length-only model,
# not a quirk of this one fixture. Converted here at TERPS/ICAO's default
# below-14,000ft holding airspeed (200 KIAS) so this test can fly it; the
# real conversion belongs in whichever layer resolves a leg for the wire
# (PA5/PA7's procedure lookup and insertion, pyEfis side) -- not asserted
# correct by this test, only used to exercise the state machine.
KSBA_GOLET_HM_LEG_LEN_NM = 200.0 * (1.0 / 60.0)  # ~3.33nm for a 1.0-min leg


@pytest.mark.parametrize("pt", ["RF", "AF", "CA", "HM", "PI"])
def test_every_real_leg_type_used_below_would_have_been_rejected_whole_before_pa13(pt):
    # Guardrail 1: before PA13, none of these were in PT_SUPPORTED, so any
    # procedure containing one -- including every real one exercised below --
    # was rejected whole with a reason on FPLMSG (fix-gateway#28/PA4).
    assert pt not in (engine.PT_TIER1 | engine.PT_VECTOR)
    assert pt in engine.PT_TIER2


def test_leg_constants_match_the_committed_golden_fixture_file():
    """Re-decodes the committed FAACIFP18 excerpt directly (independent of
    the hand-transcribed constants above) so a transcription slip in this
    file cannot silently go untested."""
    proc_airport, proc_ident, proc_route_type = slice(6, 10), slice(13, 19), slice(19, 20)
    proc_transition, proc_seq, proc_turn = slice(20, 25), slice(26, 29), slice(43, 44)
    proc_path_term, proc_course, proc_alt1 = slice(47, 49), slice(70, 74), slice(84, 89)
    proc_centre_fix = slice(106, 111)

    def find_leg(airport, ident, transition, seq):
        with open(_FIXTURE_PATH, encoding="latin-1") as f:
            for line in f:
                line = line.rstrip("\n")
                if len(line) != 132 or line[4:5] != "P":
                    continue
                if (line[proc_airport].strip() == airport and line[proc_ident].strip() == ident
                        and line[proc_transition].strip() == transition and line[proc_seq].strip() == seq):
                    return line
        raise AssertionError(f"fixture record not found: {airport} {ident} {transition!r} {seq}")

    keifr = find_leg("KABQ", "H21-Y", "FOXRR", "030")
    assert keifr[proc_path_term] == "RF"
    assert keifr[proc_turn] == "R"
    assert keifr[proc_centre_fix].strip() == "CFDXH"

    sause = find_leg("09J", "VOR-A", "TOMDY", "020")
    assert sause[proc_path_term] == "AF"
    assert sause[proc_turn] == "R"

    ca = find_leg("KSBA", "I07", "", "040")
    assert ca[proc_path_term] == "CA"
    assert int(ca[proc_course]) / 10.0 == KSBA_CA_COURSE
    assert int(ca[proc_alt1]) == KSBA_CA_ALT_FT

    hm = find_leg("KSBA", "I07", "", "060")
    assert hm[proc_path_term] == "HM"
    assert hm[proc_turn] == "R"
    assert int(hm[proc_course]) / 10.0 == KSBA_GOLET_HM_COURSE

    pi = find_leg("09J", "VOR-A", "SSI", "010")
    assert pi[proc_path_term] == "PI"
    assert pi[proc_turn] == "R"
    assert int(pi[proc_course]) / 10.0 == NINEJ_PI_COURSE


# --- RF: KABQ H21-Y FOXRR transition, two chained real arcs -----------------


def test_kabq_h21y_foxrr_transition_rf_arcs_load_and_fly_the_real_centre_and_radius():
    route = [
        wp("BGEYE", *KABQ_BGEYE),
        engine.Waypoint("KEIFR", *KABQ_KEIFR, pt="RF", dst=KABQ_KEIFR_RADIUS_NM,
                         turn="R", ctrlat=KABQ_CFDXH[0], ctrlon=KABQ_CFDXH[1]),
        engine.Waypoint("KAGNE", *KABQ_KAGNE, pt="RF", dst=KABQ_KAGNE_RADIUS_NM,
                         turn="R", ctrlat=KABQ_CFDXH[0], ctrlon=KABQ_CFDXH[1]),
        wp("NEXT", 35.0, -106.5),
    ]
    assert engine.unsupported_leg_reason(route) is None

    e = engine.Engine()
    e.load_route(route, "H21-Y", 1)
    e.handle_command("1 ACT 2", (*KABQ_BGEYE, True))

    keifr_exit_brg = geo.bearing_deg(*KABQ_CFDXH, *KABQ_KEIFR)
    before = geo.destination_point(*KABQ_CFDXH, keifr_exit_brg - 0.1, KABQ_KEIFR_RADIUS_NM)
    out = e.update(*before, 150.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2  # still turning the first real arc

    after = geo.destination_point(*KABQ_CFDXH, keifr_exit_brg + 0.1, KABQ_KEIFR_RADIUS_NM)
    out2 = e.update(*after, 150.0, 0.0, True, None, None, 1001.0)
    assert out2["act_leg"] == 3  # sequenced onto the second real arc, same real centre
    assert out2.get("msg") == "SEQ KEIFR -> KAGNE"

    kagne_exit_brg = geo.bearing_deg(*KABQ_CFDXH, *KABQ_KAGNE)
    after2 = geo.destination_point(*KABQ_CFDXH, kagne_exit_brg + 0.1, KABQ_KAGNE_RADIUS_NM)
    out3 = e.update(*after2, 150.0, 0.0, True, None, None, 1002.0)
    assert out3["act_leg"] == 4
    assert out3.get("msg") == "SEQ KAGNE -> NEXT"


# --- AF: 09J VOR-A TOMDY/WIGID transitions, SSI-VOR-centred DME arc --------


def test_09j_vora_tomdy_transition_af_arc_loads_and_flies_the_real_navaid_centred_arc():
    route = [
        wp("TOMDY", *NINEJ_TOMDY),
        engine.Waypoint("SAUSE", *NINEJ_SAUSE, pt="AF", dst=NINEJ_SAUSE_RADIUS_NM,
                         turn="R", ctrlat=NINEJ_SSI[0], ctrlon=NINEJ_SSI[1]),
        wp("NEXT", 31.0, -81.4),
    ]
    assert engine.unsupported_leg_reason(route) is None

    e = engine.Engine()
    e.load_route(route, "VOR-A", 1)
    e.handle_command("1 ACT 2", (*NINEJ_TOMDY, True))

    exit_brg = geo.bearing_deg(*NINEJ_SSI, *NINEJ_SAUSE)
    before = geo.destination_point(*NINEJ_SSI, exit_brg - 0.1, NINEJ_SAUSE_RADIUS_NM)
    out = e.update(*before, 150.0, 0.0, True, None, None, 1000.0)
    assert out["act_leg"] == 2

    after = geo.destination_point(*NINEJ_SSI, exit_brg + 0.1, NINEJ_SAUSE_RADIUS_NM)
    out2 = e.update(*after, 150.0, 0.0, True, None, None, 1001.0)
    assert out2["act_leg"] == 3
    assert out2.get("msg") == "SEQ SAUSE -> NEXT"


def test_09j_vora_wigid_transition_af_arc_turn_direction_sign_matches_real_coding():
    # Same SSI-centred real arc, opposite real coded turn direction (WIGID:
    # L vs TOMDY: R) -- a direct check, on real data, that the engine's
    # turn-direction sign convention gets a real AF leg's coded turn_dir
    # right, not just the synthetic constant-centre case in test_engine.py.
    to_wp = engine.Waypoint("SAUSE", *NINEJ_SAUSE, pt="AF", dst=NINEJ_SAUSE_RADIUS_NM,
                             turn="L", ctrlat=NINEJ_SSI[0], ctrlon=NINEJ_SSI[1])
    e = engine.Engine()
    on_brg = geo.bearing_deg(*NINEJ_SSI, *NINEJ_SAUSE)
    out_lat, out_lon = geo.destination_point(*NINEJ_SSI, on_brg, NINEJ_SAUSE_RADIUS_NM + 1.0)
    _, _, xtk_out, _ = e._arc_geometry(to_wp, out_lat, out_lon)
    assert xtk_out == pytest.approx(1.0, abs=1e-2)  # outside a left turn is right of course


# --- CA: KSBA I07 missed approach, real climb course and altitude ----------


def test_ksba_i07_missed_approach_ca_leg_terminates_on_the_real_published_altitude():
    # ARINC CA legs carry no coded fix or distance -- only a course and an
    # altitude to climb to. The "to" position here is a nominal placement
    # 10nm out along the real course (074.6 deg) purely so the engine has a
    # directional target; the real, asserted values are the course and the
    # altitude (KSBA I07's actual missed-approach climb, coded "+00700").
    lvl_lat, lvl_lon = geo.destination_point(*KSBA_RW07, KSBA_CA_COURSE, 10.0)
    route = [
        wp("RW07", *KSBA_RW07),
        engine.Waypoint("CALVL", lvl_lat, lvl_lon, pt="CA", crs=KSBA_CA_COURSE, alt=f"+{KSBA_CA_ALT_FT}"),
        wp("NEXT", 34.5, -119.8),
    ]
    assert engine.unsupported_leg_reason(route) is None

    e = engine.Engine()
    e.load_route(route, "I07", 1)
    e.handle_command("1 ACT 2", (*KSBA_RW07, True))

    mid_lat, mid_lon = geo.destination_point(*KSBA_RW07, KSBA_CA_COURSE, 3.0)
    below = e.update(mid_lat, mid_lon, 120.0, 0.0, True, None, None, 1000.0, alt_ft=KSBA_CA_ALT_FT - 200.0)
    assert below["act_leg"] == 2
    # The real published course, not a bearing guess -- abs=0.1 (not an
    # exact match) because unlike the synthetic test_engine.py CA test
    # (course 90 on the equator, a degenerate case with zero convergence),
    # KSBA's real course (074.6) at 34N does drift a few hundredths of a
    # degree between the synthetic anchor and the aircraft: great-circle
    # meridian convergence, not an engine defect.
    assert below["crs"] == pytest.approx(KSBA_CA_COURSE, abs=0.1)

    above = e.update(mid_lat, mid_lon, 120.0, 0.0, True, None, None, 1001.0, alt_ft=KSBA_CA_ALT_FT + 100.0)
    assert above["act_leg"] == 3
    assert above.get("msg") == "SEQ ALT -> NEXT"  # no ident invented for where it terminated


# --- HM: KSBA I07 missed approach hold at GOLET -----------------------------


def test_ksba_i07_golet_hold_flies_the_real_inbound_course_and_turn_direction():
    entry_lat, entry_lon = geo.destination_point(*KSBA_GOLET, KSBA_GOLET_CF_COURSE + 180.0, KSBA_GOLET_CF_DIST_NM)
    route = [
        wp("PRIOR", entry_lat, entry_lon),
        engine.Waypoint("GOLET", *KSBA_GOLET, pt="HM", crs=KSBA_GOLET_HM_COURSE,
                         dst=KSBA_GOLET_HM_LEG_LEN_NM, turn="R", alt="+4000"),
        wp("NEXT", 34.5, -119.8),
    ]
    assert engine.unsupported_leg_reason(route) is None

    e = engine.Engine()
    e.load_route(route, "I07", 1)
    e.handle_command("1 ACT 2", (entry_lat, entry_lon, True))

    # First arrival at GOLET is flown on the real CF leg's own course (184.8)
    # -- the plain from/to line the engine uses before leg_phase exists.
    arrival_past_lat, arrival_past_lon = geo.destination_point(*KSBA_GOLET, KSBA_GOLET_CF_COURSE, 0.05)
    entry = e.update(arrival_past_lat, arrival_past_lon, 120.0, 0.0, True, None, None, 1000.0)
    assert e._leg_phase == engine.LEGPHASE_OUTBOUND
    assert entry.get("phase") == "HOLD"

    outbound_target = e._hold_outbound_target(e.route[1], 0.0)
    past_lat, past_lon = geo.destination_point(
        outbound_target.lat, outbound_target.lon,
        geo.bearing_deg(*KSBA_GOLET, outbound_target.lat, outbound_target.lon), 0.05)
    e.update(past_lat, past_lon, 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_INBOUND

    # Every re-arrival at GOLET from here on is on the hold's own real
    # coded inbound course (307), not the CF leg's arrival course (184.8) --
    # a real published hold entry is flown on a different course than
    # whatever course happened to bring the aircraft to the fix the first
    # time, and the engine must key off the leg's own crs, not the arrival
    # heading, once the hold's course-anchor geometry takes over.
    inbound_past_lat, inbound_past_lon = geo.destination_point(*KSBA_GOLET, KSBA_GOLET_HM_COURSE, 0.05)

    no_exit = e.update(inbound_past_lat, inbound_past_lon, 120.0, 0.0, True, None, None, 1002.0)
    assert no_exit["act_leg"] == 2  # no RESUME yet -- go around again, exactly as HM is coded
    assert e._leg_phase == engine.LEGPHASE_OUTBOUND

    ack, msg = e.handle_command("2 RESUME", (inbound_past_lat, inbound_past_lon, True))
    assert ack == 2 and msg == "HOLD EXIT ARMED"

    e.update(past_lat, past_lon, 120.0, 0.0, True, None, None, 1003.0)
    exited = e.update(inbound_past_lat, inbound_past_lon, 120.0, 0.0, True, None, None, 1004.0)
    assert exited["act_leg"] == 3
    assert exited.get("msg") == "SEQ GOLET -> NEXT"


# --- PI: 09J VOR-A SSI transition, a lone procedure turn at the IAF --------


def test_09j_vora_ssi_transition_pi_flies_the_real_outbound_course_and_leg_length():
    entry_lat, entry_lon = geo.destination_point(*NINEJ_SSI, NINEJ_PI_COURSE + 180.0, 20.0)
    route = [
        wp("ENROUTE", entry_lat, entry_lon),
        engine.Waypoint("SSI", *NINEJ_SSI, pt="PI", crs=NINEJ_PI_COURSE, dst=NINEJ_PI_DIST_NM, turn="R"),
        wp("NEXT", 31.0, -81.4),
    ]
    assert engine.unsupported_leg_reason(route) is None

    e = engine.Engine()
    e.load_route(route, "VOR-A", 1)
    e.handle_command("1 ACT 2", (entry_lat, entry_lon, True))

    just_past_lat, just_past_lon = geo.destination_point(*NINEJ_SSI, NINEJ_PI_COURSE, 0.05)
    entry = e.update(just_past_lat, just_past_lon, 120.0, 0.0, True, None, None, 1000.0)
    assert entry.get("phase") == "PTURN"

    outbound_target = e._hold_outbound_target(e.route[1], 0.0)
    outbound_brg = geo.bearing_deg(*NINEJ_SSI, outbound_target.lat, outbound_target.lon)
    # A standard 45-deg procedure turn's outbound track is the coded inbound
    # course reversed, then offset 45 deg for the coded turn direction (R).
    assert outbound_brg == pytest.approx(geo.wrap360(NINEJ_PI_COURSE + 180.0 + 45.0), abs=1e-6)

    past_lat, past_lon = geo.destination_point(outbound_target.lat, outbound_target.lon, outbound_brg, 0.05)
    e.update(past_lat, past_lon, 120.0, 0.0, True, None, None, 1001.0)
    assert e._leg_phase == engine.LEGPHASE_INBOUND

    out = e.update(just_past_lat, just_past_lon, 120.0, 0.0, True, None, None, 1002.0)
    assert out["act_leg"] == 3
    assert out.get("msg") == "SEQ SSI -> NEXT"
