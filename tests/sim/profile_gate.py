"""The simulated-flight gate (SYN3): every committed profile flown headless
through the navigation engine, and its ``checks:`` evaluated.

``test_profiles.py`` runs it under pytest; CI also runs it on its own, as
the "Simulated flights" job, so a failing check fails the PR as a separate
check with one annotation per failing line::

    python -m tests.sim.profile_gate [PROFILE ...] [--github] [--summary FILE]
                                     [--findings FILE]

With no profiles it flies every ``*.yaml`` in ``src/fixgw/sim/profiles/``.
Each failing check is one line naming the profile, the keys and tolerance,
the time of the first failure and the values there, and the largest error
and when it happened. It lives in tests/ for the same reason
``engine_replay`` does: it is the part of the tier that runs the engine.

A profile listed in QA's ``open_findings.yaml`` is a strict expected
failure (XFAIL): its failures are reported as warnings and do not fail the
job, and if it passes (XPASS) the job fails until the finding is closed and
the entry removed. The gate only reads that list; QA owns it.
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
OPEN_FINDINGS = Path(__file__).resolve().with_name("open_findings.yaml")


def committed_profiles():
    return sorted(PROFILES_DIR.glob("*.yaml"))


def open_findings(path=OPEN_FINDINGS):
    """QA's open findings: profile stem -> the finding, one line."""
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) and v.strip()
                                            for k, v in raw.items()):
        raise ValueError(f"{path}: expected a mapping of profile name -> finding")
    return raw


def unknown_findings(findings, paths):
    """Listed names that are not among the profiles flown: a renamed or
    deleted profile must not leave its entry behind."""
    stems = {Path(p).stem for p in paths}
    return sorted(k for k in findings if k not in stems)


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
    finding: str = None  # QA's open finding, if the profile is listed

    @property
    def passed(self):
        return all(r.passed for r in self.results)

    @property
    def status(self):
        if self.finding is None:
            return "PASS" if self.passed else "FAIL"
        return "XPASS" if self.passed else "XFAIL"

    @property
    def ok(self):
        # What the gate holds against the job: a failure nobody expected, or
        # an expected failure that has stopped failing.
        return self.status in ("PASS", "XFAIL")


def _check_lines(path):
    # The line each item of checks: starts on, so an annotation points at it.
    node = yaml.compose(Path(path).read_text())
    for k, v in node.value:
        if k.value == "checks":
            return [item.start_mark.line + 1 for item in v.value]
    return []


def fly(path, findings=None):
    t0 = time.monotonic()
    prof = load_profile(str(path))
    rows = replay(generator.generate(prof).rows)
    log = checks.Log.from_rows(rows, checks.keys_needed(prof.checks))
    results = checks.evaluate(prof.checks, log)
    return ProfileRun(prof.name, Path(path), prof.checks, results, _check_lines(path),
                      len(rows), rows[-1][0], time.monotonic() - t0,
                      (findings or {}).get(Path(path).stem))


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


def xpass_line(run):
    return (f"{run.name} passes every check but is listed as an open finding "
            f"({run.finding}): close the finding and remove it from {_rel(OPEN_FINDINGS)}")


def _rel(path):
    return path.relative_to(REPO) if path.is_relative_to(REPO) else path


def _escape(msg):
    return msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github_errors(run):
    # GitHub workflow commands, one per failing check, pointing at its line
    # in the profile. %, CR and LF are escaped in the message. An expected
    # failure is a warning; an expected failure that passed is the error.
    where = f"file={_rel(run.path)}"
    if run.status == "XPASS":
        return [f"::error {where},title=Simulated flight {run.name} (open finding)::{_escape(xpass_line(run))}"]
    level = "warning" if run.status == "XFAIL" else "error"
    title = f"Simulated flight {run.name}" + (" (open finding)" if run.status == "XFAIL" else "")
    out = []
    for i, r in enumerate(run.results):
        line = failure_line(run.name, r)
        if line is None:
            continue
        at = where + (f",line={run.check_lines[i]}" if i < len(run.check_lines) else "")
        out.append(f"::{level} {at},title={title}::{_escape(line)}")
    return out


def summary_markdown(runs, unknown=()):
    ok = sum(r.passed for r in runs)
    out = [f"## Simulated flights: {ok} of {len(runs)} profiles pass", ""]
    xfail = [r for r in runs if r.status == "XFAIL"]
    if xfail:
        out += [f"{len(xfail)} fail as expected, on QA's open findings (`{_rel(OPEN_FINDINGS)}`).", ""]
    for name in unknown:
        out += [f"- **{name}** is listed as an open finding but is not a profile flown here.", ""]
    for run in runs:
        out += [f"### {run.status} {run.name}", "",
                f"`{_rel(run.path)}`: "
                f"{run.rows} rows, {run.duration_s:.0f} s of flight, flown in {run.wall_s:.1f} s", ""]
        if run.finding is not None:
            out += [f"Open finding: {run.finding}", ""]
        if run.status == "XPASS":
            out += [f"- {xpass_line(run)}", ""]
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
    ap.add_argument("--findings", default=OPEN_FINDINGS, help="QA's open findings (default: %(default)s)")
    a = ap.parse_args(argv)
    paths = [Path(p).resolve() for p in a.profiles] or committed_profiles()
    if not paths:
        # A gate with nothing to fly would pass forever.
        print(f"no profiles in {PROFILES_DIR}", file=sys.stderr)
        return 2
    findings = open_findings(a.findings)
    # Only the full run can tell a stale entry from a profile not asked for.
    unknown = [] if a.profiles else unknown_findings(findings, paths)
    for name in unknown:
        print(f"ERROR {name} is listed in {a.findings} but is not a committed profile")
        if a.github:
            print(f"::error file={_rel(Path(a.findings).resolve())},title=Open finding without a profile::{name}")
    runs = []
    for p in paths:
        run = fly(p, findings)
        runs.append(run)
        n_ok = sum(r.passed for r in run.results)
        print(f"{run.status} {run.name}: {n_ok} of {len(run.results)} checks, "
              f"{run.rows} rows, {run.duration_s:.0f} s flown in {run.wall_s:.1f} s")
        if run.finding is not None:
            print(f"  open finding: {run.finding}")
        if run.status == "XPASS":
            print(f"  {xpass_line(run)}")
        for line in failure_lines(run):
            print(f"  {line}")
        if a.github:
            for line in github_errors(run):
                print(line)
    if a.summary:
        with open(a.summary, "a") as f:
            f.write(summary_markdown(runs, unknown) + "\n")
    return 0 if all(r.ok for r in runs) and not unknown else 1


if __name__ == "__main__":
    sys.exit(main())
