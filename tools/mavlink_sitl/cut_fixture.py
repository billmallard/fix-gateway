"""Cut a recorded fc.tlog to the autopilot's own fixed-wing heartbeats.

usage: cut_fixture.py <fc.tlog> <out.tlog> [<from> <to>]
<from> and <to> (unix seconds, as session.log prints them) keep only the
heartbeats between them. Each record keeps its 8-byte timestamp and its exact wire bytes, so the
fixture still parses (and checksums) through pymavlink.
"""
import struct
import sys

from pymavlink import mavutil

src = mavutil.mavlink_connection(sys.argv[1], notimestamps=False)
t_from, t_to = (float(sys.argv[3]), float(sys.argv[4])) if len(sys.argv) > 4 \
    else (0.0, float("inf"))
n = 0
modes = []
with open(sys.argv[2], "wb") as out:
    while True:
        m = src.recv_match(type="HEARTBEAT")
        if m is None:
            break
        if (m.get_srcSystem(), m.get_srcComponent()) != (1, 1) or m.type != 1:
            continue
        if not t_from <= m._timestamp <= t_to:
            continue
        out.write(struct.pack(">Q", int(m._timestamp * 1e6)) + m.get_msgbuf())
        n += 1
        name = mavutil.mode_mapping_apm.get(m.custom_mode, m.custom_mode)
        if not modes or modes[-1][0] != name:
            modes.append([name, 0])
        modes[-1][1] += 1
print(n, "heartbeats;", " -> ".join("%s x%d" % tuple(x) for x in modes))
