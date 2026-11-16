# Listen for GDL90 on UDP 4000 for N seconds; count message ids and save every 0x07 (hex) to a file.
import socket, sys, time, collections
secs = float(sys.argv[1]); out = sys.argv[2]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("", 4000)); s.settimeout(0.5)
FLAG, ESC = 0x7E, 0x7D
ids = collections.Counter(); dgrams = 0; senders = set(); ups = []
end = time.monotonic() + secs
while time.monotonic() < end:
    try:
        d, a = s.recvfrom(8192)
    except socket.timeout:
        continue
    dgrams += 1; senders.add(a[0])
    for raw in bytes(d).split(bytes([FLAG]))[1:-1]:
        clear = bytearray(); esc = False
        for b in raw:
            if esc: clear.append(b ^ 0x20); esc = False
            elif b == ESC: esc = True
            else: clear.append(b)
        if len(clear) < 3: continue
        ids["0x%02X" % clear[0]] += 1
        if clear[0] == 0x07: ups.append("%.3f %s" % (time.time(), clear[:-2].hex()))
open(out, "w").write("\n".join(ups) + ("\n" if ups else ""))
print("utc_start_end", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - secs)), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
print("datagrams", dgrams, "senders", sorted(senders)); print("message ids", dict(ids)); print("uplinks (0x07)", len(ups))
