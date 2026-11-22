#!/usr/bin/env python3
"""Build the two private lanes for the AER-6243 bench run, from the branch's
shipped two-lane configs.  Touches nothing live.

Each lane: the branch's two_lane_<n>.yaml, with
  - netfix on 127.0.0.1, port 3497 (lane 1) or 3498 (lane 2);
  - canfix on python-can's serial interface, on the hub's pty for that lane;
  - its own X-Plane feed over RREF on its own socket (udp_in 49021 / 49022),
    datarefs only: no recv or send rows, no dataref writes, nothing written
    to the sim;
and a pyEfis config copied from the bench's live one, pointed at that port,
with SVS off (two GL panes on one box would load the bench for nothing; the
attitude indicator draws without it).

    python3 build.py SCRATCH_DIR              both lanes, on the one-box hub
    python3 build.py SCRATCH_DIR 1 --no-efis  one lane, on this host's bridge
                                              (two-box run, bridge.py)
"""
import json
import os
import shutil
import sys

import yaml

S = sys.argv[1]
LANES = [int(a) for a in sys.argv[2:] if a.isdigit()] or [1, 2]
EFIS = "--no-efis" not in sys.argv
SRC = os.path.join(S, "fgw", "src", "fixgw", "config")
if len(LANES) == 1 and os.path.exists(os.path.join(S, "run", "bridge.json")):
    with open(os.path.join(S, "run", "bridge.json")) as f:
        PTY = {LANES[0]: json.load(f)["path"]}
else:
    with open(os.path.join(S, "run", "hub.json")) as f:
        PTY = dict(enumerate(json.load(f)["paths"], start=1))
LIVE_EFIS = os.path.expanduser("~/makerplane/pyefis/config")

XPLANE = {
    "load": True,
    "module": "fixgw.plugins.xplane",
    "ipaddress": "10.110.10.167",
    "udp_out": 49000,
    "send_interval": 0.1,
    "recv": {},
    "send": {},
    "datarefs": {
        "PITCH": "sim/flightmodel/position/theta",
        "ROLL": "sim/flightmodel/position/phi",
        "IAS": "sim/flightmodel/position/indicated_airspeed",
        "ALT": ["sim/flightmodel/position/elevation", 3.28084],
        "TAS": ["sim/flightmodel/position/true_airspeed", 1.943844],
        "VS": "sim/flightmodel/position/vh_ind_fpm",
        "HEAD": "sim/flightmodel/position/mag_psi",
        "ALAT": "sim/flightmodel/forces/g_side",
    },
    "dataref_hz": 10,
}

for n, port, udp in ((1, 3497, 49021), (2, 3498, 49022)):
    if n not in LANES:
        continue
    d = os.path.join(S, f"lane{n}")
    shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(SRC, d)
    with open(os.path.join(d, f"two_lane_{n}.yaml")) as f:
        cfg = yaml.safe_load(f)
    c = cfg["connections"]
    c["netfix"]["host"] = "127.0.0.1"
    c["netfix"]["port"] = port
    c["canfix"]["interface"] = "serial"
    c["canfix"]["channel"] = PTY[n]
    c["xplane"] = dict(XPLANE, udp_in=udp)
    with open(os.path.join(d, "lane.yaml"), "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)

    if not EFIS:
        print("built lane", n, "port", port, "pty", PTY[n])
        continue
    e = os.path.join(S, f"efis{n}")
    shutil.rmtree(e, ignore_errors=True)
    shutil.copytree(LIVE_EFIS, e)
    p = os.path.join(e, "main", "default.yaml")
    s = open(p).read()
    assert s.count("FixPort: 3490") == 1
    open(p, "w").write(s.replace("FixPort: 3490", f"FixPort: {port}"))
    for name in ("managed.yaml", "managed_PFD.yaml"):
        p = os.path.join(e, "screens", name)
        if os.path.exists(p):
            s = open(p).read()
            open(p, "w").write(s.replace("svs_enabled: true", "svs_enabled: false"))
    print("built lane", n, "port", port, "pty", PTY[n])
