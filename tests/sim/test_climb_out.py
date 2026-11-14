"""A route that opens with a departure (DEMO9): the ``climb_out`` route
item, a ``CA`` on the runway heading from the threshold to an altitude, and
``start.runway``. The gate flies ``demo9-ksmx-rw30-climb-out-gvo.yaml``
through the engine; these pin the parts the gate's checks do not name."""

from pathlib import Path

import pytest

from fixgw.sim import fplroute, generator, refnav
from fixgw.sim.cifp import Cifp, CifpError
from fixgw.sim.profile import ProfileError, load_profile

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "src/fixgw/sim/profiles"
PROFILE = PROFILES / "demo9-ksmx-rw30-climb-out-gvo.yaml"
CIFP = PROFILES / "cifp/FAACIFP18-2611-KSMX-RW30-V27-GOLET"

BASE = """
name: t
cifp: {cycle: "2611", file: CIFP}
environment: {magvar_deg: -11.9}
departure: KSMX
destination: KSBA
start: {runway: RW30, alt_ft: 311}
route:
  - climb_out: {runway: RW30, alt_ft: 3000}
  - waypoint: GVO
end: {after_fix: GVO}
"""


def _profile(tmp_path, text):
    p = tmp_path / "p.yaml"
    p.write_text(text.replace("CIFP", str(CIFP)))
    return str(p)


@pytest.fixture(scope="module")
def run():
    return generator.generate(str(PROFILE))


# -- the runway record ----------------------------------------------------------
def test_the_runway_record_gives_the_threshold_bearing_and_elevation():
    # SUSAP KSMXK2GRW30    0080043010 N34533343W120264423 ... 00261 ...
    rwy, bearing, elev = Cifp(CIFP).runway("KSMX", "RW30")
    assert (rwy.ident, rwy.kind) == ("RW30", "runway")
    assert rwy.lat == pytest.approx(34 + 53 / 60 + 33.43 / 3600)
    assert rwy.lon == pytest.approx(-(120 + 26 / 60 + 44.23 / 3600))
    assert (bearing, elev) == (301.0, 261.0)
    with pytest.raises(CifpError, match="no runway record"):
        Cifp(CIFP).runway("KSMX", "RW12")


# -- the loader -------------------------------------------------------------------
def test_the_loader_reads_a_climb_out_and_a_runway_start(tmp_path):
    p = load_profile(_profile(tmp_path, BASE))
    assert p.climb_out == {"runway": "RW30", "alt_ft": 3000.0}
    assert p.enroute == [{"kind": "waypoint", "fix": "GVO"}]
    assert p.start == {"runway": "RW30", "alt_ft": 311}


@pytest.mark.parametrize("change, match", [
    (("  - climb_out: {runway: RW30, alt_ft: 3000}\n  - waypoint: GVO\n",
      "  - waypoint: GVO\n  - climb_out: {runway: RW30, alt_ft: 3000}\n"), "must be the first item"),
    (("departure: KSMX\n", ""), "departure: required with climb_out"),
    (("  - waypoint: GVO\n", "  - procedure: {airport: KSMX, approach: R30, transition: GVO}\n"),
     "put a waypoint after it"),
    (("alt_ft: 3000}", "alt_ft: 3000, course: 301}"), "unknown key"),
    (("alt_ft: 3000}", "alt_ft: lots}"), "climb_out: {runway"),
    (("start: {runway: RW30,", "start: {runway: RW12,"), "not the runway of a climb_out"),
    (("start: {runway: RW30,", "start: {runway: RW30, fix: GVO,"), "exactly one of fix / runway"),
    (("start: {runway: RW30,", "start: {runway: RW30, before_nm: 1,"), "before_nm goes with fix"),
], ids=["not-first", "no-departure", "no-waypoint-after", "unknown-key", "alt-not-a-number",
        "other-runway", "fix-and-runway", "before-nm"])
def test_the_loader_refuses_a_climb_out_it_cannot_fly(tmp_path, change, match):
    old, new = change
    assert old in BASE
    with pytest.raises(ProfileError, match=match):
        load_profile(_profile(tmp_path, BASE.replace(old, new)))


def test_a_runway_start_needs_a_climb_out(tmp_path):
    text = BASE.replace("  - climb_out: {runway: RW30, alt_ft: 3000}\n", "")
    with pytest.raises(ProfileError, match="not the runway of a climb_out"):
        load_profile(_profile(tmp_path, text))


# -- what the box and the reference are given ------------------------------------
def test_the_box_gets_a_departure_row_then_a_df(tmp_path):
    p = load_profile(_profile(tmp_path, BASE))
    slots, segments, _, _ = fplroute.route_slots(Cifp(CIFP), p)
    assert [(s["ID"], s["PT"], s["SGI"]) for s in slots] == [
        ("KSMX", "TF", 1), ("RW30", "TF", 2), ("", "CA", 2), ("GVO", "DF", 3), ("KSBA", "TF", 4)]
    ca = slots[2]
    assert (ca["CRS"], ca["ALT"], ca["FLAGS"]) == (301.0, "+3000", 0)
    # the slot's point is on the runway's course line (true = 301 - MAGVAR)
    d, az, _ = refnav.inverse(slots[1]["LAT"], slots[1]["LON"], ca["LAT"], ca["LON"])
    assert (d, az) == (pytest.approx(2.0), pytest.approx(301.0 + 11.9))
    assert segments[1] == {"KIND": fplroute.SEGKIND_DEPARTURE, "ID": "RW30", "ENT": "RW30", "EXT": "",
                           "FIRST": 2, "LAST": 3}


def test_the_reference_flies_the_ca_from_the_threshold_and_the_next_leg_direct(tmp_path):
    p = load_profile(_profile(tmp_path, BASE))
    legs = refnav.legs_from_route(Cifp(CIFP), p)
    assert [(lg.pt, lg.ident) for lg in legs] == [
        ("TF", "KSMX"), ("TF", "RW30"), ("CA", ""), ("DF", "GVO"), ("TF", "KSBA")]
    assert legs[2].course_true == pytest.approx(312.9) and legs[2].alt_ft == 3000.0


# -- the flight ---------------------------------------------------------------------
def test_the_climb_out_climbs_to_its_altitude_then_sequences(run):
    """From 311 ft over the threshold at the profile's 700 fpm, the CA ends
    on 3,000 ft (2,689 ft / 700 fpm = 230.5 s), and only then does GVO
    become the TO fix."""
    assert run.timeline[0]["cmd"] == "1 ACT 3"
    seq = [e for e in run.timeline if e["what"] == "sequence"]
    assert (seq[0]["from"], seq[0]["to"]) == ("", "GVO")
    assert seq[0]["t"] == pytest.approx(230.5, abs=0.4)
    rows = dict(run.rows)
    before, at = rows[round(seq[0]["t"] - 0.2, 3)], rows[seq[0]["t"]]
    assert (before["REFPT"], before["REFWPT"]) == ("CA", "")
    assert before["ALT"] < 3000.0 <= at["ALT"] + 1e-6
    assert before["VS"] == pytest.approx(700.0)
    assert (at["REFPT"], at["REFWPT"]) == ("DF", "GVO")
    # on the runway's course until then, and level at 3,000 after it
    assert max(abs(r["REFXTK"]) for t, r in run.rows if t < seq[0]["t"]) < 0.001
    assert all(r["ALT"] == pytest.approx(3000.0) for t, r in run.rows if t > seq[0]["t"])


def test_a_start_at_or_above_the_climb_altitude_is_refused(tmp_path):
    with pytest.raises(generator.GeneratorError, match="not between the threshold elevation 261 and the climb"):
        generator.generate(_profile(tmp_path, BASE.replace("alt_ft: 311", "alt_ft: 3000")))


def test_a_start_on_the_direct_leg_after_the_climb_out_is_refused(tmp_path):
    text = BASE.replace("  - waypoint: GVO\n", "  - waypoint: GVO\n  - waypoint: GOLET\n")
    text = text.replace("start: {runway: RW30, alt_ft: 311}", "start: {fix: GVO, before_nm: 2, alt_ft: 3000}")
    with pytest.raises(generator.GeneratorError, match="start on the runway instead"):
        generator.generate(_profile(tmp_path, text))
