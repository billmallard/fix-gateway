"""TURN2b (AER-5324): a recorded X-Plane flight that cuts a 154 deg turn short.

V27 slice 1, session ``d419efeeea66`` (README.md beside the fixture):
KSBA GVO GOLET, a 153.8 deg left turn at GVO. X-Plane's own GPS anticipated
it, so the aircraft began turning 1.22 nm before GVO and came no nearer than
0.74 nm. Brief 3.3.1 row 3 flies a turn over 120 deg as a fly-over and
sequences abeam the fix, and this aircraft never gets abeam. Replayed into
the engine before TURN2b, it stayed on GVO for the whole 25 minutes while
the aircraft flew on to VTU. Bill's card of 2026-11-12: it now also
sequences once the aircraft has passed its closest approach to the fix.
"""

import csv
import gzip
import json

import pytest

from fixgw import geo

from .xplane_replay import FIXTURES, fly_route, xplane_sequences

TRACE = FIXTURES / "turn2b_v27_gvo_xplane_2026-11-01.csv.gz"
ROUTE_BLOCK = FIXTURES / "v27_route_block.json"
ACT = 2  # the leg KSBA -> GVO
# FIX MAGVAR as the AER-5172 replay wrote it for this session.
MAGVAR = -11.67


def load_trace(path=TRACE):
    """The trace's rows. It carries X-Plane's waypoint and course but not its
    deviation or TO/FROM, which this test does not compare."""
    with gzip.open(path, "rt", newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            for k in ("t", "lat", "lon", "alt_ft", "gs_kt", "trk", "xp_crs"):
                r[k] = float(r[k])
            r["xp_hdef_dot"] = 0.0
            r["xp_fromto"] = 1
            rows.append(r)
        return rows


@pytest.fixture(scope="module")
def flight():
    rows = load_trace()
    block = json.loads(ROUTE_BLOCK.read_text())
    samples, events = fly_route(rows, block, ACT, MAGVAR)
    return rows, block, samples, events


def test_the_aircraft_never_gets_abeam_gvo(flight):
    # Why row 3's abeam rule alone never fired here.
    rows, block, _, _ = flight
    a = (block["FPL1LAT"], block["FPL1LON"])
    g = (block["FPL2LAT"], block["FPL2LON"])
    atd = [geo.along_track_distance_to_waypoint_nm(*a, *g, r["lat"], r["lon"]) for r in rows]
    assert min(atd) > 0.5


def test_the_engine_sequences_every_fix_x_plane_did_within_10_s(flight):
    rows, _, _, events = flight
    eng = [(t, text.split(" -> ")[1]) for t, kind, text, _, _ in events if kind == "sequence"]
    xp = [(t, to) for t, _, to, _, _ in xplane_sequences(rows)]
    assert [to for _, to in eng] == [to for _, to in xp] == ["GOLET", "KWANG", "DEANO", "HENER", "VTU"]
    for (te, _), (tx, _) in zip(eng, xp):
        assert abs(te - tx) <= 10.0


def test_gvo_sequences_after_the_closest_approach_not_before(flight):
    rows, block, samples, events = flight
    g = (block["FPL2LAT"], block["FPL2LON"])
    t_seq = next(t for t, kind, text, _, _ in events if kind == "sequence" and text == "GVO -> GOLET")
    dist = [(r["t"], geo.distance_nm(r["lat"], r["lon"], *g)) for r in rows if r["t"] < t_seq + 60.0]
    t_closest, closest = min(dist, key=lambda d: d[1])
    assert closest == pytest.approx(0.741, abs=0.005)
    assert t_closest < t_seq < t_closest + 10.0
    after = next(s for s in samples if s["t"] >= t_seq)
    assert after["wp_name"] == "GOLET"
