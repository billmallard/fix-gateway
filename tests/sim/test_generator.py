"""The scenario profile, the generator's stream and its data_recorder file
(SYN1)."""

import ast
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fixgw.plugins import data_playback
from fixgw.sim import checks, generator
from fixgw.sim.profile import ProfileError, load_profile

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "src" / "fixgw" / "sim"
PROFILE = SIM / "profiles" / "fp8-ksmx-rnav30-missed.yaml"


@pytest.fixture(scope="module")
def run():
    return generator.generate(str(PROFILE))


def test_the_synthetic_tier_imports_nothing_from_the_engine():
    """The rule the tier rests on: the reference side (everything in
    fixgw.sim) never imports the engine under test or the geometry module
    it uses. Shared code makes both sides wrong the same way."""
    offenders = []
    for py in sorted(SIM.rglob("*.py")):
        tree = ast.parse(py.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                if n == "fixgw.netfix" and py.name == "__main__.py":
                    continue    # the bus client the live mode writes with (SYN2), not the engine
                if n.startswith("fixgw") and not n.startswith("fixgw.sim"):
                    offenders.append(f"{py.name}: {n}")
    assert offenders == []


def _write(tmp_path, text):
    p = tmp_path / "p.yaml"
    p.write_text(text)
    return str(p)


BASE = """
name: t
cifp: {cycle: "2611", file: x}
environment: {magvar_deg: -11.9}
start: {fix: PAMJI, alt_ft: 2500}
route:
  - procedure: {airport: KSMX, approach: R30, transition: GVO}
"""


def test_profile_rejects_unknown_keys_and_an_unpinned_cycle(tmp_path):
    assert load_profile(_write(tmp_path, BASE)).cifp_cycle == "2611"
    with pytest.raises(ProfileError, match="unknown key"):
        load_profile(_write(tmp_path, BASE + "xplane: {aircraft: c172}\n"))
    with pytest.raises(ProfileError, match="cycle"):
        load_profile(_write(tmp_path, BASE.replace('"2611"', "current")))
    with pytest.raises(ProfileError, match="unknown key"):
        load_profile(_write(tmp_path, BASE.replace("alt_ft: 2500", "alt_ft: 2500, speed: 3")))


def test_generator_refuses_a_cifp_file_from_another_cycle(tmp_path):
    src = (SIM / "profiles" / "cifp" / "FAACIFP18-2611-KSMX-R30").read_text()
    other = tmp_path / "FAACIFP18"
    other.write_text(src.replace("2611  07-OCT", "2610  07-OCT", 1))
    with pytest.raises(generator.GeneratorError, match="2611.*2610"):
        generator.generate(str(PROFILE), cifp_path=str(other))


def test_first_rows_load_the_route_then_activate_the_leg_into_the_start_fix(run):
    keys = list(run.rows[0][1])
    route_keys = [k for k in keys if k.startswith("FPL")]
    assert route_keys[-1] == "FPLSEQ"                     # the commit is written last
    assert keys.index("LAT") < keys.index("FPLSEQ")
    r0 = run.rows[0][1]
    assert r0["FPLCOUNT"] == 11 and r0["FPLSEGCNT"] == 2 and r0["FPLDBCYC"] == "2611"
    assert [r0[f"FPL{i}ID"] for i in range(1, 12)] == [
        "GVO", "NIRLE", "ELZUS", "PAMJI", "CIMSO", "ANIBY", "RW30", "", "HANNU", "WINCH", "WINCH"]
    assert [r0[f"FPL{i}PT"] for i in (1, 8, 9, 11)] == ["IF", "CA", "DF", "HM"]
    assert r0["FPL8ALT"] == "+661" and r0["FPL11DST"] == 4.0 and r0["FPL11TURN"] == "L"
    assert r0["FPL7FLAGS"] == 0x20 | 0x08 | 0x01                 # RW30: MAP, fly-over
    assert r0["FPLS2KIND"] == 5 and (r0["FPLS2FIRST"], r0["FPLS2LAST"]) == (8, 11)
    assert run.rows[1][1]["FPLCMD"] == "1 ACT 4"


def test_the_scenario_flies_as_written(run):
    tl = run.timeline
    seq = [(e["from"], e["to"]) for e in tl if e["what"] == "sequence"]
    assert seq == [("PAMJI", "CIMSO"), ("CIMSO", "ANIBY"), ("ANIBY", "RW30"), ("", "HANNU"),
                   ("HANNU", "WINCH"), ("WINCH", "WINCH")]
    t = {(e["what"], e.get("id") or e.get("cmd") or e.get("fix")): e["t"] for e in tl}
    assert t[("pilot", "2 RESUME")] - t[("map", "RW30")] == pytest.approx(5.0, abs=0.25)
    rows = dict(run.rows)

    def xtk_between(a, b):
        return [r["REFXTK"] for tt, r in run.rows if a <= tt <= b]

    right = xtk_between(t[("established", "right")], t[("done", "right")])
    left = xtk_between(t[("established", "left")], t[("done", "left")])
    assert min(right) > 0.95 and max(left) < -0.95
    assert t[("done", "right")] - t[("established", "right")] == pytest.approx(30.0, abs=0.25)
    assert t[("done", "hold")] - t[("event", "hold")] == pytest.approx(300.0, abs=0.25)
    assert rows[t[("done", "hold")]]["REFPT"] == "HM"


def test_recorder_file_is_the_data_recorder_format(run, tmp_path):
    out = tmp_path / "run.json"
    generator.write_recorder_file(run, out)
    lines = out.read_text().splitlines()
    head = json.loads(lines[0])
    assert head["frequency"] == "200" and "starttime" in head
    assert len(lines) == len(run.rows) + 1
    row = json.loads(lines[10])
    assert row["REFWPT"] == ["PAMJI", 0, 0, 0, 0, 0]
    log = checks.Log.from_recorder_files([out], {"REFWPT", "LAT"})
    assert log.t[1] == pytest.approx(0.2) and log.rows[-1]["REFWPT"] == "WINCH"


def test_data_playback_replays_the_file_unchanged(run, tmp_path, database):
    """The generated file goes through the real data_playback plugin into
    the real database: every key exists, and the route block and the
    reference keys land with the generator's values."""
    out = tmp_path / "run.json"
    short = generator.Run(run.profile, run.frequency_ms, run.rows[:3])
    generator.write_recorder_file(short, out)
    parent = MagicMock()
    parent.config = {"CONFIGPATH": str(tmp_path), "files": ["{CONFIG}/run.json"]}
    thread = data_playback.MainThread(parent)
    seen = []

    def stop_after_file(*a):
        seen.append(1)
        if len(seen) >= len(short.rows):
            thread.getout = True

    with patch.object(time, "sleep", side_effect=stop_after_file):
        thread.run()
    last = dict(short.rows[-1][1])
    for k in ("LAT", "LONG", "REFCRS", "REFWPT", "REFPT"):
        assert database.get_raw_item(k).value[0] == last[k], k
    assert database.get_raw_item("FPLCOUNT").value[0] == 11
    assert database.get_raw_item("FPL8PT").value[0] == "CA"
    assert database.get_raw_item("FPLCMD").value[0] == "1 ACT 4"
