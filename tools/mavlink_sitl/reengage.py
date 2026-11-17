"""The GUIDED re-engagement hazard (AER-5822, found while recording AFC7;
fixed by AFC7a, AER-5897).
session.py up to CRUISE, then three cases, each after the plugin itself
engaged GUIDED (NAV) and the transmitter took MANUAL:

  A. the flight plan moves to the next waypoint 2 s later;
  B. the waypoint moves at the same instant as the switch, before the
     plugin has seen a MANUAL heartbeat;
  C. the waypoint goes invalid (the flight plan is cleared).

ArduPlane must stay MANUAL in all three. Two checks follow:

  D. with no transmitter switch, a new waypoint still moves the GUIDED
     target the plugin engaged (it must stay GUIDED);
  E. in MANUAL, a DO_REPOSITION without the mode-change flag, sent on the
     transmitter link, is refused and ArduPlane stays MANUAL. The plugin's
     waypoint updates rely on this when the switch beats the heartbeat.
 Its mode is read from its own
heartbeats on the transmitter link, which the plugin does not share, and
every change is logged with its time.

AFC7 end to end: ArduPlane SITL, the real mavlink plugin in the real fixgw
server, a netfix client as the control head, a second MAVLink link as the
pilot's RC transmitter.

usage: reengage.py <fix-gateway tree> <out dir>
Run with pymavlink on PYTHONPATH. Every key read is logged with its flags.
The autopilot's side is recorded in <out>/fc.tlog, the plugin's in
<out>/gcs.tlog.
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

from pymavlink import mavutil

TREE = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])
HERE = os.path.abspath(os.path.dirname(__file__))
INSTANCE = 7
SERIAL0, SERIAL1 = 5760 + 10 * INSTANCE, 5762 + 10 * INSTANCE
HOME = "34.4258,-119.8403,3,250"     # KSBA, runway 25 heading
WP = (34.4400, -119.9200, "WPT1")    # about 8 km west-northwest

os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    p = os.path.join(OUT, f)
    shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
log = open(os.path.join(OUT, "session.log"), "w")


def say(*a):
    line = "%.3f %s" % (time.time(), " ".join(str(x) for x in a))
    print(line, flush=True)
    log.write(line + "\n")
    log.flush()


def free_tcp():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


procs = []


def cleanup():
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except Exception:
            pass


import atexit  # noqa: E402

atexit.register(cleanup)

# --- SITL -----------------------------------------------------------------
run = os.path.join(OUT, "sitl")
os.makedirs(run)
sitl = subprocess.Popen(
    [os.path.join(HERE, "arduplane"), "-w", "-M", "plane", "-O", HOME,
     "--defaults", os.path.join(HERE, "plane.parm"), "-I", str(INSTANCE)],
    cwd=run, stdout=open(run + "/sitl.log", "w"), stderr=subprocess.STDOUT,
    start_new_session=True)
procs.append(sitl)
time.sleep(2)

# --- bridge (plugin side is SERIAL0, so SITL starts once it connects) -------
link = os.path.join(OUT, "ttyFC")
ctl = os.path.join(OUT, "ctl")
os.makedirs(ctl)
bridge = subprocess.Popen(
    [sys.executable, os.path.join(HERE, "bridge.py"), str(SERIAL0), link,
     os.path.join(OUT, "fc.tlog"), ctl, os.path.join(OUT, "gcs.tlog")],
    stdout=open(OUT + "/bridge.log", "w"), stderr=subprocess.STDOUT,
    start_new_session=True)
procs.append(bridge)
for _ in range(50):
    if os.path.lexists(link):
        break
    time.sleep(0.1)

# --- the transmitter link --------------------------------------------------
time.sleep(1)
rc = mavutil.mavlink_connection("tcp:127.0.0.1:%d" % SERIAL1, source_system=250)
rc.wait_heartbeat(timeout=60)
say("rc link up, target", rc.target_system, rc.target_component)


def set_param(name, value):
    rc.mav.param_set_send(rc.target_system, rc.target_component,
                          name.encode(), float(value),
                          mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    m = rc.recv_match(type="PARAM_VALUE", blocking=True, timeout=5)
    say("param", name, value, "->", m and (m.param_id, m.param_value))


def rc_mode(mode):
    """What a mode switch on the transmitter does: a mode change the
    head did not ask for."""
    num = {v: k for k, v in mavutil.mode_mapping_apm.items()}[mode]
    rc.mav.command_long_send(rc.target_system, rc.target_component,
                             mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
                             mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                             num, 0, 0, 0, 0, 0)
    say("RC: mode", mode)


def rc_hb():
    m = rc.recv_match(type="HEARTBEAT", blocking=True, timeout=3)
    while m is not None and m.get_srcSystem() != rc.target_system:
        m = rc.recv_match(type="HEARTBEAT", blocking=True, timeout=3)
    return m


set_param("TKOFF_ALT", 300)
set_param("TKOFF_THR_MAX", 100)

# --- fixgw ---------------------------------------------------------------
nf = free_tcp()
cfg = os.path.join(OUT, "config")
shutil.copytree(os.path.join(TREE, "src/fixgw/config"), cfg)
open(cfg + "/preferences.yaml.custom", "w").write(
    "enabled:\n  MAVLINK: true\n  DEMO: false\n  STATE_PERSIST: false\n"
    "  FLIGHTPLAN: false\n  COMPUTE: false\n  XPLANE: false\n")


def edit(f, a, b):
    p = cfg + "/connections/" + f
    x = open(p).read()
    assert x.count(a) == 1, (f, a)
    open(p, "w").write(x.replace(a, b))


edit("netfix.yaml", "port: 3490", "port: %d" % nf)
edit("mavlink.yaml", "port: /dev/ttyACM0", "port: %s" % link)
env = dict(os.environ, PYTHONPATH=os.path.join(TREE, "src") + ":" + os.environ.get("PYTHONPATH", ""))
srv = subprocess.Popen(
    [sys.executable, "-c", "import sys; from fixgw.server import main; sys.exit(main())",
     "--config-file", cfg + "/default.yaml", "--debug"],
    env=env, cwd=OUT, stdout=open(OUT + "/server.log", "w"),
    stderr=subprocess.STDOUT, start_new_session=True)
procs.append(srv)
time.sleep(4)
c = socket.create_connection(("127.0.0.1", nf))
c.settimeout(3)
buf = [b""]


def q(line):
    c.sendall((line + "\n").encode())
    while b"\n" not in buf[0]:
        buf[0] += c.recv(4096)
    out, _, buf[0] = buf[0].partition(b"\n")
    return out.decode()


def r(k):
    """(value, old) of a key."""
    parts = q("@r" + k).split(";")
    return parts[1], parts[2][1] == "1"


KEYS = ("APENG", "APLAT", "APVERT", "APMSG", "APDISC", "APCAPS", "FDENG",
        "MAVMODE", "MAVSTATE", "MAVMSG")
records = []


def state(label):
    s = {k: r(k) for k in KEYS}
    records.append({"t": time.time(), "label": label, "keys": s})
    say("%-14s" % label, " | ".join(
        "%s=%s%s" % (k, v[0], "(old)" if v[1] else "") for k, v in s.items()))
    return s


def wait_for(label, pred, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = {k: r(k) for k in KEYS}
        if pred(s):
            return state(label)
        time.sleep(0.2)
    state(label + " TIMEOUT")
    return None


seq = [None]


def press(v):
    if seq[0] is None:
        seq[0] = int(r("APCMDSEQ")[0])
    q("@wAPCMD;" + v)
    seq[0] += 1
    q("@wAPCMDSEQ;%d" % seq[0])
    say("HEAD: press", v)


def mode_is(m):
    return lambda s: s["MAVMODE"][0] == m and not s["APENG"][1]


wait_for("link", lambda s: s["MAVMODE"][0] == "TRIM" and not s["APENG"][1], 90)
state("start")
import threading  # noqa: E402


FEED = [True]


def waypoint_feed():
    """What the flight-plan engine does: keep the active waypoint fresh
    (WPLAT/WPLON have a 50 s tol). Its own connection, so its replies never
    interleave with the head's."""
    w = socket.create_connection(("127.0.0.1", nf))
    while FEED[0]:
        for line in ("@wWPLAT;%f" % WP[0], "@wWPLON;%f" % WP[1],
                     "@wWPNAME;%s" % WP[2]):
            w.sendall((line + "\n").encode())
        w.recv(4096)
        time.sleep(5)


threading.Thread(target=waypoint_feed, daemon=True).start()

# The plugin arms by itself once the pre-arm checks pass.
wait_for("armed", lambda s: s["MAVSTATE"][0] == "ARMED", 120)
press("HDG")
wait_for("HDG refused", lambda s: 'HDG is not available' in s["APMSG"][0], 5)
press("NAV")
wait_for("NAV refused", lambda s: 'engage AP first' in s["APMSG"][0], 5)

rc_mode("TAKEOFF")
wait_for("TAKEOFF", lambda s: s["APENG"][0] == "True" and "TAKEOFF" in s["APMSG"][0])
press("AP")
wait_for("AP in TAKEOFF", lambda s: 'AP refused' in s["APMSG"][0], 5)
# climb
t0 = time.time()
while time.time() - t0 < 120:
    m = rc.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
    if m and m.relative_alt > 250000:
        break
say("climbed, rel alt m", m and m.relative_alt / 1000)

rc_mode("CRUISE")
wait_for("CRUISE (RC)", mode_is("CRUISE"))
time.sleep(3)
state("CRUISE held")


def watch(label, secs):
    """ArduPlane's own mode, from its heartbeats on the transmitter link,
    for ``secs``: every change, with its time. Buffered messages are
    drained first, so nothing older than the call is read."""
    while rc.recv_match(blocking=False) is not None:
        pass
    seen = []
    t0 = time.time()
    while time.time() - t0 < secs:
        m = rc_hb()
        if m is None:
            continue
        name = mavutil.mode_mapping_apm.get(m.custom_mode, m.custom_mode)
        if not seen or seen[-1] != name:
            say("ArduPlane %s: %s" % (label, name))
            seen.append(name)
    say("ArduPlane %s: modes %s" % (label, " -> ".join(map(str, seen))))
    return seen


results = {}


def nav_then_manual(label, wp_lat, same_instant):
    """The plugin engages GUIDED, the transmitter takes MANUAL, then the
    waypoint is written (or, in B, both at once)."""
    if state(label + " start")["MAVMODE"][0] == "GUIDED":
        press("NAV")        # back to CRUISE first
        wait_for(label + " CRUISE", mode_is("CRUISE"))
    if state(label + " start")["APENG"][0] != "True":
        press("AP")
        wait_for(label + " AP", mode_is("CRUISE"))
    press("NAV")
    if wait_for(label + " NAV->GUIDED", mode_is("GUIDED")) is None:
        # The plugin never engaged GUIDED, so the switch tests nothing.
        results[label] = None
        return
    if same_instant:
        rc_mode("MANUAL")
        q("@wWPLAT;%s" % wp_lat)
        say("FPL: waypoint", wp_lat, "with the switch")
    else:
        rc_mode("MANUAL")
        wait_for(label + " MANUAL (RC)", mode_is("TRIM"))
        time.sleep(2)
        state(label + " pilot has it")
        q("@wWPLAT;%s" % wp_lat)
        say("FPL: waypoint", wp_lat)
    results[label] = watch(label, 6)
    state(label + " end")


# From here the script writes the waypoint itself, so only the case's own
# write moves it. Each case starts it fresh (50 s tol) at WPT1.
FEED[0] = False
time.sleep(1)
for case, lat, same_instant in (("A", "34.4500", False),
                                ("B", "34.4550", True),
                                ("C", "0", False)):
    for line in ("@wWPLAT;%f" % WP[0], "@wWPLON;%f" % WP[1],
                 "@wWPNAME;%s" % WP[2]):
        q(line)
    nav_then_manual(case, lat, same_instant)
# D: the plugin's own GUIDED follows the flight plan.
for line in ("@wWPLAT;%f" % WP[0], "@wWPLON;%f" % WP[1], "@wWPNAME;%s" % WP[2]):
    q(line)
press("AP")
wait_for("D AP", mode_is("CRUISE"))
press("NAV")
wait_for("D NAV->GUIDED", mode_is("GUIDED"))
q("@wWPLAT;34.4500")
say("FPL: waypoint 34.4500, no switch")
results["D"] = watch("D", 4)
state("D end")

# E: ArduPlane's own rule for a reposition without the mode-change flag.
rc_mode("MANUAL")
wait_for("E MANUAL (RC)", mode_is("TRIM"))
while rc.recv_match(blocking=False) is not None:
    pass
rc.mav.command_int_send(rc.target_system, rc.target_component,
                        mavutil.mavlink.MAV_FRAME_GLOBAL,
                        mavutil.mavlink.MAV_CMD_DO_REPOSITION, 0, 0,
                        0, 0, 0, 0, int(34.45 * 1e7), int(-119.92 * 1e7), 300)
ack = rc.recv_match(type="COMMAND_ACK", blocking=True, timeout=3)
say("E: DO_REPOSITION change_mode=0 in MANUAL ->",
    ack and mavutil.mavlink.enums["MAV_RESULT"][ack.result].name)
results["E"] = watch("E", 3)
for k, v in results.items():
    say("case %s: %s" % (k, "VOID, the plugin never engaged GUIDED" if v is None
                         else "ArduPlane stayed " + v[0] if len(v) == 1
                         else "ArduPlane went " + " -> ".join(map(str, v))))
json.dump(records, open(os.path.join(OUT, "states.json"), "w"), indent=1)
srv.send_signal(signal.SIGINT)
try:
    srv.wait(10)
except Exception:
    pass
say("done")
