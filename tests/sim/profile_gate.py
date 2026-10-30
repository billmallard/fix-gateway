"""The simulated-flight gate (SYN3): every committed profile flown headless
through the navigation engine, and its ``checks:`` evaluated.

``test_profiles.py`` runs it under pytest; CI also runs it on its own, as
the "Simulated flights" job, so a failing check fails the PR as a separate
check with one annotation per failing line::

    python -m tests.sim.profile_gate [PROFILE ...] [--github] [--summary FILE]

With no profiles it flies every ``*.yaml`` in ``src/fixgw/sim/profiles/``.
Each failing check is one line naming the profile, the keys and tolerance,
the time of the first failure and the values there, and the largest error
and when it happened. It lives in tests/ for the same reason
``engine_replay`` does: it is the part of the tier that runs the engine.
"""

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from fixgw.sim import checks, generator
from fixgw.sim.profile import load_profile

from .engine_replay import replay

REPO = Path(__file__).resolve().parents[2]
PROFILES_DIR = REPO / "src/fixgw/sim/profiles"


def committed_profiles():
    return sorted(PROFILES_DIR.glob("*.yaml"))


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


def fly(path):
    t0 = time.monotonic()
    prof = load_profile(str(path))
    rows = replay(generator.generate(prof).rows)
    log = checks.Log.from_rows(rows, checks.keys_needed(prof.checks))
    results = checks.evaluate(prof.checks, log)
    return ProfileRun(prof.name, Path(path), prof.checks, results, _check_lines(path),
                      len(rows), rows[-1][0], time.monotonic() - t0)


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


def github_errors(run):
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
        out.append(f"::error {where},title=Simulated flight {run.name}::{msg}")
    return out


def summary_markdown(runs):
    ok = sum(r.passed for r in runs)
    out = [f"## Simulated flights: {ok} of {len(runs)} profiles pass", ""]
    for run in runs:
        out += [f"### {'PASS' if run.passed else 'FAIL'} {run.name}", "",
                f"`{_rel(run.path)}`: "
                f"{run.rows} rows, {run.duration_s:.0f} s of flight, flown in {run.wall_s:.1f} s", ""]
        fails = failure_lines(run)
        if fails:
            out += [f"- {f}" for f in fails] + [""]
        out += [checks.markdown(run.name, run.results, "the headless engine replay"), ""]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m tests.sim.profile_gate")
    ap.add_argument("profiles", nargs="*", help="profile files (default: every committed profile)")
    ap.add_argument("--github", action="store_true", help="also print GitHub error annotations")
    ap.add_argument("--summary", help="append a markdown report to this file (e.g. $GITHUB_STEP_SUMMARY)")
    a = ap.parse_args(argv)
    paths = [Path(p).resolve() for p in a.profiles] or committed_profiles()
    if not paths:
        # A gate with nothing to fly would pass forever.
        print(f"no profiles in {PROFILES_DIR}", file=sys.stderr)
        return 2
    runs = []
    for p in paths:
        run = fly(p)
        runs.append(run)
        n_ok = sum(r.passed for r in run.results)
        print(f"{'PASS' if run.passed else 'FAIL'} {run.name}: {n_ok} of {len(run.results)} checks, "
              f"{run.rows} rows, {run.duration_s:.0f} s flown in {run.wall_s:.1f} s")
        for line in failure_lines(run):
            print(f"  {line}")
        if a.github:
            for line in github_errors(run):
                print(line)
    if a.summary:
        with open(a.summary, "a") as f:
            f.write(summary_markdown(runs) + "\n")
    return 0 if all(r.passed for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
