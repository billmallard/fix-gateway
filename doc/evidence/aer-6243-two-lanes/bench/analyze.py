#!/usr/bin/env python3
"""Judge an AER-6243 bench run against the brief's pass criteria (section 8).

Pass: lane 2's PITCH, ROLL, IAS and ALT stay within their time-to-live and
valid for the whole run, and lane 2 leads within 1.5 s of any lane-1 gateway
death.  Reads RUN/events.csv and RUN/run.log; prints one row per mode and
writes RUN/summary.md.

    python3 analyze.py RUN_DIR
"""
import csv
import datetime
import os
import sys

run = sys.argv[1]
TOL_MS = {"PITCH": 300, "ROLL": 300, "IAS": 2000, "ALT": 2000}
TAKEOVER_S = 1.5
GATEWAY_DEATHS = {"gw_kill", "gw_stop", "host_drop"}

events = []
with open(os.path.join(run, "events.csv")) as f:
    for r in csv.DictReader(f):
        if r["wall"] == "wall":
            continue
        events.append((float(r["wall"]), int(r["lane"]), r["key"], r["value"], r["flags"]))
events.sort()

# run.log lines: "HH:MM:SS.mmm mode:phase" (UTC, same day as the events)
day = datetime.datetime.fromtimestamp(events[0][0], datetime.timezone.utc).date()
marks = []
for line in open(os.path.join(run, "run.log")):
    t, what = line.split(" ", 1)
    if what.startswith("grab"):
        continue
    hh, mm, ss = t.split(":")
    stamp = datetime.datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=datetime.timezone.utc)
    marks.append((stamp.timestamp() + float(ss), what.strip()))


def window(lo, hi, lane=None, key=None):
    return [e for e in events if lo <= e[0] < hi and (lane is None or e[1] == lane) and (key is None or e[2] == key)]


def leader_at(lane, t):
    # The quorum plugin writes LEADER every 0.3 s; older than 1 s, the lane is not answering.
    prev = [e for e in events if e[0] <= t and e[1] == lane and e[2] == "LEADER"]
    if not prev:
        return "?"
    return prev[-1][3] if t - prev[-1][0] < 1.0 else f"silent ({prev[-1][3]} {t - prev[-1][0]:.0f} s ago)"


rows, ok_all = [], True
starts = [(t, w.split(":")[0]) for t, w in marks if w.endswith(":before")]
ends = [t for t, w in marks if w.endswith(":before")][1:] + [t for t, w in marks if w == "done"]
for (t0, mode), t1 in zip(starts, ends):
    fault = next(t for t, w in marks if w == f"{mode}:fault")
    recover = next(t for t, w in marks if w == f"{mode}:recover")
    verdict, notes = True, []
    for key, tol in TOL_MS.items():
        ws = window(t0, t1, 2, key)
        writes = [e for e in ws if e[4] == "00000"]
        bad = [e for e in ws if e[4] != "00000"]
        times = [t0] + [e[0] for e in writes] + [t1]
        gap = max(b - a for a, b in zip(times, times[1:])) * 1000
        if bad or gap > tol:
            verdict = False
        notes.append(f"{key} max gap {gap:.0f} ms (tol {tol}), {len(writes)} writes, {len(bad)} flagged")
    took = None
    for e in window(fault, recover, 2, "LEADER"):
        if e[3] == "True":
            took = e[0] - fault
            break
    l1_during = leader_at(1, recover - 0.5)
    l2_during = leader_at(2, recover - 0.5)
    if mode in GATEWAY_DEATHS and (took is None or took > TAKEOVER_S):
        verdict = False
    ok_all &= verdict
    rows.append((mode, verdict, took, l1_during, l2_during, notes))

out = ["| Mode | Pass | Lane 2 leads after | LEADER lane 1 / lane 2, just before recovery | Lane 2's keys |",
       "|---|---|---|---|---|"]
for mode, verdict, took, l1, l2, notes in rows:
    took_s = "never" if took is None else f"{took:.2f} s"
    out.append(f"| {mode} | {'yes' if verdict else 'NO'} | {took_s} | {l1} / {l2} | {'; '.join(notes)} |")
out.append("")
out.append(f"Overall: {'PASS' if ok_all else 'FAIL'}  (events: {len(events)}, "
           f"{datetime.datetime.fromtimestamp(events[0][0], datetime.timezone.utc):%H:%M:%S} to "
           f"{datetime.datetime.fromtimestamp(events[-1][0], datetime.timezone.utc):%H:%M:%S} UTC)")
text = "\n".join(out)
print(text)
open(os.path.join(run, "summary.md"), "w").write(text + "\n")
