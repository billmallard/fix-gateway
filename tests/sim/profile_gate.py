"""The simulated-flight gate (SYN3): every committed profile flown headless
through the navigation engine, and its ``checks:`` evaluated. Every recorded
X-Plane flight in ``tests/sim/fixtures/*.yaml`` too (SYN4): the engine flown
on X-Plane's trajectory, with X-Plane's navigator (``EXTCRS``/``EXTCDI``) as
the expected values.

``test_profiles.py`` runs it under pytest; CI also runs it on its own, as
the "Simulated flights" job, so a failing check fails the PR as a separate
check with one annotation per failing line::

    python -m tests.sim.profile_gate [FLIGHT ...] [--github] [--summary FILE]

With no arguments it flies every ``*.yaml`` in ``src/fixgw/sim/profiles/``
and every recording in ``tests/sim/fixtures/``.
Each failing check is one line naming the profile, the keys and tolerance,
the time of the first failure and the values there, and the largest error
and when it happened. It lives in tests/ for the same reason
``engine_replay`` does: it is the part of the tier that runs the engine.

A profile that takes a GPS input away (a ``gps_*`` event) is flown through
the plugin itself (``plugin_replay``), not the engine: what the box does
with an input that stops is the plugin's job, and ``engine_replay`` only
models it. Every other profile is flown through the engine.

A profile listed as an open finding (the strict-xfail ``OPEN`` dict of the
test module that owns it) is expected to fail: its failing checks are
reported as warnings and do not fail the gate. If it passes, the gate fails,
so the finding is closed rather than left listed. The gate only reads those
lists; their owner (QA, for the qa1 profiles) edits them.
"""

import argparse
import ast
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from fixgw.sim import checks, generator
from fixgw.sim.profile import load_profile

from . import engine_replay, plugin_replay, xplane_replay

REPO = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO / "src/fixgw/sim/profiles"
RECORDINGS_DIR = REPO / "tests/sim/fixtures"
# Test modules whose module-level OPEN = {profile stem: reason} lists open
# findings. Read with ast, not imported, so the gate needs no pytest.
OPEN_FINDINGS_FROM = [REPO / "tests/sim/test_qa1_profiles.py"]


def committed_profiles():
    return sorted(PROFILES_DIR.glob("*.yaml"))


def committed_recordings():
    return sorted(RECORDINGS_DIR.glob("*.yaml"))


def open_findings(sources=None):
    """{profile stem: reason} from each source's module-level OPEN dict."""
    out = {}
    for src in OPEN_FINDINGS_FROM if sources is None else sources:
        for node in ast.parse(Path(src).read_text()).body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "OPEN" for t in node.targets):
                out.update(ast.literal_eval(node.value))
    return out


@dataclass
class ProfileRun:
    name: str
    path: Path
    checks: list
    results: list
    check_lines: list    # line of each check in the profile file
    rows: int
    duration_s: float
    wall_s: float
    via: str = "engine_replay"   # which replay flew it

    @property
    def passed(self):
        return all(r.passed for r in self.results)


def _check_lines(path):
    # The line each item of checks: starts on, so an annotation points at it.
    node = yaml.compose(Path(path).read_text())
    for k, v in node.value:
        if k.value == "checks":
            return [item.start_mark.line + 1 for item in v.value]
    return []


def is_recording(path):
    """A recorded X-Plane flight names its trace; a profile never does."""
    return "trace" in (yaml.safe_load(Path(path).read_text()) or {})


def replay_for(prof):
    """The replay a profile is flown through: the plugin when it takes a GPS
    input away, the engine otherwise."""
    gps = any(generator.GPS_EVENTS.keys() & e.do.keys() for e in prof.events)
    return plugin_replay if gps else engine_replay


def fly(path):
    if is_recording(path):
        return fly_recording(path)
    t0 = time.monotonic()
    prof = load_profile(str(path))
    via = replay_for(prof)
    rows = via.replay(generator.generate(prof).rows)
    log = checks.Log.from_rows(rows, checks.keys_needed(prof.checks))
    results = checks.evaluate(prof.checks, log)
    return ProfileRun(prof.name, Path(path), prof.checks, results, _check_lines(path),
                      len(rows), rows[-1][0], time.monotonic() - t0, via.__name__.rsplit(".", 1)[-1])


def fly_recording(path, trace=None):
    """A recorded X-Plane flight (SYN4): the engine flown on X-Plane's
    trajectory, its checks evaluated with X-Plane's navigator as the expected
    values. ``trace`` (rows) replaces the recording's own trace file."""
    t0 = time.monotonic()
    path = Path(path)
    rec = yaml.safe_load(path.read_text())
    rows = trace if trace is not None else xplane_replay.load_trace(path.parent / rec["trace"])
    samples, _ = xplane_replay.fly(rows, PROFILES_DIR / f"{rec['profile']}.yaml")
    bus = xplane_replay.bus_rows(samples)
    log = checks.Log.from_rows(bus, checks.keys_needed(rec["checks"]))
    # legs are labelled by X-Plane's active waypoint: there is no reference navigator
    results = checks.evaluate(rec["checks"], log, start_s=bus[0][0] + 1.0, leg_keys=("gps_id", None))
    return ProfileRun(rec["name"], path, rec["checks"], results, _check_lines(path),
                      len(bus), bus[-1][0] - bus[0][0], time.monotonic() - t0, "xplane_replay")


def failure_line(name, r):
    """What failed, by how much, and when; None if the check passed."""
    if r.passed:
        return None
    if r.label.startswith("sequence"):
        return f"{name}: {r.label} FAILED: {r.detail}"
    if r.compared == 0:
        return f"{name}: {r.label} FAILED: nothing compared"
    line = f"{name}: {r.label} FAILED on {r.failed} of {r.compared} rows; first at {r.detail}"
    if r.max_err is not None:
        line += f"; max error {r.max_err:.4f} at t={r.t_max:.1f}s"
    return line


def failure_lines(run):
    return [x for x in (failure_line(run.name, r) for r in run.results) if x]


def _rel(path):
    return path.relative_to(REPO) if path.is_relative_to(REPO) else path


def github_errors(run, level="error"):
    # GitHub workflow commands, one per failing check, pointing at its line
    # in the profile. %, CR and LF are escaped in the message.
    out = []
    for i, r in enumerate(run.results):
        line = failure_line(run.name, r)
        if line is None:
            continue
        msg = line.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        where = f"file={_rel(run.path)}"
        if i < len(run.check_lines):
            where += f",line={run.check_lines[i]}"
        out.append(f"::{level} {where},title=Simulated flight {run.name}::{msg}")
    return out


def verdict(run, open_):
    """PASS, FAIL, XFAIL (an open finding that still fails) or XPASS (an
    open finding that passes now: close it). Only PASS and XFAIL pass the gate."""
    if run.path.stem in open_:
        return "XPASS" if run.passed else "XFAIL"
    return "PASS" if run.passed else "FAIL"


def summary_markdown(runs, open_=None):
    open_ = open_ or {}
    ok = sum(verdict(r, open_) in ("PASS", "XFAIL") for r in runs)
    out = [f"## Simulated flights: {ok} of {len(runs)} flights pass the gate", ""]
    for run in runs:
        v = verdict(run, open_)
        out += [f"### {v} {run.name}", ""]
        if v in ("XFAIL", "XPASS"):
            out += [f"Open finding: {open_[run.path.stem]}", ""]
        out += [
                f"`{_rel(run.path)}`: "
                f"{run.rows} rows, {run.duration_s:.0f} s of flight, flown in {run.wall_s:.1f} s", ""]
        fails = failure_lines(run)
        if fails:
            out += [f"- {f}" for f in fails] + [""]
        source = (f"the headless {run.via.replace('_', ' ')}" if run.via != "xplane_replay" else
                  "the engine replayed on X-Plane's flight, X-Plane's navigator as expected values")
        out += [checks.markdown(run.name, run.results, source), ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tests.sim.profile_gate")
    ap.add_argument("profiles", nargs="*",
                    help="profile or recording files (default: every committed profile and recording)")
    ap.add_argument("--github", action="store_true", help="also print GitHub error annotations")
    ap.add_argument("--summary", help="append a markdown report to this file (e.g. $GITHUB_STEP_SUMMARY)")
    a = ap.parse_args(argv)
    paths = [Path(p).resolve() for p in a.profiles] or committed_profiles() + committed_recordings()
    if not paths:
        # A gate with nothing to fly would pass forever.
        print(f"no profiles in {PROFILES_DIR}", file=sys.stderr)
        return 2
    open_ = open_findings()
    ok = True
    if not a.profiles:
        # A listed finding whose profile is gone would never be closed.
        for stem in sorted(set(open_) - {p.stem for p in paths}):
            print(f"FAIL open finding {stem} names no profile in {PROFILES_DIR}")
            ok = False
    runs = []
    for p in paths:
        run = fly(p)
        runs.append(run)
        v = verdict(run, open_)
        ok = ok and v in ("PASS", "XFAIL")
        n_ok = sum(r.passed for r in run.results)
        print(f"{v} {run.name}: {n_ok} of {len(run.results)} checks, "
              f"{run.rows} rows, {run.duration_s:.0f} s flown in {run.wall_s:.1f} s")
        if v == "XFAIL":
            print(f"  open finding, expected to fail: {open_[p.stem]}")
        elif v == "XPASS":
            print(f"  listed as an open finding but passes now -- close it: {open_[p.stem]}")
        for line in failure_lines(run):
            print(f"  {line}")
        if a.github:
            for line in github_errors(run, "warning" if v == "XFAIL" else "error"):
                print(line)
            if v == "XPASS":
                print(f"::error file={_rel(run.path)},title=Simulated flight {run.name}::"
                      f"{run.name} passes but is listed as an open finding; close it")
    if a.summary:
        with open(a.summary, "a") as f:
            f.write(summary_markdown(runs, open_) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
