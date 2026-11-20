"""HOLDENT1: the next hold's entry on the bus, predicted before the fix.

FPLHOLDSLOT/FPLHOLDENT/FPLHOLDCOMMIT name the next hold in the plan and its
AIM 5-3-8 entry. Until the fix is reached the entry is predicted from the
planned track into the fix, with the same hold_entry call the engine flies;
from the fix on it is the entry being flown. The engine picks the entry
exactly as before (fix-gateway#45): these tests only read it.
"""

import json
import logging

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine

from .routes import load
from .test_plugin import make_config, write_route

# The hold every sector case flies: fix (0, 0), inbound 090, 5 nm legs.
FIX = (0.0, 0.0)

# AIM 5-3-8 sectors for an inbound course of 090, the 16 cases of
# test_engine.py's test_hold_entry_sectors_follow_aim_5_3_8: each sector's
# middle and its edges, both turn directions.
SECTOR_CASES = [
    ("R", 90.0, "DIRECT"),
    ("R", 270.0, "TEARDROP"),
    ("R", 240.0, "TEARDROP"),
    ("R", 200.0, "TEARDROP"),
    ("R", 199.0, "DIRECT"),
    ("R", 300.0, "PARALLEL"),
    ("R", 20.0, "PARALLEL"),
    ("R", 21.0, "DIRECT"),
    ("L", 90.0, "DIRECT"),
    ("L", 270.0, "TEARDROP"),
    ("L", 300.0, "TEARDROP"),
    ("L", 340.0, "TEARDROP"),
    ("L", 341.0, "DIRECT"),
    ("L", 240.0, "PARALLEL"),
    ("L", 160.0, "PARALLEL"),
    ("L", 159.0, "DIRECT"),
]


def back(arrival_true, nm):
    """The point nm before FIX on a track that reaches it on arrival_true."""
    return geo.destination_point(*FIX, geo.wrap360(arrival_true + 180.0), nm)


# On a sector edge the track at the fix of the great circle from a point
# 20 or 40 nm out comes back 1e-11 deg either side of the line, so which
# entry the edge gets is rounding. hold_entry's own test pins the edges
# exactly; flown, they are checked against the table 1e-6 deg inside the
# sector, and on the edge itself only for prediction and commit agreeing.
EDGE_INSIDE = {("R", 20.0): -1e-6, ("R", 200.0): 1e-6, ("L", 160.0): 1e-6, ("L", 340.0): -1e-6}


def hold_wp(turn, pt="HM"):
    return engine.Waypoint("X", *FIX, pt=pt, crs=90.0, dst=5.0, turn=turn)


def entry(out):
    return out["hold_slot"], out["hold_ent"], out["hold_commit"]


def fly_in(e, arrival_true, distances, t0=1000.0):
    """Update at each distance before FIX on the arrival line (negative is
    past it). Returns the outputs."""
    return [e.update(*back(arrival_true, d), 120.0, 0.0, True, None, None, t0 + i)
            for i, d in enumerate(distances)]


# Past Z, past A (the collinear fix the leg into X starts at), then into X.
LEG_DISTANCES = (39.0, 30.0, 20.5, 19.99, 10.0, 1.0, 0.1, -0.01)


def coincident_route(turn, arrival):
    """The missed-approach coding: TF X then HM X, with Z and A on the
    arrival line so the flight is the planned track."""
    return [engine.Waypoint("Z", *back(arrival, 40.0)), engine.Waypoint("A", *back(arrival, 20.0)),
            engine.Waypoint("X", *FIX), hold_wp(turn), engine.Waypoint("B", *back(arrival, -20.0))]


def own_leg_route(turn, arrival):
    """A -> HM X: the hold slot is the leg to its fix."""
    return [engine.Waypoint("Z", *back(arrival, 40.0)), engine.Waypoint("A", *back(arrival, 20.0)),
            hold_wp(turn), engine.Waypoint("B", *back(arrival, -20.0))]


def fly_route(route, arrival):
    e = engine.Engine()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (*back(arrival, 40.0), True))
    return e, fly_in(e, arrival, LEG_DISTANCES)


@pytest.mark.parametrize("build,slot,act_legs", [
    (coincident_route, 4, [2, 2, 2, 3, 3, 3, 3, 4]),
    (own_leg_route, 3, [2, 2, 2, 3, 3, 3, 3, 3]),
], ids=["tf-then-hm", "own-leg"])
@pytest.mark.parametrize("turn,arrival,expected", SECTOR_CASES)
def test_each_aim_sector_predicts_the_entry_it_then_flies(build, slot, act_legs, turn, arrival, expected):
    # The prediction is read from two legs out, from the leg into X, and as
    # the hold starts; flown on the planned track it never changes.
    flown = arrival + EDGE_INSIDE.get((turn, arrival), 0.0)
    e, outs = fly_route(build(turn, flown), flown)
    assert [o["act_leg"] for o in outs] == act_legs
    assert [entry(o) for o in outs] == [(slot, expected, False)] * 7 + [(slot, expected, True)]
    assert e._hold_entry == expected


@pytest.mark.parametrize("build", [coincident_route, own_leg_route], ids=["tf-then-hm", "own-leg"])
@pytest.mark.parametrize("turn,arrival", list(EDGE_INSIDE))
def test_on_a_sector_edge_the_prediction_is_still_the_entry_flown(build, turn, arrival):
    e, outs = fly_route(build(turn, arrival), arrival)
    assert e._hold_entry is not None
    assert {o["hold_ent"] for o in outs} == {e._hold_entry}
    assert outs[-1]["hold_commit"] and not any(o["hold_commit"] for o in outs[:-1])


def test_the_prediction_follows_a_reroute_and_a_direct_to():
    # Right hold at X, inbound 090. Via B west of X the leg into X is on
    # 090: direct. The editor moves B so the leg arrives on 300: parallel.
    # A direct-to X from where the line to X arrives on 240: teardrop, and
    # flying that line into X commits it.
    def route(b):
        return [engine.Waypoint("A", *geo.destination_point(*b, 0.0, 20.0)), engine.Waypoint("B", *b),
                engine.Waypoint("X", *FIX), hold_wp("R")]

    e = engine.Engine()
    load(e, route(back(90.0, 20.0)), "T", 1)
    e.handle_command("1 ACT 2", (*back(90.0, 40.0), True))
    out = e.update(*back(90.0, 40.0), 120.0, 0.0, True, None, None, 1000.0)
    assert entry(out) == (4, "DIRECT", False)

    load(e, route(back(300.0, 20.0)), "T", 2)
    out = e.update(*back(90.0, 40.0), 120.0, 0.0, True, None, None, 1001.0)
    assert e.mode == "NONE"   # B moved, so the active leg is gone (HOLDFIX0); the plan still has its hold
    assert entry(out) == (4, "PARALLEL", False)

    ack, msg = e.handle_command("3 DTO 3", (*back(240.0, 15.0), True))
    assert ack == 3, msg
    outs = fly_in(e, 240.0, (15.0, 5.0, 0.5, -0.01), t0=1002.0)
    assert [entry(o) for o in outs] == [(4, "TEARDROP", False)] * 3 + [(4, "TEARDROP", True)]


def test_a_plan_with_no_hold_publishes_no_entry():
    e = engine.Engine()
    load(e, [engine.Waypoint("A", *back(90.0, 20.0)), engine.Waypoint("B", *FIX)], "T", 1)
    out = e.update(*back(90.0, 30.0), 120.0, 0.0, True, None, None, 1000.0)
    assert entry(out) == (0, "", False)   # loaded, not active
    e.handle_command("1 ACT 2", (*back(90.0, 30.0), True))
    out = e.update(*back(90.0, 10.0), 120.0, 0.0, True, None, None, 1001.0)
    assert entry(out) == (0, "", False)
    stale = e.update(*back(90.0, 10.0), 120.0, 0.0, False, None, None, 1100.0)
    assert stale["fail"] and entry(stale) == (0, "", False)


def test_no_entry_once_the_last_hold_is_left():
    # A -> HF X -> B: the HF exits after its parallel entry, and with no
    # hold after it there is nothing to publish.
    e = engine.Engine()
    load(e, [engine.Waypoint("A", *back(300.0, 20.0)), hold_wp("R", pt="HF"),
             engine.Waypoint("B", *back(90.0, -20.0))], "T", 1)
    e.handle_command("1 ACT 2", (*back(300.0, 20.0), True))
    assert entry(fly_in(e, 300.0, (0.01,))[0]) == (2, "PARALLEL", False)
    assert entry(fly_in(e, 300.0, (-0.01,))[0]) == (2, "PARALLEL", True)
    e.update(*geo.destination_point(*FIX, 270.0, 2.05), 120.0, 0.0, True, None, None, 1010.0)
    out = e.update(*geo.destination_point(*FIX, 90.0, 0.05), 120.0, 0.0, True, None, None, 1011.0)
    assert out["act_leg"] == 3
    assert entry(out) == (0, "", False)


def test_the_committed_entry_survives_a_restart_mid_hold():
    e = engine.Engine()
    load(e, [engine.Waypoint("A", *back(240.0, 20.0)), hold_wp("R"), engine.Waypoint("B", *back(90.0, -20.0))],
         "T", 1)
    e.handle_command("1 ACT 2", (*back(240.0, 20.0), True))
    assert entry(fly_in(e, 240.0, (-0.01,))[0]) == (2, "TEARDROP", True)

    restored = engine.Engine()
    restored.restore_from_dict(json.loads(json.dumps(e.to_persisted_dict())))
    # One cycle further along the teardrop leg, as a restart would find it.
    out = restored.update(*geo.destination_point(*FIX, 240.0, 0.5), 120.0, 0.0, True, None, None, 1100.0)
    assert out["phase"] == "HOLD"
    assert entry(out) == (2, "TEARDROP", True)


def test_a_coded_df_into_the_hold_predicts_from_the_turns_end_once_it_starts():
    # B, then DF C coded R 130 deg off the track into B, then HM C (the
    # geometry of test_df_coded_turn.py's hold-after-DF test). The turn's
    # end, where the arrival track comes from, is fixed only as the DF
    # starts, from the GS then. Before that the line from the aircraft to
    # C stands in; from the DF's start the prediction is the entry flown.
    gs, dt = 120.0, 0.2
    b = (34.0, -118.0)
    a = geo.destination_point(*b, 270.0, 10.0)
    c = geo.destination_point(*b, 90.0 - 130.0, 6.0)
    probe = engine.Engine()
    probe._df_turn_start(engine.Waypoint("C", *c, pt="DF", turn="R"), engine.Point(*b), 90.0, gs)
    t = probe._df_turn
    via_turn = geo.wrap360(geo.bearing_deg(*c, t["exit_lat"], t["exit_lon"]) + 180.0)
    via_b = geo.wrap360(geo.bearing_deg(*c, *b) + 180.0)
    outbound = geo.wrap360(via_b + ((via_turn - via_b + 180.0) % 360.0 - 180.0) / 2.0)
    inbound = geo.wrap360(outbound + 180.0)
    flown = engine.hold_entry(inbound, "R", via_turn)
    assert flown != engine.hold_entry(inbound, "R", via_b)

    route = [engine.Waypoint("A", *a), engine.Waypoint("B", *b), engine.Waypoint("C", *c, pt="DF", turn="R"),
             engine.Waypoint("C", *c, pt="HM", crs=inbound, dst=4.0, turn="R")]
    e = engine.Engine()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (a[0], a[1], True))
    first = e.update(a[0], a[1], gs, 0.0, True, True, 0.01, 1000.0)
    live = geo.wrap360(geo.bearing_deg(*c, *a) + 180.0)
    assert entry(first) == (4, engine.hold_entry(inbound, "R", live), False)

    lat, lon, track, now = a[0], a[1], 90.0, 1000.0
    seen = []
    while e.act_leg < 4 and now < 1000.0 + 900.0:
        out = e.update(lat, lon, gs, 0.0, True, True, 0.01, now)
        if out["act_leg"] == 3:
            seen.append(entry(out))
        cmd = geo.wrap360(out["crs"] + max(-30.0, min(30.0, -out["xtk"] * 100.0)))
        track = geo.wrap360(track + max(-4.0 * dt, min(4.0 * dt, (cmd - track + 180.0) % 360.0 - 180.0)))
        lat, lon = geo.destination_point(lat, lon, track, gs * dt / 3600.0)
        now += dt
    assert e.act_leg == 4
    assert seen and set(seen) == {(4, flown, False)}
    assert entry(out) == (4, flown, True)


def test_a_hold_after_a_vector_leg_predicts_from_the_aircraft_until_resume():
    # VM, then TF X, then HM X: the leg into X starts wherever RESUME finds
    # the aircraft, so until then the line from the aircraft to X stands
    # in, and from RESUME it is the line flown.
    e = engine.Engine()
    load(e, [engine.Waypoint("A", *back(90.0, 30.0)), engine.Waypoint("", 0.0, 0.0, pt="VM", crs=90.0),
             engine.Waypoint("X", *FIX), hold_wp("R")], "T", 1)
    e.handle_command("1 ACT 2", (*back(90.0, 30.0), True))
    out = e.update(*back(240.0, 12.0), 120.0, 0.0, True, None, None, 1000.0)
    assert out["phase"] == "VECTORS"
    assert entry(out) == (4, "TEARDROP", False)
    out = e.update(*back(300.0, 12.0), 120.0, 0.0, True, None, None, 1001.0)
    assert entry(out) == (4, "PARALLEL", False)

    over = e.update(*FIX, 120.0, 0.0, True, None, None, 1002.0)
    assert entry(over) == (4, "", False)   # over the fix: no line to predict from

    e.handle_command("2 RESUME", (*back(300.0, 12.0), True))
    outs = fly_in(e, 300.0, (12.0, 1.0, -0.01), t0=1003.0)
    assert [entry(o) for o in outs] == [(4, "PARALLEL", False)] * 2 + [(4, "PARALLEL", True)]


def test_a_hold_after_a_ca_leg_predicts_from_the_aircraft_not_the_level_off_estimate():
    # A, CA on 240 to 3000 ft, HM X. The CA ends where the aircraft reaches
    # 3000 ft, and the hold's leg to X starts there. The CA's coded
    # position is a level-off estimate, here put where it would read as a
    # parallel entry; the aircraft, on the 240 line into X, will fly a
    # teardrop.
    e = engine.Engine()
    load(e, [engine.Waypoint("A", *back(240.0, 30.0)),
             engine.Waypoint("CALVL", *back(300.0, 12.0), pt="CA", crs=240.0, alt="+3000"),
             hold_wp("R")], "T", 1)
    e.handle_command("1 ACT 2", (*back(240.0, 30.0), True))
    climbing = e.update(*back(240.0, 12.0), 120.0, 0.0, True, None, None, 1000.0, alt_ft=2000.0)
    assert climbing["act_leg"] == 2
    assert entry(climbing) == (3, "TEARDROP", False)
    level = e.update(*back(240.0, 11.0), 120.0, 0.0, True, None, None, 1001.0, alt_ft=3200.0)
    assert level["act_leg"] == 3
    assert entry(level) == (3, "TEARDROP", False)
    assert entry(fly_in(e, 240.0, (-0.01,), t0=1002.0)[0]) == (3, "TEARDROP", True)


def test_the_plugin_writes_the_entry_keys_through_the_database(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    database.write("GPS_FIX_TYPE", 3)
    write_route(database, [engine.Waypoint("A", *back(240.0, 20.0)), engine.Waypoint("X", *FIX), hold_wp("R")])
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)

    def at(nm):
        lat, lon = back(240.0, nm)
        database.write("LAT", lat)
        database.write("LONG", lon)
        pl.thread._run_update_cycle()
        return tuple(database.read(k)[0] for k in ("FPLHOLDSLOT", "FPLHOLDENT", "FPLHOLDCOMMIT"))

    assert at(20.0) == (3, "TEARDROP", False)   # loaded, not active: the plan's hold
    database.write("FPLCMD", "1 ACT 2")
    assert at(10.0) == (3, "TEARDROP", False)
    assert at(-0.01) == (3, "TEARDROP", True)


def test_a_database_without_the_keys_turns_the_output_off_with_one_log_line(database, tmp_path, caplog):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    out = {"hold_slot": 3, "hold_ent": "DIRECT", "hold_commit": False}
    real_get = pl.thread.parent.db_get_item

    def get(key):
        if key.startswith("FPLHOLD"):
            raise KeyError(key)
        return real_get(key)

    pl.thread.parent.db_get_item = get
    with caplog.at_level(logging.WARNING):
        pl.thread._write_hold_entry(out)
        pl.thread._write_hold_entry(out)
    assert sum("predates HOLDENT1" in r.getMessage() for r in caplog.records) == 1
