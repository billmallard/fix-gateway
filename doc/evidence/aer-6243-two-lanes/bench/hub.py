#!/usr/bin/env python3
"""A two-lane CAN-FIX bus made of ptys, for the AER-6243 bench run.

The same hub as tests/integration/test_two_lanes.py, as a daemon.  Each
gateway runs python-can's serial interface on its own pty; every frame from
one lane is forwarded to the other while the file RUN/CUT does not exist.
Writes the pty paths to RUN/hub.json and a frame count per lane, once a
second, to RUN/hub.csv.

    python3 hub.py RUN_DIR
"""
import json
import os
import select
import struct
import sys
import time
import tty

run = sys.argv[1]
masters, slaves, paths = [], [], []
for _ in range(2):
    m, s = os.openpty()
    tty.setraw(s)
    os.set_blocking(m, False)
    masters.append(m)
    slaves.append(s)  # held open, so a lane that dies does not close the pty
    paths.append(os.ttyname(s))
with open(os.path.join(run, "hub.json"), "w") as f:
    json.dump({"paths": paths, "pid": os.getpid()}, f)

bufs = [bytearray(), bytearray()]
counts = [0, 0]
dropped = 0
cut = os.path.join(run, "CUT")
log = open(os.path.join(run, "hub.csv"), "a")
log.write("wall,from_lane1,from_lane2,forwarded_link,dropped\n")
next_log = time.time() + 1


def split(buf):
    out = []
    while True:
        start = buf.find(b"\xaa")
        if start < 0:
            buf.clear()
            return out
        del buf[:start]
        if len(buf) < 6:
            return out
        if buf[5] > 8:
            del buf[:1]
            continue
        total = 11 + buf[5]
        if len(buf) < total:
            return out
        if buf[total - 1] != 0xBB:
            del buf[:1]
            continue
        out.append(bytes(buf[:total]))
        del buf[:total]


while True:
    ready, _, _ = select.select(masters, [], [], 0.1)
    link = not os.path.exists(cut)
    for m in ready:
        lane = masters.index(m)
        try:
            bufs[lane] += os.read(m, 4096)
        except OSError:
            continue
        for fr in split(bufs[lane]):
            counts[lane] += 1
            if not link:
                dropped += 1
                continue
            try:
                os.write(masters[1 - lane], fr)
            except OSError:
                dropped += 1
    if time.time() >= next_log:
        log.write(f"{time.time():.3f},{counts[0]},{counts[1]},{int(link)},{dropped}\n")
        log.flush()
        next_log += 1
