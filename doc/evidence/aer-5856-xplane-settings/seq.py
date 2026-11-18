#!/usr/bin/env python3
"""AER-5856 (AFC2b) bench sequencer: the pilot's settings both ways between a
private fixgw (this branch) and X-Plane.

    python3 seq.py <dir> --port 3592 --xp <x-plane ip> [--xp-port 49000] [--fast]

Writes go to the private gateway over netfix, the path pyEfis uses. X-Plane's
dials are read independently: this script subscribes the seven dials over
its own RREF socket (truth.jsonl), and turns X-Plane's own knobs with CMND
packets (the cockpit side). For each key:
  pilot   write a new value; X-Plane's dial follows and the bus keeps it 5 s
  cockpit press the sim's knob 3 times; the bus follows the sim's dial
  spin    15 clicks 40 ms apart; the bus never steps back, both end on the last
  restore write the starting value back
steps.jsonl logs each PASS/FAIL with what was read.
"""
import json, os, socket, struct, sys, threading, time

D = sys.argv[1]
def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
PORT = int(arg("--port", 3592)); XP = arg("--xp", "127.0.0.1"); XPP = int(arg("--xp-port", 49000))
FAST = "--fast" in sys.argv
STOP = os.path.join(D, "STOP")

# key, dataref, eps, angle, step, knob command, netfix format
KEYS = [
    ("HEADBUG", "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot", 0.1, True, 1, "sim/autopilot/heading_up", "%.1f"),
    ("NAV1CRS", "sim/cockpit2/radios/actuators/nav1_obs_deg_mag_pilot", 0.1, True, 1, "sim/radios/obs1_up", "%.1f"),
    ("NAV2CRS", "sim/cockpit2/radios/actuators/nav2_obs_deg_mag_pilot", 0.1, True, 1, "sim/radios/obs2_up", "%.1f"),
    ("BARO", "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot", 0.005, False, 0.01, "sim/instruments/barometer_up", "%.2f"),
    ("ALTSEL", "sim/cockpit2/autopilot/altitude_dial_ft", 1.0, False, 100, "sim/autopilot/altitude_up", "%d"),
    ("VSSEL", "sim/cockpit2/autopilot/vvi_dial_fpm", 1.0, False, 100, "sim/autopilot/vertical_speed_up", "%d"),
    ("IASSEL", "sim/cockpit2/autopilot/airspeed_dial_kts", 0.05, False, 1, "sim/autopilot/airspeed_up", "%.1f"),
]
TRUTH_BASE = 7000


def now():
    return time.time()


def diff(a, b, angle):
    d = abs(a - b)
    if angle:
        d %= 360.0
        d = min(d, 360.0 - d)
    return d


def norm(v, angle):
    return v % 360.0 if angle else v


steps = open(os.path.join(D, "steps.jsonl"), "a")
def note(step, msg, **kw):
    row = dict(t=round(now(), 3), step=step, msg=msg, **kw)
    steps.write(json.dumps(row) + "\n"); steps.flush(); print(json.dumps(row), flush=True)


class Sim:
    """X-Plane's dials (own RREF subscription) and its knobs (CMND)."""
    def __init__(self):
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind(("0.0.0.0", int(arg("--truth-port", 0)))); self.s.settimeout(0.5)
        self.dial = {}; self.heard = {}
        self.f = open(os.path.join(D, "truth.jsonl"), "a")
        self.subscribe(20)
        threading.Thread(target=self.rx, daemon=True).start()

    def subscribe(self, hz):
        for i, k in enumerate(KEYS):
            self.s.sendto(struct.pack("<4sxii400s", b"RREF", hz, TRUTH_BASE + i, k[1].encode()), (XP, XPP))

    def rx(self):
        nxt = now() + 5
        while not os.path.exists(STOP):
            if now() > nxt:
                self.subscribe(20); nxt = now() + 5
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                continue
            if data[:4] != b"RREF":
                continue
            out = {}
            for off in range(5, len(data) - 7, 8):
                idx, v = struct.unpack_from("<If", data, off)
                if TRUTH_BASE <= idx < TRUTH_BASE + len(KEYS):
                    key = KEYS[idx - TRUTH_BASE][0]
                    if self.dial.get(key) != v:
                        out[key] = v
                    self.dial[key] = v; self.heard[key] = now()
            if out:
                self.f.write(json.dumps({"t": round(now(), 3), "dials": out}) + "\n"); self.f.flush()

    def press(self, cmd):
        self.s.sendto(b"CMND\x00" + cmd.encode(), (XP, XPP))

    def stop(self):
        self.subscribe(0)


class Bus:
    def __init__(self):
        for _ in range(60):
            try:
                self.s = socket.create_connection(("127.0.0.1", PORT), timeout=5); break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("no gateway on port %d" % PORT)
        self.buf = b""

    def q(self, line):
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
        return float(parts[1]) if len(parts) >= 3 else None

    def write(self, key, fmt, value):
        return self.q("@w%s;%s" % (key, fmt % value))


def wait(what, step, cond, timeout, steady=0.0):
    t0 = now(); since = None
    while now() - t0 < timeout and not os.path.exists(STOP):
        try:
            ok = cond()
        except (TypeError, ValueError):
            ok = False
        if ok:
            since = since or now()
            if now() - since >= steady:
                return True
        else:
            since = None
        time.sleep(0.05)
    return False


def check(step, what, ok, **kw):
    note(step, ("PASS " if ok else "FAIL ") + what, **kw)
    return ok


def run_key(bus, sim, spec):
    key, dref, eps, angle, step, cmd, fmt = spec
    s = key
    start = bus.val(key)
    note(s, "start", bus=start, sim=sim.dial.get(key))
    check(s, "bus starts on the sim's dial", diff(start, norm(sim.dial[key], angle), angle) < eps,
          bus=start, sim=sim.dial[key])
    # pilot
    want = norm(start + 5 * step, angle)
    bus.write(key, fmt, want)
    ok = wait("", s, lambda: diff(norm(sim.dial[key], angle), want, angle) < eps, 5)
    check(s, "pilot: X-Plane's dial followed", ok, want=want, sim=sim.dial[key])
    bad = []
    t0 = now()
    while now() - t0 < 5:
        b = bus.val(key)
        if diff(b, want, angle) >= eps or diff(norm(sim.dial[key], angle), want, angle) >= eps:
            bad.append((round(now() - t0, 2), b, sim.dial[key]))
        time.sleep(0.05)
    check(s, "pilot: bus and dial stayed 5 s", not bad, first_bad=bad[:3])
    # cockpit
    before = sim.dial[key]
    for _ in range(3):
        sim.press(cmd); time.sleep(0.25)
    moved = wait("", s, lambda: diff(sim.dial[key], before, angle) >= eps, 3)
    check(s, "cockpit: the sim's knob moved its dial", moved, before=before, sim=sim.dial[key])
    ok = wait("", s, lambda: diff(bus.val(key), norm(sim.dial[key], angle), angle) < eps, 3, steady=2)
    check(s, "cockpit: the bus follows the sim's dial", ok and moved, bus=bus.val(key), sim=sim.dial[key])
    # spin, across 0 for the angles
    base = bus.val(key)
    if angle:
        bus.write(key, fmt, 352.0); wait("", s, lambda: diff(sim.dial[key], 352.0, True) < eps, 4, steady=1)
        base = 352.0
    clicks = [norm(base + (i + 1) * step, angle) for i in range(15)]
    seen = []
    for c in clicks:
        bus.write(key, fmt, c)
        t1 = now() + 0.04
        while now() < t1:
            seen.append(bus.val(key))
    t1 = now() + 3
    while now() < t1:
        seen.append(bus.val(key)); time.sleep(0.02)
    path = [base] + clicks
    def pos(v):
        m = [i for i, p in enumerate(path) if diff(v, p, angle) < eps]
        return m[0] if m else None
    idx = [pos(v) for v in seen]
    off = [v for v, i in zip(seen, idx) if i is None]
    back = [(a, b) for a, b in zip(idx, idx[1:]) if a is not None and b is not None and b < a]
    check(s, "spin: bus never off the click path or stepping back", not off and not back,
          samples=len(seen), off=off[:3], back=back[:3])
    check(s, "spin: bus and dial end on the last click",
          diff(seen[-1], clicks[-1], angle) < eps and diff(norm(sim.dial[key], angle), clicks[-1], angle) < eps,
          last=clicks[-1], bus=seen[-1], sim=sim.dial[key])
    # restore
    bus.write(key, fmt, start)
    ok = wait("", s, lambda: diff(norm(sim.dial[key], angle), start, angle) < eps, 5, steady=1)
    check(s, "restore", ok, want=start, sim=sim.dial[key])


def main():
    sim = Sim(); bus = Bus()
    note("0-live", "waiting for the dials and a coupled autopilot")
    ok = wait("", "0-live", lambda: len(sim.heard) == len(KEYS) and bus.q("@rAPENG").split(";")[1] == "True", 1200 if not FAST else 30, steady=15 if not FAST else 1)
    note("0-live", "PASS live" if ok else "FAIL live", dials=sim.dial, aplat=bus.q("@rAPLAT"))
    if ok:
        for spec in KEYS:
            if os.path.exists(STOP):
                break
            try:
                run_key(bus, sim, spec)
            except Exception as e:
                note(spec[0], "FAIL exception %r" % e)
            time.sleep(2)
    note("end", "done"); sim.stop()
    open(os.path.join(D, "SEQ_DONE"), "w").close()


main()
