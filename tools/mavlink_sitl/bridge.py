"""pty <-> SITL TCP bridge that records the autopilot's side as a tlog.

usage: bridge.py <tcp port> <link path> <tlog path> <control fifo dir> [<plugin tlog>]

The mavlink plugin opens <link path> (a symlink to the pty slave) as its
serial port. Bytes from SITL are parsed with pymavlink and each message is
written to the tlog as an 8-byte big-endian usec timestamp plus its exact
wire bytes (the pymavlink tlog format). Touch <dir>/pause to stop relaying
in both directions (a link loss), remove it to resume. With a fifth
argument, what the plugin sends is recorded there the same way.
"""
import os
import pty
import select
import socket
import struct
import sys
import time
import tty

from pymavlink import mavutil

port, link, tlog_path, ctl = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
master, slave = pty.openpty()
tty.setraw(slave)
# The slave stays open here for the bridge's lifetime, so the plugin's
# open/close never hangs up the pty.
if os.path.lexists(link):
    os.remove(link)
os.symlink(os.ttyname(slave), link)

sock = socket.create_connection(("127.0.0.1", port))
mav = mavutil.mavlink.MAVLink(None)
mav.robust_parsing = True
tlog = open(tlog_path, "wb")
gcs = mavutil.mavlink.MAVLink(None)
gcs.robust_parsing = True
gcs_tlog = open(sys.argv[5], "wb") if len(sys.argv) > 5 else None
pause = os.path.join(ctl, "pause")
paused = False
print("bridge up:", os.ttyname(slave), "->", port, flush=True)
while True:
    r, _, _ = select.select([master, sock], [], [], 0.1)
    now_paused = os.path.exists(pause)
    if now_paused != paused:
        paused = now_paused
        print(time.time(), "link", "PAUSED" if paused else "RESUMED", flush=True)
    for fd in r:
        if fd is sock:
            data = sock.recv(65536)
            if not data:
                sys.exit("SITL closed")
            if paused:
                continue
            os.write(master, data)
            for m in mav.parse_buffer(data) or []:
                tlog.write(struct.pack(">Q", int(time.time() * 1e6)) + m.get_msgbuf())
            tlog.flush()
        else:
            data = os.read(master, 65536)
            if not paused:
                sock.sendall(data)
                if gcs_tlog:
                    for m in gcs.parse_buffer(data) or []:
                        gcs_tlog.write(struct.pack(">Q", int(time.time() * 1e6))
                                       + m.get_msgbuf())
                    gcs_tlog.flush()
