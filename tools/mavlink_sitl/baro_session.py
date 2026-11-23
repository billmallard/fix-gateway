"""MAVALT1 (AER-6351): does the altimeter follow the baro knob?

ArduPlane SITL, the real mavlink plugin (ahrs and pressure on) in the real
fixgw server with compute on, and a netfix client that turns BARO the way
the pilot's knob does. ALT, TALT, PALT, AIRPRESS and BARO are sampled at
four settings on the ground and two in cruise. Then NAV engages GUIDED, and
the DO_REPOSITION altitude the plugin sent is read back from its tlog.

usage: baro_session.py <fix-gateway tree> <out dir>
Run with pymavlink on PYTHONPATH. The arduplane binary and plane.parm sit
beside this script (README.md, "Regenerate").

pascal_offset is set to 0: SITL's barometer has no calibration error, and
the shipped 2800 Pa (one sensor's correction) would shift ALT about 770 ft.

For the glass, three environment hooks (each a shell command):
  BARO_NETFIX_PORT  netfix on this fixed loopback port, not a free one, so a
                    display can be pointed at it
  BARO_ON_SERVER    run once the server is up (start the display)
  BARO_ON_SAMPLE    run after each sample with the label as $1 (grab the
                    screen); the sample waits BARO_SETTLE s first (default 1.5)
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time

from pymavlink import mavutil

TREE = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])
HERE = os.path.abspath(os.path.dirname(__file__))
INSTANCE = 7
SERIAL0, SERIAL1 = 5760 + 10 * INSTANCE, 5762 + 10 * INSTANCE
HOME = "34.4258,-119.8403,3,250"     # KSBA, 3 m MSL, runway 25 heading
WP = (34.4400, -119.9200, "WPT1")
KEYS = ("BARO", "AIRPRESS", "ALT", "PALT", "TALT", "AGL")
SETTINGS_GROUND = (29.92, 30.92, 28.92, 29.92)
SETTINGS_CRUISE = (29.92, 30.92, 28.92)

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

run = os.path.join(OUT, "sitl")
os.makedirs(run)
procs.append(subprocess.Popen(
    [os.path.join(HERE, "arduplane"), "-w", "-M", "plane", "-O", HOME,
     "--defaults", os.path.join(HERE, "plane.parm"), "-I", str(INSTANCE)],
    cwd=run, stdout=open(run + "/sitl.log", "w"), stderr=subprocess.STDOUT,
    start_new_session=True))
time.sleep(2)

link = os.path.join(OUT, "ttyFC")
ctl = os.path.join(OUT, "ctl")
os.makedirs(ctl)
procs.append(subprocess.Popen(
    [sys.executable, os.path.join(HERE, "bridge.py"), str(SERIAL0), link,
     os.path.join(OUT, "fc.tlog"), ctl, os.path.join(OUT, "gcs.tlog")],
    stdout=open(OUT + "/bridge.log", "w"), stderr=subprocess.STDOUT,
    start_new_session=True))
for _ in range(50):
    if os.path.lexists(link):
        break
    time.sleep(0.1)

time.sleep(1)
rc = mavutil.mavlink_connection("tcp:127.0.0.1:%d" % SERIAL1, source_system=250)
rc.wait_heartbeat(timeout=60)
say("rc link up")


def set_param(name, value):
    rc.mav.param_set_send(rc.target_system, rc.target_component,
                          name.encode(), float(value),
                          mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    rc.recv_match(type="PARAM_VALUE", blocking=True, timeout=5)


def rc_mode(mode):
    num = {v: k for k, v in mavutil.mode_mapping_apm.items()}[mode]
    rc.mav.command_long_send(rc.target_system, rc.target_component,
                             mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
                             mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
                             num, 0, 0, 0, 0, 0)
    say("RC: mode", mode)


set_param("TKOFF_ALT", 300)
set_param("TKOFF_THR_MAX", 100)

nf = int(os.environ.get("BARO_NETFIX_PORT") or free_tcp())
SETTLE = float(os.environ.get("BARO_SETTLE", "1.5"))
cfg = os.path.join(OUT, "config")
shutil.copytree(os.path.join(TREE, "src/fixgw/config"), cfg)
open(cfg + "/preferences.yaml.custom", "w").write(
    "enabled:\n  MAVLINK: true\n  DEMO: false\n  STATE_PERSIST: false\n"
    "  FLIGHTPLAN: false\n  COMPUTE: true\n  XPLANE: false\n")


def edit(f, a, b):
    p = cfg + "/connections/" + f
    x = open(p).read()
    assert x.count(a) == 1, (f, a)
    open(p, "w").write(x.replace(a, b))


edit("netfix.yaml", "port: 3490", "port: %d" % nf)
if "host: 0.0.0.0" in open(cfg + "/connections/netfix.yaml").read():
    edit("netfix.yaml", "host: 0.0.0.0", "host: 127.0.0.1")
edit("mavlink.yaml", "port: /dev/ttyACM0", "port: %s" % link)
edit("mavlink.yaml", "pascal_offset: 2800", "pascal_offset: 0")
env = dict(os.environ, PYTHONPATH=os.path.join(TREE, "src") + ":"
           + os.environ.get("PYTHONPATH", ""))
procs.append(subprocess.Popen(
    [sys.executable, "-c",
     "import sys; from fixgw.server import main; sys.exit(main())",
     "--config-file", cfg + "/default.yaml", "--debug"],
    env=env, cwd=OUT, stdout=open(OUT + "/server.log", "w"),
    stderr=subprocess.STDOUT, start_new_session=True))
time.sleep(4)
if os.environ.get("BARO_ON_SERVER"):
    subprocess.call(os.environ["BARO_ON_SERVER"], shell=True)
    say("hook: on server")
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
    """(value, flags) of a key; flags are netfix's a/o/b/f/s digits."""
    parts = q("@r" + k).split(";")
    return parts[1], parts[2]


def wait_for(label, pred, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            say("ok", label)
            return True
        time.sleep(0.2)
    say("TIMEOUT", label)
    return False


def sample(phase):
    s = {k: r(k) for k in KEYS}
    say("SAMPLE", phase, " ".join("%s=%s/%s" % (k, v[0], v[1])
                                  for k, v in s.items()))
    return s


def grab(label):
    if os.environ.get("BARO_ON_SAMPLE"):
        subprocess.call([os.environ.get("SHELL", "/bin/sh"), "-c",
                         os.environ["BARO_ON_SAMPLE"], "grab", label])


def sweep(phase, settings):
    for b in settings:
        q("@wBARO;%.2f" % b)
        time.sleep(SETTLE)  # several SCALED_PRESSUREs at the plugin's 5 Hz
        sample(phase)
        grab("%s-%.2f" % (phase, b))


wait_for("link", lambda: r("MAVMODE")[0] == "TRIM", 90)
wait_for("armed", lambda: r("MAVSTATE")[0] == "ARMED", 120)
time.sleep(3)
sweep("ground", SETTINGS_GROUND)


def waypoint_feed():
    w = socket.create_connection(("127.0.0.1", nf))
    try:
        while True:
            for line in ("@wWPLAT;%f" % WP[0], "@wWPLON;%f" % WP[1],
                         "@wWPNAME;%s" % WP[2]):
                w.sendall((line + "\n").encode())
            w.recv(4096)
            time.sleep(5)
    except OSError:
        return  # the server is gone at the end of the session


threading.Thread(target=waypoint_feed, daemon=True).start()

rc_mode("TAKEOFF")
t0 = time.time()
m = None
while time.time() - t0 < 120:
    m = rc.recv_match(type="GLOBAL_POSITION_INT", blocking=True, timeout=2)
    if m and m.relative_alt > 250000:
        break
say("climbed, rel alt m", m and m.relative_alt / 1000)
rc_mode("CRUISE")
wait_for("CRUISE", lambda: r("MAVMODE")[0] == "CRUISE")
time.sleep(3)
sweep("cruise", SETTINGS_CRUISE)
q("@wBARO;30.92")
time.sleep(SETTLE)

# Back to 30.92, so ALT and TALT differ by about 900 ft when NAV goes.
seq = int(r("APCMDSEQ")[0]) + 1
q("@wAPCMD;NAV")
q("@wAPCMDSEQ;%d" % seq)
say("HEAD: press NAV")
wait_for("GUIDED", lambda: r("MAVMODE")[0] == "GUIDED", 20)
sample("guided-start")
time.sleep(20)
sample("guided+20s")
say("MAVMSG", r("MAVMSG"))

cleanup()
time.sleep(1)
reader = mavutil.mavlink_connection(os.path.join(OUT, "gcs.tlog"))
while True:
    msg = reader.recv_match(type="COMMAND_INT")
    if msg is None:
        break
    if msg.command == mavutil.mavlink.MAV_CMD_DO_REPOSITION:
        say("SENT DO_REPOSITION frame=%d flags=%d lat=%d lon=%d z=%.2f m (%.1f ft)"
            % (msg.frame, msg.param2, msg.x, msg.y, msg.z, msg.z / 0.3048))
say("done")
