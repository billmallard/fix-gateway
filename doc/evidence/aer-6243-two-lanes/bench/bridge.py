#!/usr/bin/env python3
"""One host's end of a two-box CAN-FIX bus carried over Ethernet (AER-6243).

Makes a pty for this host's gateway (python-can's serial interface), and
sends each complete frame from it to the peer bridge as one UDP datagram.
Datagrams from the peer go into the pty.  While RUN/CUT exists nothing is
forwarded either way.  Writes RUN/bridge.json (the pty path) and a
once-a-second count to RUN/bridge.csv.

    python3 bridge.py RUN_DIR LOCAL_UDP_PORT PEER_HOST PEER_UDP_PORT
"""
import json
import os
import select
import socket
import sys
import time
import tty

run, lport, peer = sys.argv[1], int(sys.argv[2]), (sys.argv[3], int(sys.argv[4]))
m, s = os.openpty()
tty.setraw(s)
os.set_blocking(m, False)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", lport))
with open(os.path.join(run, "bridge.json"), "w") as f:
    json.dump({"path": os.ttyname(s), "pid": os.getpid()}, f)

buf = bytearray()
sent = received = dropped = 0
cut = os.path.join(run, "CUT")
log = open(os.path.join(run, "bridge.csv"), "a")
log.write("wall,sent,received,link,dropped\n")
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
    ready, _, _ = select.select([m, sock], [], [], 0.1)
    link = not os.path.exists(cut)
    if m in ready:
        try:
            buf += os.read(m, 4096)
        except OSError:
            pass
        for fr in split(buf):
            if link:
                sock.sendto(fr, peer)
                sent += 1
            else:
                dropped += 1
    if sock in ready:
        fr, addr = sock.recvfrom(64)
        if addr[0] != peer[0]:
            continue
        if link:
            try:
                os.write(m, fr)
                received += 1
            except OSError:
                dropped += 1
        else:
            dropped += 1
    if time.time() >= next_log:
        log.write(f"{time.time():.3f},{sent},{received},{int(link)},{dropped}\n")
        log.flush()
        next_log += 1
