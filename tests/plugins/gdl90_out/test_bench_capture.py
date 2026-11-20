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
    """The bus readings just before and just after time t."""
    before = [b for b in bus if b["t"] <= t]
    after = [b for b in bus if b["t"] > t]
    if not before or not after:
        return None
    return before[-1]["bus"], after[0]["bus"]


def within(x, a, b, tol):
    lo, hi = min(a, b), max(a, b)
    return lo - tol <= x <= hi + tol


def angle_within(x, a, b, tol):
    # unwrap a and b onto x's side of the circle
    def near(v):
        return x + ((v - x + 180.0) % 360.0) - 180.0

    return within(x, near(a), near(b), tol)


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
    for t, m in msgs:
        if m[0] != 0x0A:
            continue
        hb = [x for tt, x in msgs if tt == t and x[0] == 0x00]
        ar = around(bus, t)
        if ar is None or not hb:
            continue
        a, b = ar
        r = gdl90.parse_report(m)
        gps_valid = gdl90.parse_heartbeat(hb[0]).gps_valid
        pos = [valid(x["LAT"]) and valid(x["LONG"]) for x in (a, b)]
        if pos[0] != pos[1]:
            counts["edge"] += 1  # the plugin read between a change; either is right
            continue
        if not pos[0]:
            assert not gps_valid and r.pos_bad, t
            counts["invalid"] += 1
            continue
        counts["valid"] += 1
        assert gps_valid and not r.pos_bad, t
        assert within(r.lat, float(a["LAT"][0]), float(b["LAT"][0]), LSB), t
        assert within(r.lon, float(a["LONG"][0]), float(b["LONG"][0]), LSB), t
        if valid(a["PALT"]) and valid(b["PALT"]):
            assert within(r.alt, float(a["PALT"][0]), float(b["PALT"][0]), 12.5), t
        if valid(a["GS"]) and valid(b["GS"]):
            assert within(r.gs, float(a["GS"][0]), float(b["GS"][0]), 0.5), t
        if valid(a["VS"]) and valid(b["VS"]):
            assert within(r.vs, float(a["VS"][0]), float(b["VS"][0]), 32), t
        if valid(a["TRACK"]) and valid(b["TRACK"]):
            assert angle_within(r.trk, float(a["TRACK"][0]), float(b["TRACK"][0]), 0.71), t
    return counts


def test_ownship_matches_the_bus(capture):
    counts = check_ownship(capture)
    # Not vacuous: the capture holds both a live position and none.
    assert counts["valid"] >= 10 and counts["invalid"] >= 3, counts


def test_ahrs_matches_the_bus(capture):
    gdl, bus = capture
    n = 0
    for t, m in decoded(gdl):
        if m[:2] != b"\x65\x01":
            continue
        ar = around(bus, t)
        if ar is None:
            continue
        a, b = ar
        roll = int.from_bytes(m[2:4], "big", signed=True)
        pitch = int.from_bytes(m[4:6], "big", signed=True)
        hdg = int.from_bytes(m[6:8], "big")
        both = {k: valid(a[k]) and valid(b[k]) for k in ("ROLL", "PITCH", "HEAD")}
        none = {k: not valid(a[k]) and not valid(b[k]) for k in ("ROLL", "PITCH", "HEAD")}
        if both["ROLL"]:
            assert within(roll / 10, float(a["ROLL"][0]), float(b["ROLL"][0]), 0.05), t
        elif none["ROLL"]:
            assert roll == 0x7FFF, t
        if both["PITCH"]:
            assert within(pitch / 10, float(a["PITCH"][0]), float(b["PITCH"][0]), 0.05), t
        if both["HEAD"]:
            assert hdg & 0x8000
            assert angle_within((hdg & 0x7FFF) / 10, float(a["HEAD"][0]), float(b["HEAD"][0]), 0.05), t
        elif none["HEAD"]:
            assert hdg == 0xFFFF, t
        n += 1
    assert n >= 50
