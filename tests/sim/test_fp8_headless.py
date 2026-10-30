"""FP8 session 2 (AER-809) flown headless: the generated stream through the
navigation engine, compared with the reference per the profile's checks.

The engine and the reference agree for the whole flight: PAMJI through the
MAP, the missed approach, HANNU-WINCH with both offsets, WINCH itself
(fix-gateway#44) and the WINCH hold (#45) -- its parallel entry, the return
to WINCH, the turn outbound and the outbound leg.
"""

import dataclasses
from pathlib import Path

import pytest

from fixgw.sim import checks, generator
from fixgw.sim.profile import load_profile

from .engine_replay import replay

PROFILE = Path(__file__).resolve().parents[2] / "src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml"


@pytest.fixture(scope="module")
def flown():
    prof = load_profile(str(PROFILE))
    rows = replay(generator.generate(prof).rows)
    return prof, rows


def _results(checks_, rows):
    log = checks.Log.from_rows(rows, checks.keys_needed(checks_) | {"FPLMSG"})
    return checks.evaluate(checks_, log), log


def test_route_loads_and_activates_without_a_message(flown):
    _, rows = flown
    r = rows[1][1]
    assert (r["FPLMSG"], r["FPLCMDACK"], r["FPLACTLEG"], r["WPNAME"]) == ("", 1, 4, "PAMJI")


# The profile's own checks are flown by test_profiles.py, with every other
# committed profile (SYN3).


def test_two_full_circuits_of_the_winch_hold_match_the_reference():
    # The profile's five minutes in the hold end on the first outbound leg,
    # so the turn inbound and the inbound leg are never compared there.
    # Twelve minutes covers two full circuits of the racetrack.
    #
    # Every check holds at the profile's tolerance. Course on the turn
    # inbound needed 1.0 deg while the engine built its racetrack on a
    # sphere: the end of the 4 nm outbound leg landed 0.009 nm off its
    # WGS-84 place, and the tangent on the 0.58 nm turn swung by up to
    # atan(0.0092 / 0.5836) = 0.90 deg (measured: 0.93). The racetrack is
    # built on WGS-84 now (AER-4218) and the turn agrees to 0.17 deg.
    prof = load_profile(str(PROFILE))
    prof.events = [dataclasses.replace(e, do=dict(e.do, hold_min=12)) if e.id == "hold" else e
                   for e in prof.events]
    rows = replay(generator.generate(prof).rows)
    results, _ = _results(prof.checks, rows)
    assert [f"{r.label}: {r.detail}" for r in results if not r.passed] == []
