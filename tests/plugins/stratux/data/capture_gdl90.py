#!/usr/bin/env python3
"""Capture raw GDL90 datagrams and the Stratux's own JSON side by side.

Run on the Pi 5 while the stratux plugin is OFF (it owns UDP 4000 otherwise):
    python3 capture_gdl90.py --seconds 60 --stratux 10.110.10.169 --out cap
Writes cap.gdl90.jsonl (one datagram per line: monotonic time, sender, hex)
and cap.situation.jsonl (/getSituation once a second, the web UI's source).
"""
import argparse
import json
import socket
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--seconds", type=float, default=60)
ap.add_argument("--port", type=int, default=4000)
ap.add_argument("--stratux", default="10.110.10.169")
ap.add_argument("--out", default="cap")
a = ap.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("", a.port))
s.settimeout(0.2)
end = time.monotonic() + a.seconds
next_poll = 0.0
n = 0
with open(a.out + ".gdl90.jsonl", "w") as g, open(a.out + ".situation.jsonl", "w") as j:
    while time.monotonic() < end:
        now = time.monotonic()
        if now >= next_poll:
            next_poll = now + 1.0
            try:
                url = "http://%s/getSituation" % a.stratux
                body = urllib.request.urlopen(url, timeout=0.8).read()
                j.write(json.dumps({"t": now, "situation": json.loads(body)}) + "\n")
            except Exception as e:  # keep capturing GDL90 regardless
                j.write(json.dumps({"t": now, "error": str(e)}) + "\n")
        try:
            d, adr = s.recvfrom(9000)
        except socket.timeout:
            continue
        g.write(json.dumps({"t": time.monotonic(), "from": adr[0], "hex": d.hex()}) + "\n")
        n += 1
print("captured %d datagrams" % n)
