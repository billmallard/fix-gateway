#!/usr/bin/env python3
"""Record both lanes' keys for the AER-6243 bench run.

Subscribes over netfix to PITCH, ROLL, IAS, ALT and LEADER on each lane.  The
gateway sends a subscribed key on every write and on the write's expiry, so
the arrival time of each line is that key's timestamp.  Writes:

  RUN/events.csv   every line received: wall, lane, key, value, flags (aobfs)
  RUN/seconds.csv  once a second: the phase (RUN/phase), then per lane and key
                   the age of the last write in ms and its flags, and LEADER

A lane whose gateway is dead or stopped reads as empty until it answers
again; one thread per lane, so a stopped lane 1 never holds up lane 2.

    python3 recorder.py RUN_DIR PORT_LANE1 PORT_LANE2
"""
import os
import signal
import socket
import sys
import threading
import time

run, ports = sys.argv[1], {1: int(sys.argv[2]), 2: int(sys.argv[3])}
KEYS = ["PITCH", "ROLL", "IAS", "ALT", "LEADER"]
last = {(lane, k): None for lane in ports for k in KEYS}  # (wall, value, flags)
lock = threading.Lock()
events = open(os.path.join(run, "events.csv"), "a")
events.write("wall,lane,key,value,flags\n")


def follow(lane):
    while True:
        try:
            s = socket.create_connection(("127.0.0.1", ports[lane]), timeout=3)
            s.settimeout(3)
            for k in KEYS:
                s.sendall(f"@s{k}\n@r{k}\n".encode())
            buf = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    raise ConnectionError
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode(errors="replace")
                    if line.startswith("@r"):
                        line = line[2:]
                    elif line.startswith("@"):
                        continue
                    parts = line.split(";")
                    if len(parts) != 3 or parts[0] not in KEYS:
                        continue
                    now = time.time()
                    with lock:
                        last[(lane, parts[0])] = (now, parts[1], parts[2])
                        events.write(f"{now:.3f},{lane},{parts[0]},{parts[1]},{parts[2]}\n")
                        events.flush()
        except (OSError, ConnectionError):
            time.sleep(0.5)


# Stopped with SIGTERM: exit through Python so every buffered line reaches the file.
signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
for lane in ports:
    threading.Thread(target=follow, args=(lane,), daemon=True).start()

sec = open(os.path.join(run, "seconds.csv"), "a")
cols = ["wall", "phase"]
for lane in ports:
    for k in KEYS:
        cols += [f"l{lane}_{k}_age_ms", f"l{lane}_{k}_flags"]
    cols.append(f"l{lane}_LEADER")
sec.write(",".join(cols) + "\n")
next_t = time.time() + 1
while True:
    time.sleep(max(0, next_t - time.time()))
    next_t += 1
    now = time.time()
    try:
        phase = open(os.path.join(run, "phase")).read().strip()
    except OSError:
        phase = ""
    row = [f"{now:.3f}", phase]
    with lock:
        for lane in ports:
            for k in KEYS:
                v = last[(lane, k)]
                row += ["", ""] if v is None else [f"{(now - v[0]) * 1000:.0f}", v[2]]
            v = last[(lane, "LEADER")]
            row.append("" if v is None else v[1])
        events.flush()
    sec.write(",".join(row) + "\n")
    sec.flush()
