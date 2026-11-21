"""The gdl90_out stream recorded on the bench, decoded and checked against the
bus it was encoded from (AER-6142, EFB1 definition of done 2).

Each capture in data/ is what capture_gdl90_out.py recorded as the tablet: the
datagrams a private gateway on the Beelink sent it, and that gateway's keys
over netfix twice a second (data/README.md says where each came from).  Every
frame must decode with the stratux decoder, and each message must carry what
the bus held at the time: within the bus readings either side of it, plus the
field's resolution.
"""

import gzip
import json
from pathlib import Path

import pytest

from fixgw.plugins.stratux import gdl90

DATA = Path(__file__).parent / "data"
CAPTURES = sorted(DATA.glob("gdl90_out_*.jsonl.gz"))
LSB = 180.0 / (1 << 23)


def load(path):
    with gzip.open(path, "rt") as f:
        rows = [json.loads(line) for line in f]
    gdl = [r for r in rows if "gdl" in r]
    bus = [r for r in rows if "bus" in r and "_error" not in r["bus"]]
    return gdl, bus


def frames(datagram):
    """The raw frames between flags, for counting against what decodes."""
    return [p for p in datagram.split(b"\x7e")[1:-1] if p]


def valid(entry):
    value, flags = entry
    return flags != "missing" and not any(c != "0" for c in flags[1:4])


def around(bus, t):
    """The bus readings that bracket the plugin's read for a message received
    at t: the next one after t, and the two before it.  The capture reads its
    17 keys over netfix and only then calls recvfrom, so a datagram sent
    during a bus read is stamped after that read although it was encoded from
    earlier data: on 2026-11-21 a longitude came in one 10 Hz feed step
    behind the reading just before it."""
    before = [b for b in bus if b["t"] <= t]
    after = [b for b in bus if b["t"] > t]
    if len(before) < 2 or not after:
        return None
    return before[-2]["bus"], before[-1]["bus"], after[0]["bus"]


def vals(rows, key):
    return [float(r[key][0]) for r in rows]


def swing(values):
    """The largest step between adjacent readings.  Attitude at 5 Hz can
    catch a turning point the 2 Hz bus log steps over (on 2026-11-21 HEAD
    bottomed at 318.5 between readings of 318.62 and 318.58), so an AHRS value
    may sit outside the readings by up to how far the key moved between
    them."""
    return max(abs(b - a) for a, b in zip(values, values[1:]))


def unwrap(x, values):
    return [x + ((v - x + 180.0) % 360.0) - 180.0 for v in values]


# Seconds either side of a LAT/LONG validity change left out of the
# comparisons.  At the sim's air start and quit the feed stalls: on
# 2026-11-21 the bus held one GS reading for 0.55 s, and ROLL (tol 300 ms) was
# written once and went old between two 2 Hz bus readings, so the plugin's
# read fell where no bus reading was taken.  One GS report there read 109 kt
# between readings of 109.73 and 109.68; what the plugin read cannot be seen
# from a 2 Hz log, so it is excluded with the transient, not explained away.
EDGE_S = 2.0


def edges(bus):
    """Times at which the bus's position validity changed."""
    out, last = [], None
    for row in bus:
        v = valid(row["bus"]["LAT"]) and valid(row["bus"]["LONG"])
        if last is not None and v != last:
            out.append(row["t"])
        last = v
    return out


def near_edge(t, edge_times):
    return any(abs(t - e) <= EDGE_S for e in edge_times)


def within(x, values, tol):
    return min(values) - tol <= x <= max(values) + tol


def angle_within(x, values, tol):
    # unwrap the readings onto x's side of the circle
    return within(x, [x + ((v - x + 180.0) % 360.0) - 180.0 for v in values], tol)


pytestmark = pytest.mark.skipif(not CAPTURES, reason="no bench capture committed")


@pytest.fixture(params=CAPTURES, ids=[p.name for p in CAPTURES])
def capture(request):
    return load(request.param)


def decoded(gdl):
    out = []
    for row in gdl:
        d = bytes.fromhex(row["gdl"])
        msgs = gdl90.decode_datagram(d)
        assert len(msgs) == len(frames(d)), "a frame failed its FCS at t=%s" % row["t"]
        assert len(d) <= 1400
        out.extend((row["t"], m) for m in msgs)
    return out


def test_every_frame_decodes(capture):
    gdl, _bus = capture
    assert decoded(gdl)


def test_rates(capture):
    msgs = decoded(capture[0])
    span = msgs[-1][0] - msgs[0][0]
    hb = sum(1 for _t, m in msgs if m[0] == 0x00)
    ahrs = sum(1 for _t, m in msgs if m[:2] == b"\x65\x01")
    assert hb == pytest.approx(span + 1, abs=2)
    assert ahrs == pytest.approx(5 * span, rel=0.05)


def check_ownship(capture):
    gdl, bus = capture
    msgs = decoded(gdl)
    counts = {"valid": 0, "invalid": 0, "edge": 0}
    edge_times = edges(bus)
    for t, m in msgs:
        if m[0] != 0x0A:
            continue
        if near_edge(t, edge_times):
            counts["edge"] += 1
            continue
        hb = [x for tt, x in msgs if tt == t and x[0] == 0x00]
        ar = around(bus, t)
        if ar is None or not hb:
            continue
        a, b = ar[0], ar[-1]
        r = gdl90.parse_report(m)
        gps_valid = gdl90.parse_heartbeat(hb[0]).gps_valid
        pos = [valid(x["LAT"]) and valid(x["LONG"]) for x in ar]
        if len(set(pos)) > 1:
            counts["edge"] += 1  # the plugin read between a change; either is right
            continue
        if not pos[0]:
            assert not gps_valid and r.pos_bad, t
            counts["invalid"] += 1
            continue
        counts["valid"] += 1
        assert gps_valid and not r.pos_bad, t
        assert within(r.lat, vals(ar, "LAT"), LSB), t
        assert within(r.lon, vals(ar, "LONG"), LSB), t
        if all(valid(x["PALT"]) for x in ar):
            assert within(r.alt, vals(ar, "PALT"), 12.5), t
        if all(valid(x["GS"]) for x in ar):
            assert within(r.gs, vals(ar, "GS"), 0.5), t
        if all(valid(x["VS"]) for x in ar):
            assert within(r.vs, vals(ar, "VS"), 32), t
        if all(valid(x["TRACK"]) for x in ar):
            assert angle_within(r.trk, vals(ar, "TRACK"), 0.71), t
    return counts


def test_ownship_matches_the_bus(capture):
    counts = check_ownship(capture)
    # Not vacuous: the capture holds both a live position and none, and the
    # transient windows stay small.
    assert counts["valid"] >= 10 and counts["invalid"] >= 3, counts
    assert counts["edge"] <= 10 * len(edges(capture[1])) + 2, counts


def test_ahrs_matches_the_bus(capture):
    gdl, bus = capture
    n = 0
    edge_times = edges(bus)
    for t, m in decoded(gdl):
        if m[:2] != b"\x65\x01" or near_edge(t, edge_times):
            continue
        ar = around(bus, t)
        if ar is None:
            continue
        a, b = ar[0], ar[-1]
        roll = int.from_bytes(m[2:4], "big", signed=True)
        pitch = int.from_bytes(m[4:6], "big", signed=True)
        hdg = int.from_bytes(m[6:8], "big")
        both = {k: all(valid(x[k]) for x in ar) for k in ("ROLL", "PITCH", "HEAD")}
        none = {k: not any(valid(x[k]) for x in ar) for k in ("ROLL", "PITCH", "HEAD")}
        if both["ROLL"]:
            v = vals(ar, "ROLL")
            assert within(roll / 10, v, 0.05 + swing(v)), t
        elif none["ROLL"]:
            assert roll == 0x7FFF, t
        if both["PITCH"]:
            v = vals(ar, "PITCH")
            assert within(pitch / 10, v, 0.05 + swing(v)), t
        if both["HEAD"]:
            assert hdg & 0x8000
            x = (hdg & 0x7FFF) / 10
            v = unwrap(x, vals(ar, "HEAD"))
            assert within(x, v, 0.05 + swing(v)), t
        elif none["HEAD"]:
            assert hdg == 0xFFFF, t
        n += 1
    assert n >= 50
