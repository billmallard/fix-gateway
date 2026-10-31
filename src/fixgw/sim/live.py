#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Synthetic flight generator, live mode (SYN2).
#
"""Fly a profile into a running gateway over netfix.

:func:`start_state` reads what the gateway has already seen (``FPLSEQ``,
``FPLCMDACK``) so the run commits a new route and numbers its commands after
the last one acked; :func:`other_writer` watches ``LAT``/``LONG`` first and
names any key another source is moving, because two writers on one key is a
flight nobody flew. :func:`stream` then writes ``Run.rows`` at the profile's
own times, divided by ``speed``, and calls ``on_mark`` for each timeline
entry (sequence, MAP, pilot input, event) once its row is on the bus.

The client is the netfix client library (``fixgw.netfix.Client``): the bus,
not the engine, so the reference stays independent of what it judges.
"""

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass

POSITION_KEYS = ("LAT", "LONG")


class LiveError(Exception):
    pass


@dataclass
class Stats:
    rows: int
    writes: int
    run_s: float          # profile time flown
    wall_s: float         # wall time it took
    late_max_s: float     # worst lateness of a row against its schedule
    stopped: bool = False


def _read_value(client, key):
    r = client.read(key)
    if not isinstance(r, tuple):
        raise LiveError(f"gateway has no key {key} (netfix error {r})")
    return r[1]


def start_state(client):
    """(fplseq, cmd_seq_base) for a run on this gateway: one past its current
    ``FPLSEQ``, and commands numbered after the last ``FPLCMDACK``."""
    seq = int(float(_read_value(client, "FPLSEQ")))
    ack = abs(int(float(_read_value(client, "FPLCMDACK"))))
    return seq + 1, ack


def missing_keys(client, run):
    """Keys the run writes that the gateway's database does not have. The
    server drops a write to an unknown key without a word, so say it first."""
    have = set(client.getList())
    if not have:
        raise LiveError("gateway returned an empty key list")
    want = {k for _, row in run.rows for k in row}
    return sorted(want - have)


def other_writer(client, watch_s=3.0, keys=POSITION_KEYS, sleep=time.sleep):
    """The keys in ``keys`` that changed while we watched for ``watch_s``
    seconds without writing them -- someone else is publishing a position."""
    first = {k: _read_value(client, k) for k in keys}
    sleep(watch_s)
    return [k for k in keys if _read_value(client, k) != first[k]]


def stream(run, write, speed=1.0, on_mark=None, clock=time.monotonic, sleep=time.sleep, stop=None):
    """Write every row of ``run`` with ``write(key, value)``, row ``t`` at
    ``t / speed`` wall seconds after the first. The schedule is absolute, so
    a slow write is caught up, not accumulated. ``on_mark(entry, wall_s)``
    is called for each timeline entry after the row of its tick is written.
    ``stop()`` returning true ends the run early."""
    if speed <= 0:
        raise LiveError(f"speed must be positive, not {speed}")
    marks = sorted(run.timeline, key=lambda e: e["t"])
    m = 0
    writes = 0
    late_max = 0.0
    t0 = clock()
    done_t = 0.0
    n = 0
    for t, row in run.rows:
        if stop is not None and stop():
            return Stats(n, writes, done_t, clock() - t0, late_max, stopped=True)
        due = t0 + t / speed
        now = clock()
        if due > now:
            sleep(due - now)
        else:
            late_max = max(late_max, now - due)
        for key, value in row.items():
            write(key, value)
            writes += 1
        n += 1
        done_t = t
        while m < len(marks) and marks[m]["t"] <= t:
            if on_mark is not None:
                on_mark(marks[m], clock() - t0)
            m += 1
    return Stats(n, writes, done_t, clock() - t0, late_max)


class Marks:
    """What happens at each timeline mark, off the streaming thread and in
    order: wait ``delay_s`` (the glass needs a moment to draw the change),
    read ``readback`` keys from the gateway, run ``hook`` when the mark's
    ``what`` is in ``hook_on``, and log one JSON line per mark.

    The hook is a shell command; it gets ``SYN_INDEX`` (counts hooked marks
    from 1), ``SYN_WHAT``, ``SYN_FIX`` (the TO fix of a sequence, the MAP, or
    the event id / command), ``SYN_T`` (profile seconds), ``SYN_WALL_S`` and
    ``SYN_PROFILE`` in its environment."""

    def __init__(self, client, profile_name, log=None, hook=None, hook_on=(), delay_s=3.0,
                 readback=(), clock=time.monotonic, sleep=time.sleep):
        self.client = client
        self.profile_name = profile_name
        self.log = log
        self.hook = hook
        self.hook_on = set(hook_on)
        self.delay_s = delay_s
        self.readback = list(readback)
        self.clock = clock
        self.sleep = sleep
        self.hooked = 0
        self.failed_hooks = 0
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="syn-live-marks", daemon=True)
        self._thread.start()

    def __call__(self, entry, wall_s):
        self._q.put((entry, wall_s, self.clock()))

    def close(self):
        """Finish every queued mark, then stop."""
        self._q.put(None)
        self._thread.join()

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            entry, wall_s, queued = item
            try:
                self._mark(entry, wall_s, queued)
            except Exception as e:      # a broken hook must not stop the flight
                self._write_log({"t": entry.get("t"), "what": entry.get("what"), "error": repr(e)})

    def _mark(self, entry, wall_s, queued):
        wait = queued + self.delay_s - self.clock()
        if wait > 0:
            self.sleep(wait)
        line = dict(entry)
        line["wall_s"] = round(wall_s, 3)
        rb = {}
        for key in self.readback:
            try:
                rb[key] = _read_value(self.client, key)
            except Exception as e:
                rb[key] = f"<{e}>"
        if rb:
            line["readback"] = rb
        if self.hook and entry.get("what") in self.hook_on:
            self.hooked += 1
            env = dict(os.environ, SYN_INDEX=f"{self.hooked:02d}", SYN_WHAT=str(entry.get("what", "")),
                       SYN_FIX=str(entry.get("to") or entry.get("fix") or entry.get("id") or entry.get("cmd") or ""),
                       SYN_T=f"{entry.get('t', 0):.1f}", SYN_WALL_S=f"{wall_s:.1f}",
                       SYN_PROFILE=self.profile_name)
            r = subprocess.run(self.hook, shell=True, env=env, capture_output=True, text=True)
            line["hook"] = {"index": self.hooked, "rc": r.returncode,
                            "out": (r.stdout + r.stderr).strip()[-400:]}
            if r.returncode != 0:
                self.failed_hooks += 1
        self._write_log(line)

    def _write_log(self, line):
        text = json.dumps(line, separators=(",", ":"))
        print(text, flush=True)
        if self.log:
            with open(self.log, "a") as f:
                f.write(text + "\n")
