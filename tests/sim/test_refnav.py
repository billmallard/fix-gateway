"""The reference navigator (SYN1) on its own: geometry, sequencing, CDI
scaling and hold entries, checked against GeographicLib directly and
against the standards' numbers -- never against the engine."""

import math
from pathlib import Path

import pytest

from fixgw.sim import refnav
from fixgw.sim.cifp import Cifp

CIFP = Path(__file__).resolve().parents[2] / "src/fixgw/sim/profiles/cifp/FAACIFP18-2611-KSMX-R30"
MV = -11.9


@pytest.fixture(scope="module")
def cifp():
    return Cifp(CIFP)


@pytest.fixture(scope="module")
def legs(cifp):
    return refnav.legs_from_cifp(cifp, "KSMX", "R30", "GVO", MV)


def test_cifp_header_cycle_and_a_decoded_fix(cifp):
    assert cifp.cycle == "2611"
    # SUSAP KSMXK2CANIBY K20    W     N34500954W120242388 ... E0119
    f = cifp.fix("ANIBY", airport="KSMX")
    assert f.lat == pytest.approx(34 + 50 / 60 + 9.54 / 3600, abs=1e-9)
    assert f.lon == pytest.approx(-(120 + 24 / 60 + 23.88 / 3600), abs=1e-9)
    assert f.magvar_east == pytest.approx(11.9)


def test_procedure_legs_merge_the_double_coded_iaf_and_keep_the_missed(legs):
    assert [(lg.pt, lg.ident) for lg in legs] == [
        ("IF", "GVO"), ("TF", "NIRLE"), ("TF", "ELZUS"), ("TF", "PAMJI"), ("TF", "CIMSO"),
        ("TF", "ANIBY"), ("TF", "RW30"), ("CA", ""), ("DF", "HANNU"), ("TF", "WINCH"), ("HM", "WINCH")]
    by = {lg.ident: lg for lg in legs if lg.ident}
    assert by["CIMSO"].faf and by["RW30"].map and by["RW30"].flyover
    ca = legs[7]
    assert ca.first_missed and ca.alt_ft == 661.0
    assert ca.course_true == pytest.approx(316.5 - MV)
    hm = legs[-1]
    assert (hm.turn, hm.leg_nm) == ("L", 4.0)
    assert hm.course_true == pytest.approx(121.4 - MV)


def test_unsupported_leg_type_refuses_the_whole_procedure(cifp, monkeypatch):
    real = cifp.approach_legs

    def with_rf(airport, approach):
        out = real(airport, approach)
        out[4] = out[4].__class__(**{**out[4].__dict__, "path_term": "RF"})
        return out

    monkeypatch.setattr(cifp, "approach_legs", with_rf)
    with pytest.raises(refnav.ReferenceError_, match="RF"):
        refnav.legs_from_cifp(cifp, "KSMX", "R30", "GVO", MV)


@pytest.mark.parametrize("side_deg,expected", [(90.0, 1.0), (-90.0, -1.0)])
def test_cross_track_sign_and_size_on_the_ellipsoid(side_deg, expected):
    line = refnav.GeoLine.between(34.72, -120.33, 34.89, -120.45)
    mid_lat, mid_lon, azi = line.position(5.0)
    lat, lon = refnav.direct(mid_lat, mid_lon, azi + side_deg, 1.0)
    s, xtk, az_abeam = line.project(lat, lon)
    assert xtk == pytest.approx(expected, abs=1e-6)
    assert s == pytest.approx(5.0, abs=1e-6)
    assert az_abeam == pytest.approx(azi, abs=1e-6)


def _nav(legs, active_ident, position=None):
    k = next(i for i, lg in enumerate(legs) if lg.ident == active_ident and i > 0)
    return refnav.ReferenceNavigator(legs, MV, (34.8994, -120.4581), k, 110.0, position=position)


def _on_leg(legs, k, before_nm, side_nm=0.0):
    a, b = legs[k - 1], legs[k]
    line = refnav.GeoLine.between(a.lat, a.lon, b.lat, b.lon)
    lat, lon, azi = line.position(line.length_nm - before_nm)
    if side_nm:
        lat, lon = refnav.direct(lat, lon, azi + 90.0, side_nm)
    return lat, lon, azi


def test_right_of_track_reads_negative_cdi_and_magnetic_course(legs):
    nav = _nav(legs, "PAMJI")
    lat, lon, azi = _on_leg(legs, 3, 3.0, side_nm=0.5)
    o = nav.update(0.0, lat, lon, 2500.0, 110.0, azi)
    assert o.xtk_nm == pytest.approx(0.5, abs=1e-3)
    assert o.scale_nm == 1.0 and o.cdi == pytest.approx(-0.5, abs=1e-3)
    assert o.crs_mag == pytest.approx(refnav.wrap360(o.dtk_true + MV))


@pytest.mark.parametrize("d_faf,scale", [(2.0, 1.0), (1.0, 0.65), (0.5, 0.475), (0.1, 0.335)])
def test_cdi_scale_ramps_over_the_two_miles_before_the_faf(legs, d_faf, scale):
    nav = _nav(legs, "CIMSO")
    k = nav.i
    lat, lon, azi = _on_leg(legs, k, d_faf + 1e-6)
    o = nav.update(0.0, lat, lon, 2200.0, 110.0, azi)
    assert o.wpt == "CIMSO" and o.scale_nm == pytest.approx(scale, abs=1e-4)


def test_fly_by_fix_sequences_at_turn_anticipation_fly_over_abeam(legs):
    # HANNU is fly-by into HANNU-WINCH: sequences R*tan(delta/2) early
    nav = _nav(legs, "HANNU", position=(34.90, -120.44))
    hannu, winch = legs[8], legs[9]
    line = nav._line
    d_out = refnav.inverse(hannu.lat, hannu.lon, winch.lat, winch.lon)[1]
    _, _, az_in = line.position(line.length_nm)
    lead = refnav.turn_radius_nm(110.0) * math.tan(math.radians(abs(refnav.wrap180(d_out - az_in)) / 2))
    lat, lon, _ = line.position(line.length_nm - lead - 0.01)
    assert nav.update(0.0, lat, lon, 3000.0, 110.0, az_in).wpt == "HANNU"
    lat, lon, _ = line.position(line.length_nm - lead + 0.01)
    assert nav.update(1.0, lat, lon, 3000.0, 110.0, az_in).wpt == "WINCH"
    # WINCH (TF) is coded fly-over: still WINCH TF 0.05 nm before, the hold only once abeam
    k = nav.i
    lat, lon, azi = _on_leg(legs, k, 0.05)
    o = nav.update(2.0, lat, lon, 4000.0, 110.0, azi)
    assert (o.wpt, o.pt) == ("WINCH", "TF")
    lat, lon, azi = _on_leg(legs, k, -0.01)
    o = nav.update(3.0, lat, lon, 4000.0, 110.0, azi)
    assert (o.wpt, o.pt) == ("WINCH", "HM")


def test_map_suspends_until_the_pilot_goes_missed(legs):
    nav = _nav(legs, "RW30")
    k = nav.i
    lat, lon, azi = _on_leg(legs, k, -0.3)
    o = nav.update(0.0, lat, lon, 330.0, 110.0, azi)
    assert nav.map_suspended and o.wpt == "RW30" and o.scale_nm == 0.3
    o = nav.update(1.0, lat, lon, 900.0, 110.0, azi)        # altitude alone never leaves the MAP
    assert o.wpt == "RW30"
    nav.go_missed(2.0, (lat, lon))
    o = nav.update(2.0, lat, lon, 400.0, 110.0, azi)
    assert (o.pt, o.wpt, o.scale_nm) == ("CA", "", 1.0)
    assert o.crs_mag == pytest.approx(316.5, abs=0.2)
    o = nav.update(3.0, lat, lon, 661.0, 110.0, azi)          # CA ends at the altitude
    assert (o.pt, o.wpt) == ("DF", "HANNU")


def test_go_missed_before_the_map_is_refused(legs):
    nav = _nav(legs, "ANIBY")
    with pytest.raises(refnav.ReferenceError_):
        nav.go_missed(0.0, (34.8, -120.4))


def _hold(turn, inbound, arrival):
    leg = refnav.RefLeg("HM", "X", 35.0, -120.0, course_true=inbound, turn=turn, leg_nm=4.0)
    return refnav._Hold(leg, 0.6, arrival, 4.0)


@pytest.mark.parametrize("turn,arrival,entry", [
    # arrival track relative to the outbound course (inbound + 180)
    ("R", 180.0, "DIRECT"),        # straight in on the inbound course
    ("R", 0.0 - 30.0, "TEARDROP"),  # 30 deg toward the holding side (left of outbound for right turns)
    ("R", 0.0 + 30.0, "PARALLEL"),
    ("L", 0.0 + 30.0, "TEARDROP"),
    ("L", 0.0 - 30.0, "PARALLEL"),
    ("L", 0.0 - 120.0, "DIRECT"),
    ("R", 0.0 + 120.0, "DIRECT"),
])
def test_hold_entry_sectors(turn, arrival, entry):
    inbound = 180.0                 # outbound 000
    assert _hold(turn, inbound, refnav.wrap360(arrival)).entry == entry


def test_hold_racetrack_guidance_is_centred_on_every_element():
    h = _hold("L", 90.0, 90.0)        # inbound east, holding side north; straight in, direct entry
    assert h.entry == "DIRECT"
    R, L = h.R, h.L

    def at(x, y):
        # local plane -> lat/lon through the same azimuthal-equidistant map
        d = math.hypot(x, y)
        return refnav.direct(h.leg.lat, h.leg.lon, math.degrees(math.atan2(x, y)), d)

    # walk the racetrack: turn 1 top, outbound middle, turn 2 far end, inbound middle
    for (x, y), phase, dtk in [((R, R), "TURN1", 0.0), ((-L / 2, 2 * R), "OUTBOUND", 270.0),
                               ((-L - R, R), "TURN2", 180.0), ((-L / 2, 0.0), "INBOUND", 90.0),
                               ((0.01, 0.0), "TURN1", 90.0)]:
        lat, lon = at(x, y)
        g_dtk, xtk, _ = h.guidance(lat, lon, 1.83)
        assert h.phase == phase
        assert xtk == pytest.approx(0.0, abs=2e-3)
        assert abs(refnav.wrap180(g_dtk - dtk)) < 1.5
    assert h.circuits == 1
