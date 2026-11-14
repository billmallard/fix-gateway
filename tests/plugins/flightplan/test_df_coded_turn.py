"""A DF leg's coded turn direction (AER-4541, QA1 F3).

KSSF RNAV (GPS) 28's missed approach climbs on 279.6 M to 1,100 ft (CA),
then turns RIGHT direct GANLE (DF, turn direction R in the CIFP record).
GANLE lies about 130 deg to the left of the climb-out, so the short way is
left and the procedure says right. Before AER-4541 the engine flew every
DF as the great circle from where it began: at the CA's altitude the
course pointer swung 130 deg left, and an autopilot coupled to it turned
left.

The route here is the real one: the committed cycle 2611 KSSF excerpt the
QA1 profile flies, turned into slots by ``fixgw.sim.fplroute`` exactly as
the synthetic tier loads it onto the bus. The aircraft is a simple coupled
autopilot -- it steers the desired track plus a cross-track correction,
turning at most 4 deg/s the short way to its command -- so it turns
whichever way the guidance points it, which is the question.
"""

from pathlib import Path

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine
from fixgw.sim import fplroute
from fixgw.sim.cifp import Cifp

CIFP = Path(__file__).resolve().parents[3] / "src/fixgw/sim/profiles/cifp/FAACIFP18-2611-KSSF-R28"
MAGVAR = -3.0       # FIX MAGVAR, positive west: the CIFP airport record's E0030
TAS_KT = 110.0
CLIMB_FPM = 700.0
DT = 0.2


def kssf_route():
    slots, segs = fplroute.procedure_slots(Cifp(CIFP), "KSSF", "R28", "XTEND", MAGVAR)
    wps = [engine.Waypoint(s["ID"], s["LAT"], s["LON"], s["TYPE"], s["PT"], s["CRS"], s["DST"], s["ALT"],
                           s["SPD"], s["SGI"], s["FLAGS"], s["CTRLAT"], s["CTRLON"], s["TURN"]) for s in slots]
    segments = [engine.Segment(g["KIND"], g["ID"], g["ENT"], g["EXT"], g["FIRST"], g["LAST"]) for g in segs]
    return wps, segments


def slot_of(wps, pt):
    return next(i for i, w in enumerate(wps, start=1) if w.pt == pt)


def _wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def fly_missed(df_turn="R", seconds=900.0):
    """Climb out on the CA from the runway threshold at 600 ft and fly the
    guidance until GANLE's hold (or `seconds`). Returns the engine, the
    route, and one (t, out, track_true) per cycle from the CA's end."""
    wps, segments = kssf_route()
    ca = slot_of(wps, "CA")
    df = slot_of(wps, "DF")
    wps[df - 1].turn = df_turn
    e = engine.Engine()
    e.load_route(wps, "KSSF R28", 1, segments)
    rw = wps[ca - 2]
    lat, lon, alt = rw.lat, rw.lon, 600.0
    ack, msg = e.handle_command(f"1 ACT {ca}", (lat, lon, True))
    assert ack == 1, msg
    track = geo.wrap360(wps[ca - 1].crs - MAGVAR)
    rows = []
    t = 0.0
    while t < seconds:
        out = e.update(lat, lon, TAS_KT, MAGVAR, True, True, 0.01, 1000.0 + t, alt_ft=alt)
        if e.act_leg >= df:
            rows.append((t, out, track))
        if e.act_leg > df:
            break
        cmd = geo.wrap360(out["crs"] - MAGVAR + max(-30.0, min(30.0, -out["xtk"] * 100.0)))
        track = geo.wrap360(track + max(-4.0 * DT, min(4.0 * DT, _wrap180(cmd - track))))
        lat, lon = geo.destination_point(lat, lon, track, TAS_KT * DT / 3600.0)
        alt = min(3100.0, alt + CLIMB_FPM * DT / 60.0)
        t += DT
    return e, wps, rows


def test_the_kssf_missed_approach_is_coded_to_turn_right_the_long_way_round():
    # The premise, from the CIFP: CA on 279.6, then DF GANLE coded R, with
    # GANLE to the left of the climb-out.
    wps, _ = kssf_route()
    ca, df = wps[slot_of(wps, "CA") - 1], wps[slot_of(wps, "DF") - 1]
    assert (ca.crs, ca.alt, df.id, df.turn) == (279.6, "+1100", "GANLE", "R")
    climb_true = geo.wrap360(ca.crs - MAGVAR)
    # From the CA's end (fplroute puts it 2 nm out on the climb course)
    # GANLE is 130 deg left: right is a 230 deg turn.
    to_ganle = geo.bearing_deg(ca.lat, ca.lon, df.lat, df.lon)
    assert _wrap180(to_ganle - climb_true) == pytest.approx(-130.0, abs=2.0)


def test_kssf_df_ganle_turns_right_as_coded_and_annunciates_it():
    # Red before AER-4541: FPLCRS stepped 130 deg left at the CA's end, the
    # autopilot turned left, and there was no FPLTURN at all.
    _, wps, rows = fly_missed()
    first = rows[0][2]
    # The first 40 s of the DF: the track only ever moves right of the
    # climb-out, and goes on to more than 100 deg right of it.
    early = [_wrap180(trk - first) for t, _, trk in rows if t - rows[0][0] <= 40.0]
    assert min(early) > -1.0
    assert max(early) > 100.0
    # The pilot is told: FPLTURN reads R from the DF's start through the turn.
    assert rows[0][1]["turn"] == "R"
    in_turn = [out for _, out, _ in rows if out["turn"] == "R"]
    assert len(in_turn) * DT > 60.0     # a 230 deg turn at standard rate is ~77 s
    assert all(out["wp_name"] == "GANLE" for out in in_turn)


def test_kssf_df_turn_keeps_a_coupled_autopilot_centred_and_reaches_ganle():
    e, wps, rows = fly_missed()
    # The needle is measured from the path the box steers: a standard-rate
    # circle, then the line to GANLE. Flown coupled, it stays centred.
    assert max(abs(out["xtk"]) for _, out, _ in rows) < 0.05
    # The turn ends and the rest of the DF is the line to GANLE.
    after = [out for _, out, _ in rows if out["turn"] == "" and out["wp_name"] == "GANLE"]
    assert after
    assert e.act_leg == slot_of(wps, "HM")


def test_the_turn_is_coded_right_so_a_left_coding_turns_left():
    # The same leg coded L turns the short way, by the coded turn, and
    # annunciates L: the direction comes from the data, not the geometry.
    _, _, rows = fly_missed(df_turn="L")
    first = rows[0][2]
    early = [_wrap180(trk - first) for t, _, trk in rows if t - rows[0][0] <= 30.0]
    assert max(early) < 1.0 and min(early) < -60.0
    assert rows[0][1]["turn"] == "L"


def test_a_df_with_no_turn_coded_flies_the_short_way_unannunciated():
    # "" is ARINC's "no direction": the great circle from where the leg
    # began, as before AER-4541.
    _, _, rows = fly_missed(df_turn="")
    first = rows[0][2]
    early = [_wrap180(trk - first) for t, _, trk in rows if t - rows[0][0] <= 30.0]
    assert max(early) < 1.0
    assert all(out["turn"] == "" for _, out, _ in rows)


def test_df_turn_direction_e_is_either_way_so_the_short_way():
    _, _, rows = fly_missed(df_turn="E")
    first = rows[0][2]
    early = [_wrap180(trk - first) for t, _, trk in rows if t - rows[0][0] <= 30.0]
    assert max(early) < 1.0
    assert all(out["turn"] == "" for _, out, _ in rows)


def test_a_df_with_an_unreadable_turn_direction_rejects_the_route():
    wps, segments = kssf_route()
    wps[slot_of(wps, "DF") - 1].turn = "X"
    with pytest.raises(engine.RouteRejected) as exc:
        engine.Engine().load_route(wps, "KSSF R28", 1, segments)
    assert exc.value.reason == "BADTURN DF GANLE"


def test_no_fly_by_turn_is_anticipated_into_a_coded_df():
    # The turn onto a coded DF is the DF's own: the fix before it publishes
    # no fly-by arc (FPLfTA 0) and sequences abeam.
    a = (34.0, -118.0)
    b = geo.destination_point(*a, 90.0, 10.0)
    c = geo.destination_point(*b, 0.0, 10.0)
    route = [engine.Waypoint("A", *a), engine.Waypoint("B", *b), engine.Waypoint("C", *c, pt="DF", turn="L")]
    e = engine.Engine()
    e.load_route(route, "T", 1, engine.waypoint_segments(route))
    e.handle_command("1 ACT 2", (a[0], a[1], True))
    near = geo.destination_point(*b, 270.0, 0.5)
    e.update(near[0], near[1], 120.0, 0.0, True, True, 0.01, 1000.0)
    assert e.turn_anticipations(120.0, 0.0)[1] == 0.0
    assert e.act_leg == 2
    past = geo.destination_point(*b, 90.0, 0.01)
    out = e.update(past[0], past[1], 120.0, 0.0, True, True, 0.01, 1001.0)
    # A 270 deg left turn onto C, which is 90 deg to the right.
    assert e.act_leg == 3 and out["turn"] == "L"


def test_a_df_turn_survives_a_restart_mid_turn():
    e, wps, _ = fly_missed(seconds=60.0)
    assert e._leg_phase == engine.LEGPHASE_DF_TURN
    r = engine.Engine()
    r.restore_from_dict(e.to_persisted_dict())
    assert r._df_turn == e._df_turn and r._leg_phase == engine.LEGPHASE_DF_TURN


def test_a_state_file_from_before_the_df_turn_flies_the_line():
    e, _, _ = fly_missed(seconds=60.0)
    d = e.to_persisted_dict()
    del d["df_turn"]
    r = engine.Engine()
    r.restore_from_dict(d)
    assert r._df_turn is None and r._leg_phase is None


def test_the_map_anchor_is_the_tangent_point_through_the_turn():
    # FPLFRLAT/FPLFRLON, which the map draws the active leg from, hold the
    # point the turn rolls out at: one line, the one the DF ends on.
    _, wps, rows = fly_missed()
    df = slot_of(wps, "DF")
    anchors = {(round(out["fr_lat"], 9), round(out["fr_lon"], 9)) for _, out, _ in rows if out["act_leg"] == df}
    assert len(anchors) == 1



def test_a_hold_after_a_coded_df_is_entered_from_the_track_the_turn_rolled_out_on():
    # B, then DF C coded R 130 deg left of the track into B, then a hold at
    # C. The hold's outbound course is put midway between the two arrival
    # tracks -- the line from the turn's tangent point, the way the aircraft
    # came, and the line from B, where the DF began -- so the teardrop/
    # parallel boundary (AIM 5-3-8) lies between them and only the right
    # one gives the entry the engine must pick.
    gs = 120.0
    b = (34.0, -118.0)
    a = geo.destination_point(*b, 270.0, 10.0)
    c = geo.destination_point(*b, 90.0 - 130.0, 6.0)
    probe = engine.Engine()
    probe._df_turn_start(engine.Waypoint("C", *c, pt="DF", turn="R"), engine.Point(*b), 90.0, gs)
    t = probe._df_turn
    via_turn = geo.wrap360(geo.bearing_deg(*c, t["exit_lat"], t["exit_lon"]) + 180.0)
    via_b = geo.wrap360(geo.bearing_deg(*c, *b) + 180.0)
    outbound = geo.wrap360(via_b + _wrap180(via_turn - via_b) / 2.0)
    inbound = geo.wrap360(outbound + 180.0)
    assert engine.hold_entry(inbound, "R", via_turn) != engine.hold_entry(inbound, "R", via_b)

    route = [engine.Waypoint("A", *a), engine.Waypoint("B", *b), engine.Waypoint("C", *c, pt="DF", turn="R"),
             engine.Waypoint("C", *c, pt="HM", crs=inbound, dst=4.0, turn="R")]
    e = engine.Engine()
    e.load_route(route, "T", 1, engine.waypoint_segments(route))
    e.handle_command("1 ACT 2", (a[0], a[1], True))
    lat, lon, track, now = a[0], a[1], 90.0, 1000.0
    while e.act_leg < 4 and now < 1000.0 + 900.0:
        out = e.update(lat, lon, gs, 0.0, True, True, 0.01, now)
        cmd = geo.wrap360(out["crs"] + max(-30.0, min(30.0, -out["xtk"] * 100.0)))
        track = geo.wrap360(track + max(-4.0 * DT, min(4.0 * DT, _wrap180(cmd - track))))
        lat, lon = geo.destination_point(lat, lon, track, gs * DT / 3600.0)
        now += DT
    assert e.act_leg == 4
    assert e._hold_entry == engine.hold_entry(inbound, "R", via_turn)
