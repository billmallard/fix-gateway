#!/usr/bin/env python3
"""Score each heading-bug change in a sim-bench run (AER-6268).

    python3 analyze.py <run dir> [<run dir> ...]

A run dir holds the sequencer's steps.jsonl, the private gateway's bus log
(bus.jsonl or bus.jsonl.gz: one row per key update) and, optionally, the
adapter's raw log (raw.jsonl or raw.jsonl.gz: the sim's datarefs on change).
For each bug change (a "bug set" step, or AER-6052's "bug course+40"):

- which way the flight director first asked to roll (the first FDROLL past
  3 deg, positive right), against the side the change is on;
- the most the heading moved against the change (0 = it never turned the
  wrong way), unwrapped so a turn the long way keeps counting;
- the time until the heading was within 5 deg of the bug, all scored up to
  the next bug write;
- from the raw log, when the sim's heading dial reached the bug (within
  half a degree), and how it moved in the first 10 s: its number of changes
  and the median time between them.
"""
import gzip
import json
import os
import sys


def rows(path):
    for name in (path, path + ".gz"):
        if os.path.exists(name):
            opener = gzip.open if name.endswith(".gz") else open
            with opener(name, "rt") as f:
                return [json.loads(line) for line in f if line.strip()]
    return []


def angdiff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def score(run):
    steps = rows(os.path.join(run, "steps.jsonl"))
    bus = rows(os.path.join(run, "bus.jsonl"))
    raw = rows(os.path.join(run, "raw.jsonl"))
    series = {}
    for r in bus:
        try:
            series.setdefault(r["k"], []).append((r["t"], float(r["v"])))
        except (KeyError, ValueError):
            pass
    dial = [(r["t"], r["refs"]["hdg_dial"]) for r in raw
            if "hdg_dial" in r.get("refs", {})]
    out = []
    changes = [s for s in steps if s["msg"] in ("bug set", "bug course+40")]
    writes = sorted(s["t"] for s in steps if "bug" in s)   # any bug write
    for s in changes:
        t0, bug = s["t"], s["bug"]
        # Score until the next bug write: AER-6052 set its intercept bug 60 s
        # after the +40, mid-turn.
        t_end = next((t for t in writes if t > t0), t0 + 600.0)
        head = [(t, v) for t, v in series.get("HEAD", []) if t0 <= t < t_end]
        if not head:
            continue
        h0 = [v for t, v in series["HEAD"] if t <= t0][-1]
        delta = angdiff(bug, h0)
        fd = next((v for t, v in series.get("FDROLL", [])
                   if t0 <= t < t_end and abs(v) > 3.0), None)
        against, prev, reached = 0.0, 0.0, None
        for t, v in head:
            moved = angdiff(v, h0)
            if abs(moved - prev) > 180.0:
                moved += 360.0 if moved < prev else -360.0
            prev = moved
            against = max(against, -moved if delta > 0 else moved)
            if reached is None and abs(angdiff(v, bug)) < 5.0:
                reached = t - t0
        at_bug = next((t - t0 for t, v in dial
                       if t >= t0 and abs(angdiff(bug, v)) < 0.5), None)
        moves = [t for (t, v), (_t, w) in zip(dial, dial[1:])
                 if t0 <= _t <= t0 + 10.0 and v != w]
        gaps = sorted(b - a for a, b in zip(moves, moves[1:]))
        out.append({
            "step": s["step"], "from": round(h0, 1), "bug": bug,
            "change": round(delta, 1),
            "first_fd_roll": None if fd is None else round(fd, 1),
            "fd_short_way": None if fd is None else (fd > 0) == (delta > 0),
            "most_against_deg": round(against, 1),
            "reached_s": None if reached is None else round(reached, 1),
            "dial_at_bug_s": None if at_bug is None else round(at_bug, 2),
            "dial_moves_10s": len(moves) if dial else None,
            "dial_gap_median_s": (round(gaps[len(gaps) // 2], 3)
                                  if gaps else None),
        })
    return out


if __name__ == "__main__":
    for run in sys.argv[1:]:
        print(run)
        for row in score(run):
            print("  " + json.dumps(row))
