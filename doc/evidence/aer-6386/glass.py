#!/usr/bin/env python3
"""AER-5856 (AFC2b) glass check (pyEfis edits; built from the bench sequencer: the pilot's settings both ways between a
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
import atexit, json, os, socket, struct, sys, threading, time

D = sys.argv[1]
def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
PORT = int(arg("--port", 3490)); XP = arg("--xp", "127.0.0.1"); XPP = int(arg("--xp-port", 49000))
FAST = "--fast" in sys.argv
STOP = os.path.join(D, "STOP")

# key, dataref, eps, angle, step, knob command, netfix format
KEYS = [
    ("HEADBUG", "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot", 0.5, True, 1, "sim/autopilot/heading_up", "%.1f"),
    ("NAV1CRS", "sim/cockpit2/radios/actuators/nav1_obs_deg_mag_pilot", 0.1, True, 1, "sim/radios/obs1_up", "%.1f"),
    ("NAV2CRS", "sim/cockpit2/radios/actuators/nav2_obs_deg_mag_pilot", 0.1, True, 1, "sim/radios/obs2_up", "%.1f"),
    ("BARO", "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot", 0.005, False, 0.01, "sim/instruments/barometer_up", "%.2f"),
    ("ALTSEL", "sim/cockpit2/autopilot/altitude_dial_ft", 1.0, False, 100, "sim/autopilot/altitude_up", "%d"),
    ("VSSEL", "sim/cockpit2/autopilot/vvi_dial_fpm", 1.0, False, 100, "sim/autopilot/vertical_speed_up", "%d"),
    ("IASSEL", "sim/cockpit2/autopilot/airspeed_dial_kts", 0.5, False, 1, "sim/autopilot/airspeed_up", "%.1f"),
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
BUSLOG = open(os.path.join(D, "bus.log"), "a", buffering=1 << 16)
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
        v = float(parts[1]) if len(parts) >= 3 else None
        BUSLOG.write("%.3f %s %r\n" % (now(), key, v))
        return v

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



import subprocess
GLASS = {
    # key: (tap x, tap y, value per wheel notch, source taps to NAVSRC wanted or None)
    "HEADBUG": (566, 678, 1, 2),
    "NAV1CRS": (690, 678, 1, 0),
    "NAV2CRS": (690, 678, 1, 1),
    "BARO": (1060, 631, 0.01, None),
    "ALTSEL": (1823, 288, 100, None),
    "VSSEL": (1549, 288, 100, None),
    "IASSEL": (1686, 288, 1, None),
}
AP_HDG_BOX = (1378, 288)
# The AFC4 VSSEL and IASSEL bugs draw only in their vertical mode: the sim's
# own buttons, with the adapter's command names; ALT hold afterwards.
MODE = {"VSSEL": ("sim/autopilot/alt_vs", "VS"), "IASSEL": ("sim/autopilot/level_change", "IAS")}
ALT_HOLD = ("sim/autopilot/altitude_hold", "ALT")
SRC = (505, 669)
ENV = dict(os.environ, DISPLAY=":0")


def x(*a):
    subprocess.run(["xdotool"] + [str(v) for v in a], env=ENV, check=False)


def tap(xy):
    x("mousemove", xy[0], xy[1], "click", 1); time.sleep(0.5)


def wheel(n, delay=150):
    if n:
        x("click", "--repeat", abs(n), "--delay", delay, 4 if n > 0 else 5)
        time.sleep(0.3)


def grab(name):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-i", ":0",
                    "-frames:v", "1", os.path.join(D, "frames", name + ".png")], env=ENV, check=False)


def check(step, what, ok, **kw):
    note(step, ("PASS " if ok else "FAIL ") + what, **kw)
    return ok


def source(bus, want):
    for _ in range(4):
        if bus.val("NAVSRC") == want:
            return True
        tap(SRC); time.sleep(0.7)
    return bus.val("NAVSRC") == want


def run_key(bus, sim, spec):
    key, dref, eps, angle, step, cmd, fmt = spec
    tx, ty, notch, src = GLASS[key]
    s = key
    if key in MODE:
        a0 = sim.dial["ALTSEL"]
        for _ in range(10):
            sim.press("sim/autopilot/altitude_up"); time.sleep(0.15)
        check(s, "ALTSEL 1000 ft up first", wait("", s, lambda: sim.dial["ALTSEL"] - a0 >= 999, 5), sim=sim.dial["ALTSEL"])
        if not vmode(bus, sim, s, *MODE[key]):
            altsel_back(sim, s, a0)
            return
    try:
        run_edits(bus, sim, spec, s, key, dref, eps, angle, step, cmd, fmt, tx, ty, notch, src)
    finally:
        if key in MODE:
            vmode(bus, sim, s, *ALT_HOLD)
            altsel_back(sim, s, a0)


def altsel_back(sim, s, a0):
    for _ in range(int(round((sim.dial["ALTSEL"] - a0) / 100))):
        sim.press("sim/autopilot/altitude_down"); time.sleep(0.15)
    check(s, "ALTSEL put back", wait("", s, lambda: abs(sim.dial["ALTSEL"] - a0) < 1, 5), sim=sim.dial["ALTSEL"])


def vapvert(bus):
    return bus.q("@rAPVERT").split(";")[1]


def vmode(bus, sim, s, cmd, want):
    for _ in range(3):
        sim.press(cmd)
        if wait("", s, lambda: vapvert(bus) == want, 4, steady=1):
            break
    time.sleep(2.5)
    return check(s, "vertical mode " + want, vapvert(bus) == want, apvert=bus.q("@rAPVERT"))


def run_edits(bus, sim, spec, s, key, dref, eps, angle, step, cmd, fmt, tx, ty, notch, src):
    if src is not None and not check(s, "HSI source for the edit", source(bus, src), navsrc=bus.val("NAVSRC")):
        return
    start = bus.val(key)
    note(s, "start", bus=start, sim=sim.dial.get(key))
    grab(s + "-0-start")
    # pilot: tap the readout, five notches up
    want = norm(round(start / notch) * notch + 5 * notch, angle)
    tap((tx, ty)); wheel(5)
    ok = wait("", s, lambda: diff(bus.val(key), want, angle) < eps, 3)
    check(s, "pyEfis edit reached the bus", ok, want=want, bus=bus.val(key))
    ok = wait("", s, lambda: diff(norm(sim.dial[key], angle), want, angle) < eps, 5)
    check(s, "pyEfis edit: X-Plane's dial followed", ok, want=want, sim=sim.dial[key])
    bad = []
    t0 = now()
    while now() - t0 < 5:
        b = bus.val(key)
        if diff(b, want, angle) >= eps or diff(norm(sim.dial[key], angle), want, angle) >= eps:
            bad.append((round(now() - t0, 2), b, sim.dial[key]))
        time.sleep(0.05)
    check(s, "pyEfis edit: bus and dial stayed 5 s", not bad, first_bad=bad[:3])
    grab(s + "-1-pilot")
    # cockpit: the sim's knob, three presses; the glass follows
    before = sim.dial[key]
    for _ in range(3):
        sim.press(cmd); time.sleep(0.25)
    moved = wait("", s, lambda: diff(sim.dial[key], before, angle) >= eps, 3)
    ok = wait("", s, lambda: diff(bus.val(key), norm(sim.dial[key], angle), angle) < eps, 3, steady=2)
    check(s, "cockpit: the bus follows X-Plane's knob", ok and moved, before=before, bus=bus.val(key), sim=sim.dial[key])
    time.sleep(1.5); grab(s + "-2-cockpit")
    # spin: tap, 15 notches 40 ms apart
    base = bus.val(key)
    clicks = [norm(base + (i + 1) * notch, angle) for i in range(15)]
    path = [base] + clicks
    seen = []
    tap((tx, ty))
    proc = subprocess.Popen(["xdotool", "click", "--repeat", "15", "--delay", "40", "4"], env=ENV)
    while proc.poll() is None:
        seen.append(bus.val(key))
    t1 = now() + 3
    while now() < t1:
        seen.append(bus.val(key)); time.sleep(0.02)
    def pos(v):
        m = [i for i, p in enumerate(path) if diff(v, p, angle) < eps]
        return m[0] if m else None
    idx = [pos(v) for v in seen]
    off = [v for v, i in zip(seen, idx) if i is None]
    back = [(a, b) for a, b in zip(idx, idx[1:]) if a is not None and b is not None and b < a]
    check(s, "spin: bus never off the click path or stepping back", not off and not back,
          samples=len(seen), off=off[:3], back=back[:3], reached=max([i for i in idx if i is not None] or [0]))
    ok = wait("", s, lambda: diff(norm(sim.dial[key], angle), seen[-1], angle) < eps, 3, steady=1)
    check(s, "spin: X-Plane's dial ends where the bus ends", ok and diff(seen[-1], clicks[-1], angle) < eps,
          last=clicks[-1], bus=seen[-1], sim=sim.dial[key])
    time.sleep(1.5); grab(s + "-3-spin")
    # restore by the knob: back down to the start
    tap((tx, ty))
    d = round(start / notch) * notch - bus.val(key)
    if angle:
        d = (d + 540.0) % 360.0 - 180.0
    wheel(int(round(d / notch)), delay=60)
    ok = wait("", s, lambda: diff(norm(sim.dial[key], angle), norm(round(start / notch) * notch, angle), angle) < eps, 5, steady=1)
    check(s, "restore", ok, want=start, bus=bus.val(key), sim=sim.dial[key])
    if src is not None:
        source(bus, 2)


def ap_hdg_box(bus, sim):
    """The AFC3 panel's HDG box: the same key from a second pyEfis control."""
    s = "HEADBUG-AFC3"
    start = bus.val("HEADBUG")
    want = norm(round(start) + 3, True)
    tap(AP_HDG_BOX); wheel(3)
    ok = wait("", s, lambda: diff(norm(sim.dial["HEADBUG"], True), want, True) < 0.5, 5, steady=2)
    check(s, "AFC3 HDG box: X-Plane's dial followed and held", ok, want=want, bus=bus.val("HEADBUG"), sim=sim.dial["HEADBUG"])
    grab(s)
    tap(AP_HDG_BOX); wheel(-3)
    wait("", s, lambda: diff(norm(sim.dial["HEADBUG"], True), norm(round(start), True), True) < 0.5, 5)


def main():
    os.makedirs(os.path.join(D, "frames"), exist_ok=True)
    sim = Sim(); bus = Bus()
    note("0-live", "waiting for the dials and a coupled autopilot")
    ok = wait("", "0-live", lambda: len(sim.heard) == len(KEYS) and ("--dry" in sys.argv or bus.q("@rAPENG").split(";")[1] == "True"), 1200, steady=15 if "--dry" not in sys.argv else 1)
    note("0-live", "PASS live" if ok else "FAIL live", dials=sim.dial, aplat=bus.q("@rAPLAT"))
    grab("0-live")
    if ok:
        for spec in KEYS:
            if os.path.exists(STOP):
                break
            try:
                run_key(bus, sim, spec)
            except Exception as e:
                note(spec[0], "FAIL exception %r" % e)
            time.sleep(2)
        try:
            ap_hdg_box(bus, sim)
        except Exception as e:
            note("HEADBUG-AFC3", "FAIL exception %r" % e)
        source(bus, 2)
    x("mousemove", 960, 550)
    note("end", "done"); sim.stop()
    open(os.path.join(D, "GLASS_DONE"), "w").close()


atexit.register(BUSLOG.close)
main()
