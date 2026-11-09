"""En-route route items in a scenario profile (SIM3b): ``waypoint`` and
``airway {id, exit}`` before the procedure, ``departure``/``destination``,
``start.fix`` on an en-route fix and ``end.after_fix``. The shape is the one
xplane-bench reads (``fca936a``), so one profile flies in both tiers."""

import json
from pathlib import Path

import pytest
import yaml

from fixgw.sim import checks, fplroute, generator, refnav
from fixgw.sim.cifp import Cifp, CifpError
from fixgw.sim.profile import ProfileError, load_profile

from . import engine_replay, profile_gate

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ROOT / "src/fixgw/sim/profiles"
PROFILE = PROFILES / "sim2b-v27-s2-hener-doyle.yaml"
V27_CIFP = PROFILES / "cifp/FAACIFP18-2611-V27-GVO-AVOLS"
KSMX_CIFP = PROFILES / "cifp/FAACIFP18-2611-KSMX-R30"
FIXTURES = ROOT / "tests/sim/fixtures"
RECORDING = FIXTURES / "sim2b_v27_xplane_2026-11-01.yaml"

# What the X-Plane tier was sent for session 9b6d523db79a (inline, before
# this loader read en-route items): tests/sim/fixtures/README.md.
XPLANE_SUBMITTED = yaml.safe_load("""
name: sim2b-v27-s2-hener-doyle
departure: KSBA
destination: KSMX
cifp: {cycle: "2611"}
aircraft: {tas_kt: 110}
start: {fix: HENER, before_nm: 3.0, alt_ft: 7000, heading: on_course}
route:
  - waypoint: GVO
  - airway: {id: V27, exit: AVOLS}
end: {after_fix: DOYLE, max_s: 2100}
""")

V27_GVO_AVOLS = ["GVO", "GOLET", "KWANG", "DEANO", "HENER", "VTU", "EXERT", "DOYLE", "PAROL", "SXC", "AVOLS"]


def _subset(small, big, where=""):
    """Every key of ``small`` is in ``big`` with the same value."""
    if isinstance(small, dict):
        return [p for k, v in small.items()
                for p in ([f"{where}{k}: missing"] if k not in big else _subset(v, big[k], f"{where}{k}."))]
    return [] if small == big else [f"{where[:-1]}: {small!r} != {big!r}"]


def _profile(tmp_path, text, cifp=V27_CIFP):
    p = tmp_path / "p.yaml"
    p.write_text(text.replace("CIFP", str(cifp)))
    return str(p)


BASE = """
name: t
cifp: {cycle: "2611", file: CIFP}
environment: {magvar_deg: -11.45}
start: {fix: HENER, before_nm: 3.0, alt_ft: 7000}
"""


# -- the loader ---------------------------------------------------------------
def test_the_committed_profile_is_what_the_xplane_tier_flew():
    raw = yaml.safe_load(PROFILE.read_text())
    assert _subset(XPLANE_SUBMITTED, raw) == []
    p = load_profile(str(PROFILE))
    assert p.procedure is None
    assert p.departure == "KSBA" and p.destination == "KSMX"
    assert p.enroute == [{"kind": "waypoint", "fix": "GVO"}, {"kind": "airway", "id": "V27", "exit": "AVOLS"}]
    assert p.end == {"after_fix": "DOYLE", "max_s": 2100}


def test_the_xplane_recording_names_the_profile_it_was_flown_from():
    assert yaml.safe_load(RECORDING.read_text())["profile"] == PROFILE.stem


@pytest.mark.parametrize("route, extra, error", [
    ("- airway: {id: V27, exit: AVOLS}", "destination: KSMX", "needs an entry fix"),
    ("- waypoint: GVO\n  - airway: {id: V27, exit: HENER}\n  - airway: {id: V27, exit: AVOLS}",
     "destination: KSMX", "airway straight after an airway"),
    ("- procedure: {airport: KSMX, approach: R30, transition: GVO}\n  - waypoint: GVO", "",
     "procedure must be the last"),
    ("- waypoint: GVO", "", "destination: required"),
    ("- waypoint: GVO\n  - procedure: {airport: KSMX, approach: R30, transition: GVO}", "destination: KSBA",
     "not the procedure's airport"),
    ("- waypoint: GVO\n  - airway: {id: V27, exit: AVOLS, via: X}", "destination: KSMX", "unknown key"),
    ("- waypoint: GVO\n  - airway: {id: V27}", "destination: KSMX", "id: V27, exit: AVOLS"),
    ("- waypoint: GVO\n    airway: {id: V27, exit: AVOLS}", "destination: KSMX", "exactly one of"),
    ("- direct: GVO", "destination: KSMX", "exactly one of"),
    ("[]", "destination: KSMX", "a list of waypoint"),
])
def test_route_items_out_of_shape_are_refused(tmp_path, route, extra, error):
    text = BASE + f"{extra}\nroute:\n  {route}\n" if route != "[]" else BASE + f"{extra}\nroute: []\n"
    with pytest.raises(ProfileError, match=error):
        load_profile(_profile(tmp_path, text))


def test_after_fix_needs_en_route_items(tmp_path):
    text = BASE + "route:\n  - procedure: {airport: KSMX, approach: R30, transition: GVO}\nend: {after_fix: PAMJI}\n"
    with pytest.raises(ProfileError, match="after_fix"):
        load_profile(_profile(tmp_path, text))


# -- the CIFP airway ----------------------------------------------------------
def test_an_airway_flown_against_its_record_order_is_its_fixes_reversed():
    cifp = Cifp(V27_CIFP)
    assert [f for f, _, _ in cifp.airway("V27", "GVO", "AVOLS")] == V27_GVO_AVOLS
    assert [f for f, _, _ in cifp.airway("V27", "AVOLS", "GVO")] == V27_GVO_AVOLS[::-1]
    assert cifp.airway("V27", "GVO", "AVOLS")[0] == ("GVO", "D ", "K2")
    with pytest.raises(CifpError, match="no run through both"):
        cifp.airway("V27", "GVO", "MQO")
    with pytest.raises(CifpError, match="no records"):
        cifp.airway("V25", "GVO", "AVOLS")


def test_an_airway_ident_that_restarts_its_sequence_is_two_airways(tmp_path):
    # The same ident on two runs: the run holding both fixes is used.
    lines = V27_CIFP.read_text().splitlines()
    er = [ln for ln in lines if ln[4:6] == "ER"]
    p = tmp_path / "c"
    p.write_text("\n".join(lines + er[:3]) + "\n")
    cifp = Cifp(p)
    assert [f for f, _, _ in cifp.airway("V27", "GVO", "HENER")] == V27_GVO_AVOLS[:5]
    with pytest.raises(CifpError, match="2 runs"):
        cifp.airway("V27", "AVOLS", "PAROL")


# -- the route block: RS1 rows, and what pyEfis published for the same route --
def test_the_route_block_is_rs1s_rows():
    slots, segs, name, aprtype = fplroute.route_slots(Cifp(V27_CIFP), load_profile(str(PROFILE)))
    assert [s["ID"] for s in slots] == ["KSBA"] + V27_GVO_AVOLS + ["KSMX"]
    assert segs == [
        {"KIND": 0, "ID": "", "ENT": "KSBA", "EXT": "KSBA", "FIRST": 1, "LAST": 1},
        {"KIND": 1, "ID": "V27", "ENT": "GVO", "EXT": "AVOLS", "FIRST": 2, "LAST": 12},
        {"KIND": 0, "ID": "", "ENT": "KSMX", "EXT": "KSMX", "FIRST": 13, "LAST": 13}]
    assert [s["SGI"] for s in slots] == [1] + [2] * 11 + [3]
    assert (name, aprtype) == ("KSBA GVO V27 KSMX", "")


def test_the_route_block_matches_what_pyefis_published_for_the_xplane_flight():
    """The synthetic tier's route block against the one pyEfis's
    FixBridge.publish wrote on the bench (v27_route_block.json), on every
    field the engine flies a TF from. ALT and CRS are not compared: neither
    is part of a TF leg (``doc/flightplan_keys.md``), and this encoder writes
    no airway altitudes. Airport positions come from different sources (the
    CIFP airport record here, pyEfis's airport table there) and sit 0.001
    deg apart; en-route fixes are the same CIFP record on both sides."""
    block = json.loads((FIXTURES / "v27_route_block.json").read_text())
    slots, segs, _, _ = fplroute.route_slots(Cifp(V27_CIFP), load_profile(str(PROFILE)))
    assert len(slots) == block["FPLCOUNT"] and len(segs) == block["FPLSEGCNT"]
    diffs = []
    for i, s in enumerate(slots, start=1):
        for f in ("ID", "TYPE", "PT", "SGI", "FLAGS", "DST", "TURN", "CTRLAT", "CTRLON"):
            if s[f] != block[f"FPL{i}{f}"]:
                diffs.append(f"slot {i} {f}: {s[f]!r} != {block[f'FPL{i}{f}']!r}")
        tol = 2e-3 if s["TYPE"] == 1 else 1e-7
        for f in ("LAT", "LON"):
            if abs(s[f] - block[f"FPL{i}{f}"]) > tol:
                diffs.append(f"slot {i} {f}: {s[f]} != {block[f'FPL{i}{f}']}")
    for k, g in enumerate(segs, start=1):
        for f, v in g.items():
            if v != block[f"FPLS{k}{f}"]:
                diffs.append(f"segment {k} {f}: {v!r} != {block[f'FPLS{k}{f}']!r}")
    # The one known difference: pyEfis types every fix an airway expands to
    # as a fix (4), the VORs VTU and SXC included (airways.py, AirwayGraph.
    # expand); the entry, GVO, entered by name, is a VOR (2) on both sides.
    # The engine reads TYPE only to find an airport.
    assert diffs == ["slot 7 TYPE: 2 != 4", "slot 11 TYPE: 2 != 4"]


# -- the synthetic tier flies it ----------------------------------------------
@pytest.fixture(scope="module")
def v27():
    return generator.generate(str(PROFILE))


def test_it_starts_on_the_leg_into_hener_and_ends_once_doyle_is_sequenced(v27):
    assert v27.timeline[0] == {"t": 0.2, "what": "pilot", "cmd": "1 ACT 6"}
    seqs = [(e["from"], e["to"]) for e in v27.timeline if e["what"] == "sequence"]
    assert seqs == [("HENER", "VTU"), ("VTU", "EXERT"), ("EXERT", "DOYLE"), ("DOYLE", "PAROL")]
    assert v27.rows[-1][0] == next(e["t"] for e in v27.timeline if e.get("from") == "DOYLE")
    assert v27.rows[-1][1]["REFWPT"] == "PAROL"
    # en route: no altitude constraint is encoded, so the airplane holds 7,000 ft
    assert {r["ALT"] for _, r in v27.rows if "ALT" in r} == {7000.0}


def test_a_route_with_no_procedure_ends_when_the_destination_is_the_to_fix(tmp_path):
    text = BASE.replace("HENER", "AVOLS") + "destination: KSMX\nroute:\n  - waypoint: GVO\n" \
        "  - airway: {id: V27, exit: AVOLS}\n"
    run = generator.generate(_profile(tmp_path, text))
    assert [e["to"] for e in run.timeline if e["what"] == "sequence"] == ["KSMX"]
    assert run.rows[-1][1]["REFWPT"] == "KSMX"


def test_after_fix_off_the_en_route_legs_is_refused(tmp_path):
    text = BASE + "destination: KSMX\nroute:\n  - waypoint: GVO\n  - airway: {id: V27, exit: AVOLS}\n" \
        "end: {after_fix: KSMX}\n"
    with pytest.raises(generator.GeneratorError, match="not an en-route fix"):
        generator.generate(_profile(tmp_path, text))


def _evaluate(path):
    prof = load_profile(path)
    rows = engine_replay.replay(generator.generate(prof).rows)
    return checks.evaluate(prof.checks, checks.Log.from_rows(rows, checks.keys_needed(prof.checks))), rows


CDI_CHECK = """
checks:
  - {key_pair: [FPLCDI, REFCDI], tol: 0.05, same_sign: true, except_s_around_sequence: 5}
  - {key_pair: [FPLXTK, REFXTK], tol: 0.02, except_s_around_sequence: 5}
"""


def test_cdi_scale_is_terminal_within_30_nm_of_the_departure_airport(tmp_path):
    """The leg into HENER is under 30 nm from KSBA, HENER-VTU beyond it. A
    0.5 nm offset must read half scale (1.0 nm) on the first and a quarter
    (2.0 nm) on the second. The reference measured the terminal radius from
    the destination only until SIM3b, and this check failed by 0.25 on the
    first. An on-course flight cannot show a scale difference. Each offset
    is flown back to the track before the 30 nm line: there the engine
    steps the scale on the sphere and the reference on WGS-84, a few
    seconds apart."""
    text = BASE.replace("before_nm: 3.0", "before_nm: 8.0") + "departure: KSBA\ndestination: KSMX\n" \
        "route:\n  - waypoint: GVO\n  - airway: {id: V27, exit: AVOLS}\nend: {after_fix: VTU, max_s: 900}\n" \
        "events:\n" \
        "  - {id: term, at: {leg: DEANO-HENER, after_s: 1}, do: {offset_nm: 0.5, hold_s: 20}}\n" \
        "  - {id: back, at: {after: term}, do: {offset_nm: 0.0}}\n" \
        "  - {id: enr, at: {leg: HENER-VTU, after_s: 60}, do: {offset_nm: 0.5, hold_s: 20}}\n" + CDI_CHECK
    results, rows = _evaluate(_profile(tmp_path, text))
    assert [r.passed for r in results] == [True, True], [r.detail for r in results]
    ksba = (34.42619, -119.84149)
    off = [(refnav.inverse(r["LAT"], r["LONG"], *ksba)[0], r) for _, r in rows if abs(r["REFXTK"] - 0.5) < 0.02]
    term = [r for d, r in off if d < 29.5]
    enr = [r for d, r in off if d > 30.5]
    assert len(term) > 20 and len(enr) > 20
    assert all(abs(r["REFCDI"] + 0.5) < 0.02 for r in term)
    assert all(abs(r["REFCDI"] + 0.25) < 0.01 for r in enr)


# -- en-route items before a procedure ----------------------------------------
def _joined(tmp_path):
    """The V27 excerpt and the KSMX RNAV 30 excerpt in one file."""
    v27 = V27_CIFP.read_text().splitlines()
    ksmx = KSMX_CIFP.read_text().splitlines()[1:]
    p = tmp_path / "joined"
    p.write_text("\n".join(v27 + [ln for ln in ksmx if ln not in v27]) + "\n")
    return p


def test_en_route_items_then_the_approach_fly_as_one_route(tmp_path):
    text = BASE.replace("HENER", "GVO") + "departure: KSBA\nroute:\n  - waypoint: GOLET\n" \
        "  - procedure: {airport: KSMX, approach: R30, transition: GVO}\n" + CDI_CHECK + \
        "  - {sequence: [GVO, NIRLE, ELZUS, PAMJI, CIMSO, ANIBY, RW30], keys: [WPNAME, REFWPT]}\n"
    path = _profile(tmp_path, text, cifp=_joined(tmp_path))
    slots, segs, name, aprtype = fplroute.route_slots(Cifp(_joined(tmp_path)), load_profile(path))
    assert [s["ID"] for s in slots[:3]] == ["KSBA", "GOLET", "GVO"]
    assert [(g["KIND"], g["ENT"], g["FIRST"]) for g in segs] == [
        (0, "KSBA", 1), (0, "GOLET", 2), (4, "GVO", 3), (5, "", 10)]   # the missed approach starts with a CA
    assert (name, aprtype) == ("KSBA GOLET KSMX R30 GVO", "RNAV")
    results, _ = _evaluate(path)
    assert [r.passed for r in results] == [True, True, True], [r.detail for r in results]


def test_en_route_items_ending_where_the_procedure_starts_are_refused(tmp_path):
    # GVO twice in a row: a pilot would not file it, and the X-Plane driver
    # would turn back to GVO after sequencing it.
    text = BASE.replace("HENER", "GVO") + "route:\n  - waypoint: GOLET\n  - waypoint: GVO\n" \
        "  - procedure: {airport: KSMX, approach: R30, transition: GVO}\n"
    with pytest.raises(generator.GeneratorError, match="where the procedure starts"):
        generator.generate(_profile(tmp_path, text, cifp=_joined(tmp_path)))


def test_the_gate_flies_the_committed_en_route_profile():
    assert PROFILE in profile_gate.committed_profiles()
    run = profile_gate.fly(PROFILE)
    assert profile_gate.failure_lines(run) == []
    assert all(r.compared > 7000 for r in run.results if r.label.startswith(("FPL", "WP")))
