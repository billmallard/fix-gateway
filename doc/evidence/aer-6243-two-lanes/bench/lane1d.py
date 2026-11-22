#!/usr/bin/env python3
"""Lane 1's host agent for the AER-6243 two-box run, on the Pi 5.

Lane 1 here is a private gateway from the branch, fed by X-Plane over RREF
on its own socket, with its CAN-FIX bus carried to the Beelink by bridge.py.
The Pi's own gateway, pyEfis and sensor rig are not touched.  The agent sits
outside the lane, so it survives the faults it applies, and takes one-word
commands over UDP from the Beelink only (the run's driver, twobox.sh):

  start      bridge, then lane 1's gateway, then the lane-1 recorder
  host_drop  SIGKILL lane 1's gateway and its bridge at the same instant:
             everything the lane runs on this host, gone, and its bus end
             with it (what a power pull does to the software; not the power)
  gw_kill    SIGKILL lane 1's gateway only
  restart    start whatever host_drop or gw_kill stopped
  feed_on / feed_off   dry run only: synthetic PITCH, ROLL, IAS, ALT at 10 Hz
  stop       stop everything; quit: stop and exit

Exits by itself at the deadline (UTC, YYYY-MM-DDTHH:MM).

    python3 lane1d.py SCRATCH_DIR DEADLINE
"""
import datetime
import json
import math
import os
import signal
import socket
import subprocess
import sys
import threading
import time

S, deadline = sys.argv[1], sys.argv[2]
deadline = datetime.datetime.strptime(deadline, "%Y-%m-%dT%H:%M").replace(tzinfo=datetime.timezone.utc).timestamp()
R = os.path.join(S, "run")
H = os.path.dirname(os.path.abspath(__file__))
PY = os.path.expanduser("~/fix-gateway/.venv/bin/python")
BEELINK = "10.110.10.241"
os.makedirs(R, exist_ok=True)
procs = {}
feeding = threading.Event()


def log(*x):
    with open(os.path.join(R, "lane1d.log"), "a") as f:
        f.write(time.strftime("%H:%M:%S", time.gmtime()) + f"{time.time() % 1:.3f}"[1:] + " " + " ".join(map(str, x)) + "\n")


def spawn(name, argv, env=None):
    out = open(os.path.join(R, f"{name}.log"), "a")
    procs[name] = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, **(env or {})), start_new_session=True)
    log("spawned", name, procs[name].pid)


def alive(name):
    return name in procs and procs[name].poll() is None


def start_bridge():
    try:
        os.remove(os.path.join(R, "bridge.json"))
    except OSError:
        pass
    spawn("bridge", ["python3", os.path.join(H, "bridge.py"), R, "47651", BEELINK, "47650"])
    for _ in range(50):
        if os.path.exists(os.path.join(R, "bridge.json")):
            return
        time.sleep(0.1)
    log("bridge did not start")


def start_gw():
    subprocess.run([PY, os.path.join(H, "build.py"), S, "1", "--no-efis"], check=True,
                   stdout=open(os.path.join(R, "build.log"), "a"), stderr=subprocess.STDOUT)
    spawn("gw1", [PY, "-c", "import sys; sys.argv=['fixgw','--config-file',sys.argv[1]]; "
                  "from fixgw.server import main; main()", os.path.join(S, "lane1", "lane.yaml")],
          env={"PYTHONPATH": os.path.join(S, "fgw", "src")})


def kill(names):
    for n in names:
        if alive(n):
            procs[n].send_signal(signal.SIGKILL)
    for n in names:
        if n in procs:
            procs[n].wait(5)
    log("killed", *names)


def feeder():
    t0 = time.time()
    while True:
        feeding.wait()
        try:
            with socket.create_connection(("127.0.0.1", 3497), timeout=2) as s:
                while feeding.is_set():
                    t = time.time() - t0
                    s.sendall(("PITCH;%.2f\nROLL;%.2f\nIAS;%.1f\nALT;%.0f\n" % (
                        2 + 3 * math.sin(t / 7), 15 * math.sin(t / 11),
                        110 + 5 * math.sin(t / 13), 4500 + 200 * math.sin(t / 17))).encode())
                    time.sleep(0.1)
        except OSError:
            time.sleep(0.5)


threading.Thread(target=feeder, daemon=True).start()
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 47660))
sock.settimeout(5)
log("listening; deadline", deadline)
while time.time() < deadline:
    try:
        data, addr = sock.recvfrom(64)
    except socket.timeout:
        continue
    if addr[0] != BEELINK:
        continue
    cmd = data.decode(errors="replace").strip()
    log("command", cmd)
    if cmd == "start":
        start_bridge()
        start_gw()
        if not alive("recorder"):
            spawn("recorder", ["python3", os.path.join(H, "recorder.py"), R, "3497", "1"])
    elif cmd == "host_drop":
        kill(["gw1", "bridge"])
    elif cmd == "gw_kill":
        kill(["gw1"])
    elif cmd == "restart":
        if not alive("bridge"):
            start_bridge()
        if not alive("gw1"):
            start_gw()
    elif cmd == "feed_on":
        feeding.set()
    elif cmd == "feed_off":
        feeding.clear()
    elif cmd in ("stop", "quit"):
        feeding.clear()
        kill([n for n in procs])
        if cmd == "quit":
            break
    sock.sendto(f"ok {cmd} {json.dumps({n: alive(n) for n in procs})}".encode(), addr)
feeding.clear()
kill([n for n in procs])
log("exit")
