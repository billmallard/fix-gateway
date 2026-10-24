"""Shared route-building helpers for the flightplan tests.

RS1 (fix-gateway#39) made the segment table a required part of every
route load. Most tests are about leg geometry and sequencing, not
segments, so they load their route as a single direct segment -- the same
description a plain point list has always had."""

import fixgw.plugins.flightplan.engine as engine


def load(e, route, name, seq, segments=None, **kw):
    """e.load_route with an all-direct segment table unless one is given."""
    if segments is None:
        segments = engine.direct_segments(route)
    return e.load_route(route, name, seq, segments, **kw)
