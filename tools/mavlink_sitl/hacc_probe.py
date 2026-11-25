"""ACC2 (AER-6593): what does ArduPlane SITL put in GPS_RAW_INT.h_acc?

Starts the SITL binary (instance 9, TCP 5850), reads the GPS and SIM_GPS
parameters, then logs every GPS_RAW_INT for a period on the ground. It also
sets SIM_GPS1_ACC (if present) to a second value to see whether h_acc follows.
"""
import collections, os, subprocess, sys, time
from pymavlink import mavutil

here = os.path.dirname(os.path.abspath(__file__))
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "out")
os.makedirs(out, exist_ok=True)
sitl = subprocess.Popen(
    [os.path.join(here, "arduplane"), "-I", "9", "--model", "plane",
     "--defaults", os.path.join(here, "plane.parm"), "--home", "29.9,-97.86,180,0",
     "--speedup", "1"],
    cwd=out, stdout=open(os.path.join(out, "sitl.console"), "w"), stderr=subprocess.STDOUT)
try:
    m = None
    for _ in range(60):
        try:
            m = mavutil.mavlink_connection("tcp:127.0.0.1:5850", source_system=255)
            break
        except Exception:
            time.sleep(1)
    m.wait_heartbeat(timeout=30)
    m.mav.request_data_stream_send(m.target_system, m.target_component,
                                   mavutil.mavlink.MAV_DATA_STREAM_ALL, 5, 1)

    def param(name):
        m.mav.param_request_read_send(m.target_system, m.target_component, name.encode(), -1)
        t = time.time()
        while time.time() - t < 3:
            p = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=1)
            if p and p.param_id == name:
                return p.param_value
        return None

    def sample(secs, label):
        c = collections.Counter(); n = 0; t = time.time()
        while time.time() - t < secs:
            g = m.recv_match(type="GPS_RAW_INT", blocking=True, timeout=1)
            if g is None:
                continue
            n += 1
            c[(g.fix_type, g.h_acc, g.v_acc, g.vel_acc, g.eph, g.epv, g.satellites_visible)] += 1
        print(f"{label}: {n} GPS_RAW_INT in {secs} s; (fix_type, h_acc mm, v_acc mm, vel_acc mm/s, eph cm, epv cm, sats): count")
        for k, v in c.most_common(12):
            print("  ", k, v)

    for p in ["GPS1_TYPE", "GPS_TYPE", "SIM_GPS1_TYPE", "SIM_GPS_TYPE", "SIM_GPS1_ACC", "SIM_GPS_ACC",
              "SIM_GPS1_HZ", "SIM_GPS1_NUMSATS", "SIM_GPS_NUMSATS", "SIM_GPS1_JAM", "SIM_GPS1_NOISE", "SIM_GPS_NOISE"]:
        print(f"{p} = {param(p)}")
    sample(40, "default params")
    acc = "SIM_GPS1_ACC" if param("SIM_GPS1_ACC") is not None else "SIM_GPS_ACC"
    m.mav.param_set_send(m.target_system, m.target_component, acc.encode(), 2.0,
                         mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    time.sleep(2)
    print(f"{acc} now {param(acc)}")
    sample(20, f"{acc}=2.0")
finally:
    sitl.terminate(); sitl.wait(10)
