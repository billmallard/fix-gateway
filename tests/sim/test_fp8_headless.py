"""FP8 session 2 (AER-809) flown headless: the generated stream through the
navigation engine, compared with the reference per the profile's checks.

The engine and the reference agree from PAMJI through the MAP, the missed
approach and HANNU-WINCH with both offsets. They do not agree at WINCH and
in the hold, for two engine behaviours filed as fix-gateway issues (see
the xfail below). The full-profile test is a strict xfail so it turns red
-- and the marker has to go -- the day the engine changes.
"""

from pathlib import Path

import pytest

from fixgw.sim import checks, generator
from fixgw.sim.profile import load_profile

from .engine_replay import replay

PROFILE = Path(__file__).resolve().parents[2] / "src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml"
HOLD_ISSUES = "fix-gateway#44 (fly-over WINCH anticipated), #45 (hold outbound on the inbound line)"


@pytest.fixture(scope="module")
def flown():
    prof = load_profile(str(PROFILE))
    rows = replay(generator.generate(prof).rows)
    return prof, rows


def _results(prof, rows):
    log = checks.Log.from_rows(rows, checks.keys_needed(prof.checks) | {"FPLMSG"})
    return checks.evaluate(prof.checks, log), log


def test_route_loads_and_activates_without_a_message(flown):
    _, rows = flown
    r = rows[1][1]
    assert (r["FPLMSG"], r["FPLCMDACK"], r["FPLACTLEG"], r["WPNAME"]) == ("", 1, 4, "PAMJI")


def test_engine_matches_the_reference_up_to_the_winch_hold(flown):
    prof, rows = flown
    # everything but the hold and the last half mile into WINCH, where the
    # engine sequences early
    upto = [(t, r) for t, r in rows
            if r["REFPT"] != "HM" and not (r["REFWPT"] == "WINCH" and r["REFDIS"] < 0.5)]
    results, _ = _results(prof, upto)
    failed = [f"{r.label}: {r.detail}" for r in results if not r.passed]
    assert failed == []


@pytest.mark.xfail(strict=True, reason=HOLD_ISSUES)
def test_every_profile_check_passes(flown):
    prof, rows = flown
    results, _ = _results(prof, rows)
    assert [r.label for r in results if not r.passed] == []
