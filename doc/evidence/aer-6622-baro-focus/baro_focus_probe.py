# AER-6622 (TESS2): a Tessera press on BARO gives pyEfis's knob to the baro
# setting.  The AER-6630 probe, with key 1 and BARO for key 4 and BARO.
#
# On the Beelink bench, with pyEfis and fix-gateway on the AER-6622 branches:
#   1. one knob step (ENC3 +1, written as the Knobster plugin writes it)
#      before any press: BARO must not move;
#   2. "AD_PRESS 1" to /dev/tessera, the firmware's own synthetic press of
#      key 1 (BARO), from a second writer as press_probe.py (AER-6622) does;
#   3. five steps +1 then five steps -1: BARO must follow each one, 0.01
#      inHg a step, and end where it started;
#   4. no step for longer than the 10 s timeout, then one more step: BARO
#      must not move.
# Prints BARO after each step.  Frames are taken by the shell wrapper
# (run_focus_probe.sh) at the marks this script prints.
import os, socket, sys, time

s = socket.create_connection(("127.0.0.1", 3490), 5)
f = s.makefile("rb")


def ask(cmd, prefix):
    s.sendall(cmd.encode() + b"\n")
    while True:
        line = f.readline().decode().strip()
        if line.startswith(prefix):
            return line


def baro():
    return float(ask("@rBARO", "@rBARO;").split(";")[1])


def step(n):
    # A raw netfix write, as the Knobster plugin's db_write lands on the bus.
    s.sendall("ENC3;{};0\n".format(n).encode())
    time.sleep(0.4)
    return baro()


def mark(name):
    print("MARK", name, time.strftime("%H:%M:%S"), flush=True)
    time.sleep(1.2)  # the wrapper grabs a frame here


ok = True
start = baro()
print("BARO at start", start, flush=True)
mark("before")

after = step(1)
print("before the press, ENC3 +1: BARO", after, "(unchanged)" if abs(after - start) < 0.001 else "MOVED")
ok &= abs(after - start) < 0.001

port = os.open("/dev/tessera", os.O_WRONLY | os.O_NOCTTY)
os.write(port, b"AD_PRESS 1\n")
os.close(port)
time.sleep(0.6)
mark("focused")

want = start
for n in (1, 1, 1, 1, 1):
    want = round(want + 0.01 * n, 2)
    got = step(n)
    print("ENC3 %+d: BARO %s, want %s" % (n, got, want))
    ok &= abs(got - want) < 0.001
mark("turned")
for n in (-1, -1, -1, -1, -1):
    want = round(want + 0.01 * n, 2)
    got = step(n)
    print("ENC3 %+d: BARO %s, want %s" % (n, got, want))
    ok &= abs(got - want) < 0.001

time.sleep(11.0)
mark("timed_out")
got = step(1)
print("after the timeout, ENC3 +1: BARO", got, "(unchanged)" if abs(got - start) < 0.001 else "MOVED")
ok &= abs(got - start) < 0.001
print("BARO at end", baro(), "start", start)
print("RESULT", "PASS" if ok else "FAIL")
s.close()
sys.exit(0 if ok else 1)
