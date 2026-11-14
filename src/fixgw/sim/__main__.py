#  SPDX-License-Identifier: GPL-2.0-or-later
"""``python -m fixgw.sim generate|check|live`` -- see PROFILE_FORMAT.md."""

import argparse
import json
import signal
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

    v = sub.add_parser("live", help="fly a profile into a running gateway over netfix")
    v.add_argument("profile")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=3490)
    v.add_argument("--speed", type=float, default=1.0, help="multiple of real time (default 1)")
    v.add_argument("--cifp", help="FAACIFP18 file to use instead of the profile's cifp.file")
    v.add_argument("--frequency-ms", type=int, default=200)
    v.add_argument("--log", help="append one JSON line per timeline mark to this file")
    v.add_argument("--hook", help="shell command to run at each mark named by --hook-on")
    v.add_argument("--hook-on", default="start,sequence,map,end",
                   help="comma list of marks that run the hook (default start,sequence,map,end)")
    v.add_argument("--hook-delay-s", type=float, default=3.0, help="wall seconds after the mark (default 3)")
    v.add_argument("--readback", default="WPNAME,FPLCRS,FPLCDI,FPLMSG",
                   help="keys read back from the gateway at each mark")
    v.add_argument("--watch-s", type=float, default=3.0,
                   help="watch LAT/LONG this long first for another writer (default 3)")
    v.add_argument("--allow-other-writer", action="store_true",
                   help="fly even when another source is moving LAT/LONG")

    a = ap.parse_args(argv)
    if a.cmd == "live":
        return _live(a)
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


def _live(a):
    from fixgw.netfix import Client
    from fixgw.sim import live
    from fixgw.sim.generator import generate

    prof = load_profile(a.profile, cifp_override=a.cifp)
    client = Client(a.host, a.port)
    if not client.connect():
        client.disconnect()
        print(f"no gateway answering netfix at {a.host}:{a.port}", file=sys.stderr)
        return 2
    try:
        moving = live.other_writer(client, a.watch_s)
        if moving and not a.allow_other_writer:
            print(f"another source is writing {' '.join(moving)} on {a.host}:{a.port}; "
                  "stop it or pass --allow-other-writer", file=sys.stderr)
            return 3
        fplseq, cmd_base = live.start_state(client)
        run = generate(prof, frequency_ms=a.frequency_ms, cifp_path=a.cifp, fplseq=fplseq, cmd_seq_base=cmd_base)
        absent = live.missing_keys(client, run)
        if absent:
            print(f"the gateway has no {' '.join(absent)}: those writes are dropped", file=sys.stderr, flush=True)
        end_t = run.rows[-1][0]
        run.timeline = [{"t": 0.0, "what": "start"}] + run.timeline + [{"t": end_t, "what": "end"}]
        print(f"{prof.name}: {len(run.rows)} rows, {end_t:.1f} s at x{a.speed:g} = {end_t / a.speed:.1f} s wall, "
              f"to {a.host}:{a.port}, FPLSEQ {fplseq}, commands from {cmd_base + 1}"
              + (f"; other writer on {' '.join(moving)}" if moving else ""), flush=True)
        marks = live.Marks(client, prof.name, log=a.log, hook=a.hook,
                           hook_on=[w for w in a.hook_on.split(",") if w], delay_s=a.hook_delay_s,
                           readback=[k for k in a.readback.split(",") if k])
        stopping = []
        signal.signal(signal.SIGINT, lambda *_: stopping.append(1))
        signal.signal(signal.SIGTERM, lambda *_: stopping.append(1))
        try:
            stats = live.stream(run, client.write, speed=a.speed, on_mark=marks, stop=lambda: bool(stopping))
        finally:
            marks.close()
        print(f"{prof.name}: {'STOPPED' if stats.stopped else 'done'}: {stats.rows} rows, {stats.writes} writes, "
              f"{stats.run_s:.1f} s flown in {stats.wall_s:.1f} s wall, worst lateness {stats.late_max_s:.3f} s, "
              f"hooks {marks.hooked} ({marks.failed_hooks} failed)", flush=True)
        if stats.stopped:
            return 130
        return 1 if marks.failed_hooks else 0
    finally:
        client.disconnect()


if __name__ == "__main__":
    sys.exit(main())
