"""AER-4675: replay the SIM2b X-Plane trajectory through the 61 deg fly-by at
HENER (trace t 100-200 s of sim2b_v27_xplane_2026-11-01.csv.gz, fix-gateway
tests/sim/fixtures) into the live bench gateway at its recorded 5 Hz, with the
golden route published through pyEfis's FixBridge and ACT 6 (the leg into
HENER). The bus is logged at 2 Hz. At three points the position is held and
both the Flight Plan page and the Map are grabbed: before turn start, in the
turn window, and past the bisector. Then the route and GPSSRC/NAVSRC it found
are put back. X-Plane is not running: the trajectory is X-Plane's, the
engine and the glass are live."""
import json, signal, sys, threading, time
sys.path.insert(0, "../aer4464")
import v27_follow as v

v.LOG_KEYS.append("FPLALERT")
HOLDS = {160.0: "1_before_turn", 168.6: "2_in_turn", 177.0: "3_past_bisector"}
HOLD_S = 14.0
MAGVAR = -11.449          # the SIM2b recording's bench FIX MAGVAR
FPL_TAB, MAP_TAB = (1346, 13), (1278, 13)


class A:
    dir = "out"
    until = None


f = v.Follower(A())
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
rows = json.load(open("hener_rows.json"))
c = f.c
snap = v.snapshot_route(c)
json.dump(snap, open(f.dir + "/route_before.json", "w"))
f.log("replay: route snapshot", snap["name"], snap["count"], "slots")
saved = {k: c.read(k)[0] for k in ("GPSSRC", "NAVSRC")}
c.write("MAGVAR", MAGVAR)
time.sleep(0.5)
v.publish_golden(c, f.log)
c.write("GPSSRC", 0)
c.write("NAVSRC", 2)
t0, lat, lon, alt, gs, trk = rows[0][:6]
for k, val in (("MAGVAR", MAGVAR), ("LAT", lat), ("LONG", lon), ("ALT", alt), ("GS", gs), ("TRACK", trk)):
    c.write(k, val)
time.sleep(1)
ack = int(float(c.read("FPLCMDACK")[0] or 0))
c.write("FPLCMD", "%d ACT 6" % (abs(ack) + 100))
time.sleep(2)
f.log("replay: ACT 6", {x: c.read(x)[0] for x in ("FPLMSG", "FPLACTLEG", "WPNAME", "FPLCRS")})


def tab(xy):
    rc, out = f.locked("DISPLAY=:0 xdotool mousemove %d %d click 1" % xy, wait=20)
    f.log("tab", xy, rc, out[:100])


tab(FPL_TAB)
stop = threading.Event()
th = threading.Thread(target=f.logger, args=("hener", stop), daemon=True)
th.start()
w = v.NFX()


def write(lat, lon, alt, gs, trk, xp_crs):
    w.write("MAGVAR", MAGVAR); w.write("ALT", alt); w.write("GS", gs); w.write("TRACK", trk)
    w.write("LAT", lat); w.write("LONG", lon); w.write("EXTCRS", xp_crs)


def hold(name, r):
    f.log("HOLD", name, "t", r[0], {x: c.read(x)[0] for x in
          ("FPLACTLEG", "WPNAME", "WPDIS", "FPLCRS", "FPLXTK", "FPLCDI", "FPLTF", "FPLALERT", "FPLFRLAT", "FPLFRLON")})
    end = time.time() + HOLD_S
    shots = [(1.5, lambda: f.grab(name + "_fpl")), (2.0, lambda: tab(MAP_TAB)), (4.0, lambda: f.grab(name + "_map")),
             (0.5, lambda: tab(FPL_TAB))]
    for delay, act in shots:
        tt = time.time() + delay
        while time.time() < tt:
            write(*r[1:6], r[7]); time.sleep(0.2)
        act()
    while time.time() < end:
        write(*r[1:6], r[7]); time.sleep(0.2)


pending = sorted(HOLDS)
start = time.time()
shift = 0.0
try:
    for r in rows:
        t = r[0]
        if pending and t >= pending[0]:
            h0 = time.time()
            hold(HOLDS[pending.pop(0)], r)
            shift += time.time() - h0
        d = start + shift + (t - t0) - time.time()
        if d > 0:
            time.sleep(d)
        write(*r[1:6], r[7])
finally:
    time.sleep(2)
    stop.set(); th.join(5)
    v.restore_route(c, snap)
    for k, val in saved.items():
        c.write(k, val)
    time.sleep(2)
    f.log("replay done; route restored:", snap["name"], c.read("FPLCOUNT")[0], "src", saved)
