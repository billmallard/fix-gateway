"""The demo route (DEMO5, AER-5802): one continuous flight with every segment
the demo brief lists (``briefs/demo_mode_redesign.md`` section 3.2), so the
loop never teleports. The gate flies
``demo5-ksba-v27-ksmx-rnav30-missed.yaml`` through the plugin, because of its
GPS loss; these pin what the gate's checks do not name."""

from pathlib import Path

import pytest

from fixgw.sim import checks, generator
from fixgw.sim.profile import load_profile

from . import engine_replay

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "src/fixgw/sim/profiles/demo5-ksba-v27-ksmx-rnav30-missed.yaml"


@pytest.fixture(scope="module")
def prof():
    return load_profile(str(PROFILE))


def test_the_route_has_every_segment_of_the_loop(prof):
    """Climb-out, V27, the GPS loss, KSMX RNAV 30, the missed to WINCH and
    the hold, in that order, in one profile."""
    assert prof.start.get("runway") == "RW07" and prof.climb_out["runway"] == "RW07"
    assert [e["kind"] for e in prof.enroute] == ["waypoint", "airway"]
    assert prof.enroute[1]["id"] == "V27"
    assert prof.procedure == {"airport": "KSMX", "approach": "R30", "transition": "GVO", "missed": True}
    assert [(e.id, next(iter(e.do))) for e in prof.events] == [("gps_loss", "gps_loss_s"), ("hold", "hold_min")]
    assert prof.events[1].at == {"hold_at": "WINCH"}


def test_through_the_engine_the_course_agrees_to_half_a_degree(prof):
    """The gate judges FPLCRS to 1 deg with one-update transients forgiven:
    through the plugin, guidance cycles are rate limited and a 1 s timer
    cycle runs on the last position, so in a hold turn (3 deg/s) the course
    trails the reference by up to an update. The same stream through the
    engine, in step with the reference, holds the 0.5 deg the other
    profiles use, with nothing forgiven: the wider bound is the plugin's
    timing, not the route's geometry."""
    crs = dict(next(c for c in prof.checks if c.get("key_pair") == ["FPLCRS", "REFCRS"]))
    crs["tol_deg"] = 0.5
    del crs["tolerate_s"]
    rows = engine_replay.replay(generator.generate(prof).rows)
    [res] = checks.evaluate([crs], checks.Log.from_rows(rows, checks.keys_needed([crs])))
    assert res.passed, res.detail
    assert res.transients == 0 and res.compared > 19000
