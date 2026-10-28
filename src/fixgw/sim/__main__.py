#  SPDX-License-Identifier: GPL-2.0-or-later
"""``python -m fixgw.sim generate|check`` -- see PROFILE_FORMAT.md."""

import argparse
import json
import sys

from fixgw.sim import checks
from fixgw.sim.generator import generate, write_recorder_file
from fixgw.sim.profile import load_profile


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fixgw.sim")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="fly a profile and write a data_recorder file")
    g.add_argument("profile")
    g.add_argument("-o", "--out", required=True, help="output file (data_recorder JSON lines)")
    g.add_argument("--cifp", help="FAACIFP18 file to use instead of the profile's cifp.file")
    g.add_argument("--frequency-ms", type=int, default=200)
    g.add_argument("--timeline", help="also write the run's timeline (events, sequences) as JSON")

    c = sub.add_parser("check", help="evaluate a profile's checks on recorded bus logs")
    c.add_argument("profile")
    c.add_argument("logs", nargs="+", help="data_recorder files, in time order")

    a = ap.parse_args(argv)
    if a.cmd == "generate":
        prof = load_profile(a.profile, cifp_override=a.cifp)
        run = generate(prof, frequency_ms=a.frequency_ms, cifp_path=a.cifp)
        write_recorder_file(run, a.out)
        if a.timeline:
            with open(a.timeline, "w") as f:
                json.dump({"profile": prof.name, "cifp_cycle": prof.cifp_cycle, "frequency_ms": run.frequency_ms,
                           "duration_s": run.rows[-1][0], "timeline": run.timeline}, f, indent=1)
        print(f"{prof.name}: {len(run.rows)} rows, {run.rows[-1][0]:.1f} s -> {a.out}")
        return 0
    prof = load_profile(a.profile)
    log = checks.Log.from_recorder_files(a.logs, checks.keys_needed(prof.checks))
    results = checks.evaluate(prof.checks, log)
    print(checks.markdown(prof.name, results, ", ".join(a.logs)))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
