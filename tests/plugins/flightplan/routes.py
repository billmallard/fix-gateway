"""Shared route-building helpers for the flightplan tests.

RS1 (fix-gateway#39) made the segment table a required part of every
route load. Most tests are about leg geometry and sequencing, not
segments, so they load their route as one waypoint row per slot -- the same
description a plain point list has always had."""

import fixgw.plugins.flightplan.engine as engine


def load(e, route, name, seq, segments=None, **kw):
    """e.load_route with an all-waypoint segment table unless one is given."""
    if segments is None:
        segments = engine.waypoint_segments(route)
    return e.load_route(route, name, seq, segments, **kw)


def hold_circuit_points(fix_lat, fix_lon, inbound_true, dst_nm, turn, gs_kt, past_nm=0.05):
    """Positions just past the end of each part of a hold's racetrack
    (fix-gateway#45), built from the AIM picture rather than read back from
    the engine: the turn outbound ends two standard-rate turn radii abeam
    the fix on the holding side, the outbound leg dst_nm along-track from
    the fix, the turn inbound back on the inbound course dst_nm out.

    Returns (past_turn_out, past_outbound, past_turn_in, past_fix)."""
    import fixgw.geo as geo

    r = gs_kt / 188.5
    outbound = geo.wrap360(inbound_true + 180.0)
    side = geo.wrap360(inbound_true + (90.0 if turn == "R" else -90.0))
    s_lat, s_lon = geo.destination_point(fix_lat, fix_lon, side, 2.0 * r)
    past_turn_out = geo.destination_point(s_lat, s_lon, outbound, past_nm)
    past_outbound = geo.destination_point(s_lat, s_lon, outbound, dst_nm + past_nm)
    end_lat, end_lon = geo.destination_point(fix_lat, fix_lon, outbound, dst_nm)
    past_turn_in = geo.destination_point(end_lat, end_lon, inbound_true, past_nm)
    past_fix = geo.destination_point(fix_lat, fix_lon, inbound_true, past_nm)
    return past_turn_out, past_outbound, past_turn_in, past_fix
