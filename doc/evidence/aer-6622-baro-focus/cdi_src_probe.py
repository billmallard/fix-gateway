# AER-6622: the CDI SRC key cycles NAVSRC, and the page names each value.
#
# On the Beelink, with fix-gateway on aer-6622/baro-focus-key: "AD_PRESS 9"
# to /dev/tessera (the firmware's own press of key 9) three times, reading
# NAVSRC after each.  pyEfis's cdi-source button wraps NAVSRC through 0, 1
# and 2 (VLOC1, VLOC2, GPS), so three presses must give three different
# values and end where they started.  The name the page shows for each value is printed
# from the page file the plugin loads; the panel's glass is Bill's eye.
import os, socket, sys, time

import yaml

page = yaml.safe_load(open(os.path.expanduser(
    "~/makerplane/fixgw/config/tessera/page.yaml")))
enum = next(k for k in page["keys"] if k["id"] == 9)["enum"]

s = socket.create_connection(("127.0.0.1", 3490), 5)
f = s.makefile("rb")


def navsrc():
    s.sendall(b"@rNAVSRC\n")
    while True:
        line = f.readline().decode().strip()
        if line.startswith("@rNAVSRC;"):
            return float(line.split(";")[1])


start = navsrc()
print("NAVSRC at start", start, enum.get(int(round(start))))
seen = [start]
for _ in range(3):
    port = os.open("/dev/tessera", os.O_WRONLY | os.O_NOCTTY)
    os.write(port, b"AD_PRESS 9\n")
    os.close(port)
    time.sleep(1.5)
    v = navsrc()
    seen.append(v)
    print("press: NAVSRC", v, "panel shows", enum.get(int(round(v))))
ok = len(set(seen[:3])) == 3 and seen[3] == start
print("RESULT", "PASS" if ok else "FAIL")
s.close()
sys.exit(0 if ok else 1)
