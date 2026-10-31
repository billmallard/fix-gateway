"""The bench's own bus log of the FP8 X-Plane flight (AER-809 session 2,
``cbbeafe57646``): the recorder output beside the truth trace (SYN4).

It pins the stand-in: the gate's expected values come from the trace's
navigator columns, mapped the way the bench's ``xplane`` plugin maps them.
Here they are compared with the ``EXTCRS``/``EXTCDI`` that plugin actually
wrote on the bus during the same flight.
"""

import bisect

import pytest

from .xplane_replay import ext_cdi, load_bench_bus, load_trace, nm


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
    # XPB-008. EXTTF is in the contract but connections/xplane.yaml does not map it;
    # the gate fills it from the trace's gps_fromto. If this fails, the bench
    # maps it now: compare it with gps_fromto like EXTCRS above.
    assert {b["EXTTF"] for b in load_bench_bus()} == {"0"}
