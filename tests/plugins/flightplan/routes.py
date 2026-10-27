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
