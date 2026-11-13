"""TURN2 (AER-4760): the engine and the reference fly a fly-by turn over
120 deg the same way, as a fly-over (brief 3.3.1 row 3; Bill's card,
2026-11-04). No CIFP procedure turns that far, so no profile can carry the
case: a pilot-built reversal is the only way to reach it. This flies one
track through both sides and compares them every second, the way the
profile checks would.

Like ``engine_replay.py``, this lives in tests/ because it touches the
engine; the reference still imports nothing from it.
"""

import math

import pytest

from fixgw.plugins.flightplan.engine import Engine, Waypoint
from fixgw.sim import refnav

from ..plugins.flightplan.routes import load

GS_KT = 110.0


def _legs(turn_deg):
    # 10 nm north into B, then a turn_deg right turn at B to C, 10 nm on.
    b = (34.8, -120.4)
    a = refnav.direct(*b, 180.0, 10.0)
    c = refnav.direct(*b, turn_deg, 10.0)
    return [refnav.RefLeg("IF", "A", *a), refnav.RefLeg("TF", "B", *b), refnav.RefLeg("TF", "C", *c)]


def _track(legs, turn_deg):
    """One position a second: on A->B from 3 nm before B, a standard-rate
    right turn from B, then straight on the rolled-out heading."""
    b = legs[1]
    _, _, azi_in = refnav.inverse(legs[0].lat, legs[0].lon, b.lat, b.lon)
    lat, lon = refnav.direct(b.lat, b.lon, azi_in + 180.0, 3.0)
    hdg, turned, step = azi_in, 0.0, GS_KT / 3600.0
    out = []
    for _ in range(240):
        out.append((lat, lon, hdg))
        past_b = refnav.inverse(lat, lon, b.lat, b.lon)[0] < step / 2 or turned > 0.0
        if past_b and turned < turn_deg:
            d = min(3.0, turn_deg - turned)
            hdg, turned = hdg + d, turned + d
        lat, lon = refnav.direct(lat, lon, hdg, step)
    return out


@pytest.mark.parametrize("turn_deg", [150.0, 179.0])
def test_engine_and_reference_fly_a_reversal_the_same_way(turn_deg):
    legs = _legs(turn_deg)
    nav = refnav.ReferenceNavigator(legs, 0.0, (legs[1].lat, legs[1].lon), 1, GS_KT)
    e = Engine()
    load(e, [Waypoint(lg.ident, lg.lat, lg.lon) for lg in legs], "T", 1)
    e.handle_command("1 ACT 2", (legs[0].lat, legs[0].lon, True))
    seen = set()
    for t, (lat, lon, hdg) in enumerate(_track(legs, turn_deg)):
        ref = nav.update(float(t), lat, lon, 3000.0, GS_KT, hdg)
        out = e.update(lat, lon, GS_KT, 0.0, True, None, None, 1000.0 + t)
        seen.add(ref.wpt)
        assert out["wp_name"] == ref.wpt, t
        assert abs((out["crs"] - ref.dtk_true + 180.0) % 360.0 - 180.0) < 0.5, t
        # Sphere against WGS-84 over these 10 nm legs: well inside 0.02 nm.
        assert out["xtk"] == pytest.approx(ref.xtk_nm, abs=0.02), t
    assert seen == {"B", "C"}
    # The overshoot both sides show after the turn: R (1 - cos turn).
    r = GS_KT / 188.5
    assert abs(ref.xtk_nm) == pytest.approx(r * (1 - math.cos(math.radians(turn_deg))), abs=0.05)


def _track_cut_short(legs, turn_deg, turn_before_nm=1.0):
    """One position a second: on A->B from 3 nm before B, a standard-rate
    right turn through turn_deg started turn_before_nm short of B, hand-
    flown or in HDG mode, then straight. It never gets abeam B."""
    b = legs[1]
    _, _, azi_in = refnav.inverse(legs[0].lat, legs[0].lon, b.lat, b.lon)
    lat, lon = refnav.direct(b.lat, b.lon, azi_in + 180.0, 3.0)
    hdg, turned, step = azi_in, 0.0, GS_KT / 3600.0
    out = []
    for _ in range(240):
        out.append((lat, lon, hdg))
        started = refnav.inverse(lat, lon, b.lat, b.lon)[0] <= turn_before_nm or turned > 0.0
        if started and turned < turn_deg:
            d = min(3.0, turn_deg - turned)
            hdg, turned = hdg + d, turned + d
        lat, lon = refnav.direct(lat, lon, hdg, step)
    return out


@pytest.mark.parametrize("turn_deg", [150.0, 179.0])
def test_engine_and_reference_sequence_a_reversal_cut_short_the_same_way(turn_deg):
    # TURN2b (AER-5324): both sides sequence B past the closest approach,
    # in the same second, though the aircraft never gets abeam B.
    legs = _legs(turn_deg)
    nav = refnav.ReferenceNavigator(legs, 0.0, (legs[1].lat, legs[1].lon), 1, GS_KT)
    e = Engine()
    load(e, [Waypoint(lg.ident, lg.lat, lg.lon) for lg in legs], "T", 1)
    e.handle_command("1 ACT 2", (legs[0].lat, legs[0].lon, True))
    seen = []
    for t, (lat, lon, hdg) in enumerate(_track_cut_short(legs, turn_deg)):
        ref = nav.update(float(t), lat, lon, 3000.0, GS_KT, hdg)
        out = e.update(lat, lon, GS_KT, 0.0, True, None, None, 1000.0 + t)
        assert out["wp_name"] == ref.wpt, t
        if not seen or seen[-1] != ref.wpt:
            seen.append(ref.wpt)
    assert seen == ["B", "C"]
