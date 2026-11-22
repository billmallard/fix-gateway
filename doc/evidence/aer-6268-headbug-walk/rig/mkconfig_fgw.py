"""Build the AER-6268 private fixgw (copied from AER-6052) config tree.

    python3 mkconfig.py <branch src/fixgw/config> <live xplane.yaml> <out>
                        <xplane ip> <udp_in> <netfix port> <raw_log path>

The branch's shipped tree (database and plugins match the code under test),
with the bench's live xplane.yaml (the same RREF block as the live glass),
on its own udp_in, the autopilot adapter on, netfix on its own loopback port,
and only NETFIX, SYSTEM and XPLANE enabled. Nothing here touches the live
gateway's config tree."""
import os, shutil, sys
src, live, out, ip, udp_in, port, raw = sys.argv[1:8]
shutil.rmtree(out, ignore_errors=True)
shutil.copytree(src, out)
def sub(p, old, new):
    s = open(p).read()
    assert s.count(old) == 1, (p, old)
    open(p, "w").write(s.replace(old, new))
x = open(live).read()
assert "autopilot:" not in x.split("datarefs:")[0]
lines = x.splitlines(True)
ip_line = [l for l in lines if l.strip().startswith("ipaddress:")]
assert len(ip_line) == 1
x = x.replace(ip_line[0], "    ipaddress: %s\n" % ip)
assert x.count("udp_in:  49001") == 1
x = x.replace("udp_in:  49001", "udp_in:  %s" % udp_in)
x = x.rstrip("\n") + "\n\n    # AER-6268 private instance: the autopilot adapter under test.\n" \
    "    autopilot:\n        enabled: true\n        raw_log: %s\n" % raw
open(os.path.join(out, "connections/xplane.yaml"), "w").write(x)
sub(os.path.join(out, "connections/netfix.yaml"), "port: 3490", "port: %s" % port)
open(os.path.join(out, "preferences.yaml.custom"), "w").write(
    "enabled:\n  XPLANE: true\n  DEMO: false\n  COMPUTE: false\n"
    "  FLIGHTPLAN: false\n  STATE_PERSIST: false\n  ANNUNCIATE: false\n")
# Validate the way fixgw loads it (a yaml.safe_load is not enough, AER-5669).
sys.path.insert(0, os.path.join(src, "..", ".."))
import fixgw.cfg as cfg
c = cfg.from_yaml(os.path.join(out, "connections/xplane.yaml"))
c = c[0] if isinstance(c, tuple) else c
a = c["xplane"]
assert a["autopilot"] == {"enabled": True, "raw_log": raw}, a.get("autopilot")
assert str(a["udp_in"]) == str(udp_in) and a["ipaddress"] == ip
print("config ok:", out, "udp_in", a["udp_in"], "ip", a["ipaddress"],
      "datarefs", len(a["datarefs"]), "hz", a.get("dataref_hz"))
