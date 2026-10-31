"""The bench's own bus log of the FP8 X-Plane flight (AER-809 session 2,
``cbbeafe57646``): the recorder output beside the truth trace (SYN4).

It pins the stand-in: the gate's expected values come from the trace's
navigator columns, mapped the way the bench's ``xplane`` plugin maps them.
Here they are compared with the ``EXTCRS``/``EXTCDI`` that plugin actually
wrote on the bus during the same flight.
"""

import bisect
import math

import pytest

from .xplane_replay import MAGVAR, build_route, ext_cdi, load_bench_bus, load_trace, nm


def w180(a):
    return (a + 180.0) % 360.0 - 180.0


@pytest.fixture(scope="module")
def paired():
    """(bus row, nearest trace row) for every bus row."""
    trace, bus = load_trace(), load_bench_bus()
    tt = [r["t"] for r in trace]
    out = []
    for b in bus:
        i = bisect.bisect_left(tt, b["t"])
        r = min((trace[j] for j in (i - 1, i) if 0 <= j < len(trace)), key=lambda r: abs(r["t"] - b["t"]))
        out.append((b, r))
    return out


def test_the_bus_log_covers_the_trace(paired):
    assert len(paired) > 1200
    # 5 Hz trace, 1 Hz bus: each bus row is within about 0.12 s of a trace row
    assert max(abs(b["t"] - r["t"]) for b, r in paired) <= 0.13


def test_the_clocks_are_aligned_by_position(paired):
    # BUS_EPOCH puts the same aircraft at the same place: 0.006 nm is 0.12 s at 110 kt
    d = [nm(float(b["LAT"]), float(b["LONG"]), r["lat"], r["lon"]) for b, r in paired]
    assert max(d) <= 0.01


def test_extcrs_on_the_bus_is_xplanes_gps_course(paired):
    assert max(abs(w180(float(b["EXTCRS"]) - r["xp_crs"])) for b, r in paired) <= 0.01


def test_extcdi_on_the_bus_is_xplanes_deflection_times_0p4(paired):
    # held to -1..1 like the key; 0.02 covers 0.12 s of a moving needle
    assert max(abs(float(b["EXTCDI"]) - ext_cdi(r["xp_hdef_dot"])) for b, r in paired) <= 0.02
    assert any(abs(float(b["EXTCDI"])) == 1.0 for b, _ in paired)  # the clamp is exercised


def test_the_bench_never_wrote_exttf():
    # XPB-008. This log predates the EXTTF row in connections/xplane.yaml
    # (fix-gateway#41), so the gate fills EXTTF from the trace's gps_fromto.
    # A recording made since should compare it with gps_fromto like EXTCRS.
    assert {b["EXTTF"] for b in load_bench_bus()} == {"0"}


def test_gps_fromto_is_1_to_2_from_as_exttf_declares():
    # fix-gateway#41 maps gps_fromto straight onto EXTTF, so its encoding has
    # to be EXTTF's: 1 when the active waypoint is ahead along X-Plane's
    # course, 2 once it is behind. Checked against this live session's
    # geometry, away from the two places the answer is ambiguous: within
    # 0.1 nm of the fix (abeam) and 2 s after X-Plane sequences (it showed
    # FROM for about 1.5 s on taking WINCH 10 nm out).
    wps = {w.id: w for w in build_route()[0] if w.id}
    trace = load_trace()
    seq_t = [b["t"] for a, b in zip(trace, trace[1:]) if a["xp_wpt"] != b["xp_wpt"]]
    seen, wrong = set(), []
    for r in trace:
        w = wps[r["xp_wpt"]]
        if nm(r["lat"], r["lon"], w.lat, w.lon) < 0.1 or any(0 <= r["t"] - t < 2.0 for t in seq_t):
            continue
        ahead = abs(w180(bearing(r["lat"], r["lon"], w.lat, w.lon) + MAGVAR - r["xp_crs"])) < 90.0
        seen.add(r["xp_fromto"])
        if r["xp_fromto"] != (1 if ahead else 2):
            wrong.append((r["t"], r["xp_wpt"], r["xp_fromto"]))
    assert seen == {1, 2}
    assert wrong == []


def bearing(a_lat, a_lon, b_lat, b_lon):
    """Initial great-circle bearing, degrees true."""
    p1, p2, dl = math.radians(a_lat), math.radians(b_lat), math.radians(b_lon - a_lon)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)))
