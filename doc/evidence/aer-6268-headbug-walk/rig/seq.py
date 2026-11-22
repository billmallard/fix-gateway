#!/usr/bin/env python3
"""AER-6268 bench sequencer: does a large heading-bug change from the bus turn
X-Plane the short way, now that the adapter walks the bug (XPB-018)? Built
from the AER-6052 sequencer, without the glass.

    python3 seq.py <dir> [--port 3594] [--fast]

Through a private fixgw on the branch: wait for the driver to couple GPS,
sync the bug to the heading, press HDG, then set the bug +40.2 (the AER-6052
change that was flown 320 deg the long way), -40, and +150. For each, it
records which way FDROLL and the heading went. Last, DISC, which trips the
driver's servo guard and ends the session.

Logs the bus (bus.jsonl) and each step (steps.jsonl). The adapter's raw log
(raw.jsonl, set in the private xplane.yaml) holds the sim's heading dial as
it moved, which shows the steps.
"""
import json
import os
import socket
import sys
import threading
import time

D = sys.argv[1]


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


PORT = int(arg("--port", 3594))
FAST = "--fast" in sys.argv
STOP = os.path.join(D, "STOP")

KEYS = ["APENG", "FDENG", "APLAT", "APLATARM", "APVERT", "FDPITCH", "FDROLL",
        "APCAPS", "APMSG", "APDISC", "APCMD", "APCMDSEQ", "HEADBUG", "ROLL",
        "HEAD", "TRACK", "EXTCRS", "EXTCDI", "ALT", "IAS", "LAT", "LONG"]
SNAP = ["APENG", "APLAT", "HEADBUG", "HEAD", "FDROLL", "ROLL", "EXTCRS",
        "APMSG"]


def now():
    return time.time()


def connect():
    for _ in range(60):          # netfix refuses back-to-back connects
        try:
            return socket.create_connection(("127.0.0.1", PORT), timeout=5)
        except OSError:
            time.sleep(1)
    raise SystemExit("no gateway on port %d" % PORT)


class Bus:
    def __init__(self):
        self.s = connect()
        self.buf = b""
        self.lock = threading.Lock()

    def q(self, line):
        with self.lock:
            self.s.sendall((line + "\n").encode())
            while b"\n" not in self.buf:
                chunk = self.s.recv(4096)
                if not chunk:
                    raise SystemExit("gateway closed")
                self.buf += chunk
            out, _, self.buf = self.buf.partition(b"\n")
            return out.decode()

    def val(self, key):
        parts = self.q("@r" + key)[2:].split(";")
        return parts[1] if len(parts) >= 3 else None

    def fval(self, key):
        return float(self.val(key))

    def write(self, key, value):
        return self.q("@w%s;%s" % (key, value))


steps = open(os.path.join(D, "steps.jsonl"), "a")


def note(step, msg, **kw):
    row = dict(t=round(now(), 3), step=step, msg=msg, **kw)
    steps.write(json.dumps(row) + "\n")
    steps.flush()
    print(json.dumps(row), flush=True)


def logger():
    """Subscribe to every key; log each update with its flags."""
    s = connect()
    f = open(os.path.join(D, "bus.jsonl"), "a")
    for k in KEYS:
        s.sendall(("@s%s\n" % k).encode())
        time.sleep(0.02)
    buf = b""
    while not os.path.exists(STOP):
        try:
            s.settimeout(1.0)
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, _, buf = buf.partition(b"\n")
            line = line.decode(errors="replace")
            if line.startswith("@"):
                continue
            parts = line.split(";")
            if len(parts) >= 3:
                f.write(json.dumps({"t": round(now(), 3), "k": parts[0],
                                    "v": parts[1], "f": parts[2]}) + "\n")
        f.flush()


def state(bus):
    return {k: bus.val(k) for k in SNAP}


def press(bus, step, verb):
    bus.write("APCMD", verb)
    seq = int(bus.val("APCMDSEQ")) + 1
    bus.write("APCMDSEQ", seq)
    note(step, "pressed", verb=verb, seq=seq)


def wait(bus, step, what, cond, timeout, steady=0.0):
    t0 = now()
    since = None
    if FAST:
        timeout, steady = min(timeout, 20), min(steady, 1)
    while now() - t0 < timeout and not os.path.exists(STOP):
        try:
            ok = cond(bus)
        except (TypeError, ValueError):
            ok = False
        if ok:
            since = since or now()
            if now() - since >= steady:
                note(step, "PASS " + what, after_s=round(now() - t0, 2),
                     state=state(bus))
                return True
        else:
            since = None
        time.sleep(0.1)
    note(step, "FAIL " + what, after_s=round(now() - t0, 2), state=state(bus))
    return False


def pause(seconds):
    t0 = now()
    if FAST:
        seconds = min(seconds, 3)
    while now() - t0 < seconds and not os.path.exists(STOP):
        time.sleep(0.2)


def angdiff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


def bug_change(bus, step, delta, settle_s=90):
    """Set the bug ``delta`` from the heading and watch the turn: the signed
    heading change from the start (positive right) and FDROLL, every 0.2 s.
    The short way is the sign of ``delta``; the long way shows as the
    heading moving more than 5 deg against it."""
    h0 = bus.fval("HEAD") % 360.0
    bug = round((h0 + delta) % 360.0, 1)
    bus.write("HEADBUG", "%.1f" % bug)
    note(step, "bug set", head=h0, bug=bug, delta=delta)
    t0 = now()
    track, against, fd_first = [], 0.0, None
    reached = None
    while now() - t0 < settle_s and not os.path.exists(STOP):
        try:
            h = bus.fval("HEAD") % 360.0
            fd = bus.fval("FDROLL")
        except (TypeError, ValueError):
            time.sleep(0.2)
            continue
        moved = angdiff(h, h0)
        # Unwrap past 180 so a long-way turn keeps counting the wrong way.
        if track and abs(moved - track[-1][1]) > 180.0:
            moved += 360.0 if moved < track[-1][1] else -360.0
        track.append((round(now() - t0, 1), round(moved, 1), round(fd, 1)))
        if delta > 0:
            against = max(against, -moved)
        else:
            against = max(against, moved)
        if fd_first is None and abs(fd) > 3.0:
            fd_first = fd
        if reached is None and abs(angdiff(h, bug)) < 5.0:
            reached = round(now() - t0, 1)
            settle_s = min(settle_s, now() - t0 + 10.0)
        time.sleep(0.2)
    short = (fd_first is not None and (fd_first > 0) == (delta > 0)
             and against < 5.0 and reached is not None)
    note(step, ("PASS" if short else "FAIL") + " turned the short way",
         first_fd_roll=fd_first, most_against_deg=round(against, 1),
         reached_s=reached, bus_bug=bus.val("HEADBUG"), state=state(bus),
         track=track[::5])
    return short


def main():
    threading.Thread(target=logger, daemon=True).start()
    bus = Bus()

    note("0-live", "waiting for the driver to couple (APENG, APLAT GPS)")
    wait(bus, "0-live", "AP engaged on GPS",
         lambda b: b.val("APENG") == "True" and b.val("APLAT") == "GPS",
         1200, steady=15)
    c0 = bus.fval("EXTCRS")
    wait(bus, "0-live", "EXTCRS within 2 deg for 20 s",
         lambda b: abs(angdiff(b.fval("EXTCRS"), c0)) < 2.0, 120, steady=20)

    # 1. HDG, with the bug walked to the heading first so the press does not
    #    turn the aircraft.
    head = round(bus.fval("HEAD") % 360.0, 1)
    note("1-hdg", "bug to heading", head=head, bug_before=bus.val("HEADBUG"))
    bus.write("HEADBUG", "%.1f" % head)
    wait(bus, "1-hdg", "bus bug within 1 deg of the heading, steady",
         lambda b: abs(angdiff(b.fval("HEADBUG"), head)) < 1.0, 15, steady=3)
    press(bus, "1-hdg", "HDG")
    wait(bus, "1-hdg", "APLAT HDG", lambda b: b.val("APLAT") == "HDG", 5)
    pause(10)

    # 2-4. The bug changes. +40.2 is the AER-6052 change.
    results = {}
    for name, delta in (("2-plus40", 40.2), ("3-minus40", -40.0),
                        ("4-plus150", 150.0)):
        results[name] = bug_change(bus, name, delta)
        pause(10)
    note("summary", "results", **results)

    # 5. AP disconnect: the servo guard pauses the sim and ends the session.
    press(bus, "5-disc", "DISC")
    wait(bus, "5-disc", "APENG False", lambda b: b.val("APENG") == "False", 5)
    while not os.path.exists(STOP):
        time.sleep(1)
    note("5-disc", "stopped")


if __name__ == "__main__":
    main()
