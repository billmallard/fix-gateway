"""FPLfTA, each slot's fly-by turn anticipation (TURN3, AER-5418).

The map draws a fly-by turn as the arc tangent to the leg into the fix and
the leg out of it, d_ta from the fix on both. The engine publishes d_ta per
slot so the map draws the turns the engine will fly and no others, and so
the arc it draws through the turn in progress is the one the needle reads
from (brief section 3.3.1, row 2).
"""

import math

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine

from .routes import load
from .test_engine import RATE_120, nm_to_deg_lon, wp


def _delta(prev, fix, nxt):
    in_c = geo.wrap360(geo.bearing_deg(fix.lat, fix.lon, prev.lat, prev.lon) + 180.0)
    out_c = geo.bearing_deg(fix.lat, fix.lon, nxt.lat, nxt.lon)
    return abs((out_c - in_c + 180.0) % 360.0 - 180.0)


def _route(d_flags=0, c_turn=45.0):
    # A -> B east 60 nm, 90 deg left at B to C (10 nm north), c_turn right
    # at C to D, 30 deg left at D to E.
    b = wp("B", 0.0, nm_to_deg_lon(60.04))
    c = wp("C", *geo.destination_point(b.lat, b.lon, 0.0, 10.0))
    d = wp("D", *geo.destination_point(c.lat, c.lon, c_turn, 10.0), flags=d_flags)
    e_ = wp("E", *geo.destination_point(d.lat, d.lon, geo.wrap360(c_turn - 30.0), 10.0))
    return [wp("A", 0.0, 0.0), b, c, d, e_]


def _active(route, gs=120.0):
    e = engine.Engine()
    load(e, route, "T", 1)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))
    e.update(0.0, route[1].lon - nm_to_deg_lon(20.0), gs, 0.0, True, None, None, 1000.0)
    return e


def _arc_from_ta(prev, fix, nxt, d_ta):
    """The map's construction (pyEfis leg_paths.flyby_arc): the arc's
    center and exit point from d_ta and the two legs, as _fly_by_arc."""
    in_c = geo.wrap360(geo.bearing_deg(fix.lat, fix.lon, prev.lat, prev.lon) + 180.0)
    out_c = geo.bearing_deg(fix.lat, fix.lon, nxt.lat, nxt.lon)
    delta = (out_c - in_c + 180.0) % 360.0 - 180.0
    half = math.radians(abs(delta)) / 2.0
    back = geo.wrap360(in_c + 180.0)
    inside = geo.wrap360(back + ((out_c - back + 180.0) % 360.0 - 180.0) / 2.0)
    center = geo.destination_point(fix.lat, fix.lon, inside, d_ta / math.sin(half))
    exit_ = geo.destination_point(fix.lat, fix.lon, out_c, d_ta)
    return center, d_ta / math.tan(half), exit_


def test_every_flyby_turn_ahead_is_listed_and_nothing_else():
    route = _route()
    e = _active(route)
    ta = e.turn_anticipations(120.0, 0.0)
    assert len(ta) == 5
    assert ta[0] == 0.0     # the first slot has no leg into it
    for k in (1, 2, 3):
        delta = _delta(route[k - 1], route[k], route[k + 1])
        assert ta[k] == pytest.approx(engine.turn_anticipation_nm(120.0, delta), abs=1e-9), k
    assert ta[1] == pytest.approx(0.783, abs=1e-3)
    assert ta[4] == 0.0     # the last slot never sequences


def test_fixes_ahead_follow_the_groundspeed():
    route = _route()
    e = _active(route)
    slow, fast = e.turn_anticipations(90.0, 0.0), e.turn_anticipations(150.0, 0.0)
    assert slow[2] == pytest.approx(engine.turn_anticipation_nm(90.0, _delta(*route[1:4])), abs=1e-9)
    assert fast[2] > slow[2]


@pytest.mark.parametrize("case", ["flyover", "over_120", "map", "vector_next"])
def test_no_arc_where_the_engine_does_not_anticipate(case):
    route = _route()
    if case == "flyover":
        route[3].flags = engine.FLAG_FLYOVER
    elif case == "over_120":
        route = _route(c_turn=150.0)    # a 150 deg right turn at C
    elif case == "map":
        route[3].flags = engine.FLAG_MAP
    else:
        route[3].pt = "VM"
        route[3].crs = 90.0
    e = _active(route)
    ta = e.turn_anticipations(120.0, 0.0)
    for k in {"flyover": (3,), "over_120": (2,), "map": (3,), "vector_next": (2, 3)}[case]:
        assert ta[k] == 0.0, k
    assert ta[1] > 0.0      # the control: B's turn is still there


def test_no_arc_with_no_leg_active_or_while_suspended():
    route = _route()
    e = engine.Engine()
    load(e, route, "T", 1)
    assert e.turn_anticipations(120.0, 0.0) == [0.0] * 5
    e = _active(route)
    pos = (0.0, route[1].lon - nm_to_deg_lon(20.0))
    assert e.handle_command("2 SUSP", (*pos, True))[0] == 2
    e.update(*pos, 120.0, 0.0, True, None, None, 1001.0)
    ta = e.turn_anticipations(120.0, 0.0)
    assert ta[1] == 0.0
    assert ta[2] > 0.0      # the turns after the suspended fix stay drawn


def test_active_fix_after_a_direct_to_turns_from_the_direct_leg():
    # DTO C from a point south-west of B: the leg into C runs north-east,
    # so C's turn onto C->D is 45 deg less than on the route (90 + 45 deg).
    route = _route()
    e = engine.Engine()
    load(e, route, "T", 1)
    start = geo.destination_point(route[2].lat, route[2].lon, 225.0, 20.0)
    assert e.handle_command("1 DTO 3", (*start, True))[0] == 1
    e.update(*start, 120.0, 0.0, True, None, None, 1000.0)
    ta = e.turn_anticipations(120.0, 0.0)
    fr = wp("", *start)
    assert ta[2] == pytest.approx(engine.turn_anticipation_nm(120.0, _delta(fr, route[2], route[3])), abs=1e-9)
    assert ta[1] == 0.0


def test_turn_in_progress_publishes_the_arc_the_needle_reads_from():
    # From turn start to the arc's end, B's d_ta is the one the arc was
    # fixed with: a change of groundspeed moves the turns ahead, not B's.
    # Built back into an arc the map's way, it is the engine's _turn_arc.
    route = _route()
    e = _active(route)
    a, b, c = route[0], route[1], route[2]
    lat, lon, hdg, t, dt = 0.0, b.lon - nm_to_deg_lon(3.0), 90.0, 2000.0, 0.25
    seen_before = seen_after = False
    while t < 2150.0:
        out = e.update(lat, lon, 120.0, 0.0, True, None, None, t)
        arc = e._turn_arc
        ta = e.turn_anticipations(150.0, 0.0)
        if arc is not None:
            seen_before |= out["act_leg"] == 2
            seen_after |= out["act_leg"] == 3
            assert ta[1] == pytest.approx(engine.turn_anticipation_nm(120.0, _delta(a, b, c)), abs=1e-9)
            center, r, exit_ = _arc_from_ta(a, b, c, ta[1])
            assert center == pytest.approx((arc["lat"], arc["lon"]), abs=1e-12)
            assert r == pytest.approx(arc["r_nm"], abs=1e-12)
            assert exit_ == pytest.approx((arc["exit_lat"], arc["exit_lon"]), abs=1e-12)
            assert ta[2] == pytest.approx(engine.turn_anticipation_nm(150.0, _delta(b, c, route[3])), abs=1e-9)
        turn = (out["crs"] - hdg + 180.0) % 360.0 - 180.0
        step = RATE_120 * dt
        hdg = geo.wrap360(hdg + max(-step, min(step, turn)))
        lat, lon = geo.destination_point(lat, lon, hdg, 120.0 * dt / 3600.0)
        t += dt
    assert seen_before and seen_after
    # Past the arc's end the turn is over and B's arc goes.
    assert e._turn_arc is None and e.act_leg == 3
    assert e.turn_anticipations(120.0, 0.0)[1] == 0.0


# --- On the bus: FPLfTA through the plugin ----------------------------------


def test_plugin_publishes_fplta_and_paces_groundspeed_rewrites(database, tmp_path):
    import fixgw.plugins.flightplan as flightplan

    from .test_plugin import make_config, write_route

    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    route = _route()
    write_route(database, route, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", route[1].lon - nm_to_deg_lon(20.0))
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")
    pl.thread._run_update_cycle(now=100.0)
    published = [database.read(f"FPL{i}TA")[0] for i in range(1, 6)]
    expected = [round(v, 2) for v in pl.thread.engine.turn_anticipations(120.0, 0.0)]
    assert published == expected
    assert published[1] == 0.78 and published[0] == published[4] == 0.0

    # A groundspeed change moves the turns ahead, at most once a second
    # after the last write (the LAT/LONG writes above ran cycles on the
    # wall clock, so that is the base).
    t0 = pl.thread._ta_time
    database.write("GS", 140.0)
    pl.thread._run_update_cycle(now=t0 + 0.5)
    assert database.read("FPL2TA")[0] == 0.78
    pl.thread._run_update_cycle(now=t0 + 1.1)
    assert database.read("FPL2TA")[0] == round(engine.turn_anticipation_nm(140.0, 90.0), 2)

    # A turn going away is written at once, and so is a shorter route's end.
    database.write("FPLCMD", "2 SUSP")
    pl.thread._run_update_cycle(now=t0 + 1.2)
    assert database.read("FPL2TA")[0] == 0.0
    write_route(database, route[:3], seq=2)
    database.write("FPLCMD", "3 ACT 2")
    pl.thread._run_update_cycle(now=t0 + 1.3)
    assert [database.read(f"FPL{i}TA")[0] for i in (3, 4)] == [0.0, 0.0]
    assert database.read("FPL2TA")[0] > 0.0


def test_fplta_is_a_slot_field_that_never_goes_stale(database):
    # One per slot, like the rest of the block, and tol 0: it is rewritten
    # only when it moves, so a timeout would flag a steady value old.
    for i in (1, 100):
        item = database.get_raw_item(f"FPL{i}TA")
        assert (item.tol, item.min, item.max, item.value[0]) == (0, 0.0, 5.0, 0.0)
    with pytest.raises(KeyError):
        database.get_raw_item("FPL101TA")


def test_a_database_without_fplta_keeps_guidance_running(database, tmp_path, monkeypatch, caplog):
    # A config edited before TURN3 has no FPLfTA: an update leaves an edited
    # database file as it was. The write must not escape into the LAT/LONG
    # callback that runs the cycle, and guidance carries on.
    import fixgw.plugins.flightplan as flightplan

    from .test_plugin import make_config, write_route

    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    real = pl.db_write

    def no_ta(key, value):
        if key.endswith("TA") and key.startswith("FPL"):
            raise KeyError(key)
        real(key, value)

    monkeypatch.setattr(pl, "db_write", no_ta)
    route = _route()
    database.write("GPS_FIX_TYPE", 3)
    database.write("GPS_ACCURACY_HORIZ", 10.0)
    write_route(database, route, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", route[1].lon - nm_to_deg_lon(20.0))
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")
    pl.thread._run_update_cycle()
    pl.thread._run_update_cycle()
    assert database.read("FPLACTLEG")[0] == 2 and database.read("WPNAME")[0] == "B"
    assert database.read("FPLCRS")[0] == pytest.approx(90.0, abs=0.01) and not database.read("FPLCRS")[4]
    assert sum("no FPL2TA" in r.getMessage() for r in caplog.records) == 1
