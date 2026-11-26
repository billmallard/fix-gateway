# AER-6630 (TESS2b): a Tessera press gives pyEfis's knob to the heading bug.
#
# On the Beelink bench, with pyEfis and fix-gateway on the AER-6630 branches:
#   1. one knob step (ENC3 +1, written as the Knobster plugin writes it)
#      before any press: HEADBUG must not move;
#   2. "AD_PRESS 4" to /dev/tessera, the firmware's own synthetic press of
#      key 4 (HDG BUG), from a second writer as press_probe.py (AER-6622) does;
#   3. five steps +1 then five steps -1: HEADBUG must follow each one and
#      end where it started;
#   4. no step for longer than the 10 s timeout, then one more step: HEADBUG
#      must not move.
# Prints HEADBUG after each step.  Frames are taken by the shell wrapper
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


def headbug():
    return float(ask("@rHEADBUG", "@rHEADBUG;").split(";")[1])


def step(n):
    # A raw netfix write, as the Knobster plugin's db_write lands on the bus.
    s.sendall("ENC3;{};0\n".format(n).encode())
    time.sleep(0.4)
    return headbug()


def mark(name):
    print("MARK", name, time.strftime("%H:%M:%S"), flush=True)
    time.sleep(1.2)  # the wrapper grabs a frame here


ok = True
start = headbug()
print("HEADBUG at start", start, flush=True)
mark("before")

after = step(1)
print("before the press, ENC3 +1: HEADBUG", after, "(unchanged)" if after == start else "MOVED")
ok &= after == start

port = os.open("/dev/tessera", os.O_WRONLY | os.O_NOCTTY)
os.write(port, b"AD_PRESS 4\n")
os.close(port)
time.sleep(0.6)
mark("focused")

want = start
for n in (1, 1, 1, 1, 1):
    want = (want + n) % 360
    got = step(n)
    print("ENC3 %+d: HEADBUG %s, want %s" % (n, got, want))
    ok &= abs(got - want) < 0.01
mark("turned")
for n in (-1, -1, -1, -1, -1):
    want = (want + n) % 360
    got = step(n)
    print("ENC3 %+d: HEADBUG %s, want %s" % (n, got, want))
    ok &= abs(got - want) < 0.01

time.sleep(11.0)
mark("timed_out")
got = step(1)
print("after the timeout, ENC3 +1: HEADBUG", got, "(unchanged)" if got == start else "MOVED")
ok &= got == start
print("HEADBUG at end", headbug(), "start", start)
print("RESULT", "PASS" if ok else "FAIL")
s.close()
sys.exit(0 if ok else 1)
