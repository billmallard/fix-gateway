"""Print the plugin's mode commands (<out>/gcs.tlog), ArduPlane's acks to
them and its mode changes (<out>/fc.tlog), merged on the bridge's clock.

usage: trace.py <out dir>
"""
import sys
from pymavlink import mavutil
d = sys.argv[1]
ev = []
g = mavutil.mavlink_connection(d + "/gcs.tlog")
while (m := g.recv_match(type=["COMMAND_INT", "COMMAND_LONG"])) is not None:
    if m.command == 192:
        ev.append((m._timestamp, "plugin  DO_REPOSITION change_mode=%d lat=%.4f" % (m.param2, m.x / 1e7)))
    elif m.command == 176:
        ev.append((m._timestamp, "plugin  DO_SET_MODE %s" % mavutil.mode_mapping_apm.get(int(m.param2))))
f = mavutil.mavlink_connection(d + "/fc.tlog")
last = None
while (m := f.recv_match(type=["COMMAND_ACK", "HEARTBEAT"])) is not None:
    if m.get_type() == "COMMAND_ACK" and m.command in (192, 176):
        ev.append((m._timestamp, "ardupl  ACK %s %s" % ({192: "DO_REPOSITION", 176: "DO_SET_MODE"}[m.command],
                   mavutil.mavlink.enums["MAV_RESULT"][m.result].name)))
    elif m.get_type() == "HEARTBEAT" and m.type == 1 and m.get_srcSystem() == 1:
        n = mavutil.mode_mapping_apm.get(m.custom_mode)
        if n != last:
            ev.append((m._timestamp, "ardupl  heartbeat %s" % n))
            last = n
for t, s in sorted(ev):
    print("%.3f %s" % (t, s))
