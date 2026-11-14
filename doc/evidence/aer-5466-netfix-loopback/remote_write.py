"""CERT14 bench probe: from a second host, write PITCH with its flags omitted
and read back what the gateway holds. Usage: remote_write.py HOST LABEL"""
import socket, sys, time, json

host, label = sys.argv[1], sys.argv[2]
out = {"host": host, "label": label, "t0": time.time(), "events": []}
def ev(kind, **kw):
    kw.update(kind=kind, t=round(time.time() - out["t0"], 3)); out["events"].append(kw); print(kw, flush=True)

def readline(s):
    buf = b""
    while not buf.endswith(b"\n"):
        c = s.recv(1)
        if not c: break
        buf += c
    return buf.decode().strip()

try:
    w = socket.create_connection((host, 3490), timeout=5)
except OSError as e:
    ev("connect_refused", error=repr(e))
    json.dump(out, open(f"{label}.json", "w"), indent=1); sys.exit(0)
ev("connected", local=w.getsockname(), peer=w.getpeername())
sub = socket.create_connection((host, 3490), timeout=5)
sub.sendall(b"@sPITCH\n"); ev("subscribe_ack", line=readline(sub))
w.sendall(b"@rPITCH\n"); ev("read_before", line=readline(w))
for i in range(int(sys.argv[3]) if len(sys.argv) > 3 else 24):
    w.sendall(b"PITCH;15.0\n")          # value sentence, flags omitted
    time.sleep(0.25)
    if i in (0, 4):
        ev("subscriber_saw", line=readline(sub))
w.sendall(b"@rPITCH\n"); ev("read_during", line=readline(w))
w.close(); sub.close()
json.dump(out, open(f"{label}.json", "w"), indent=1)
