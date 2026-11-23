#!/usr/bin/env python3
"""AER-6386: the full AER-5856 (AFC2b) glass check, unchanged (glass.py),
plus a netfix subscriber logging EVERY update of the seven dial keys to
sub.log, and every xdotool call with its time.

    python3 probe_full.py <dir> --port 3490 --xp <ip> --xp-port 49000 --truth-port 49111
"""
import os, socket, sys, threading, time
D = sys.argv[1]
src = open(os.path.join(D, "glass.py")).read()
head, tail = src.rsplit("\nmain()", 1)
g = {"__name__": "glassmod", "__file__": os.path.join(D, "glass.py")}
exec(compile(head, "glass.py", "exec"), g)
SUB = open(os.path.join(D, "sub.log"), "a", buffering=1)
KEYS = [k[0] for k in g["KEYS"]]

def subscriber():
    while not os.path.exists(g["STOP"]):
        try:
            s = socket.create_connection(("127.0.0.1", g["PORT"]), timeout=5); break
        except OSError:
            time.sleep(1)
    s.sendall("".join("@s%s\n" % k for k in KEYS).encode()); s.settimeout(0.5); buf = b""
    while not os.path.exists(g["STOP"]):
        try:
            c = s.recv(4096)
        except socket.timeout:
            continue
        if not c:
            SUB.write("%.3f CLOSED\n" % time.time()); return
        buf += c
        while b"\n" in buf:
            l, _, buf = buf.partition(b"\n")
            SUB.write("%.3f %s\n" % (time.time(), l.decode()))

_note = g["note"]
def note(step, msg, **kw):
    SUB.write("%.3f NOTE %s %s\n" % (time.time(), step, msg)); _note(step, msg, **kw)
g["note"] = note
_popen = g["subprocess"].Popen
class P(_popen):
    def __init__(self, args, *a, **kw):
        if args and args[0] == "xdotool":
            SUB.write("%.3f XDO %s\n" % (time.time(), " ".join(map(str, args[1:]))))
        super().__init__(args, *a, **kw)
g["subprocess"].Popen = P
threading.Thread(target=subscriber, daemon=True).start()
g["main"]()
