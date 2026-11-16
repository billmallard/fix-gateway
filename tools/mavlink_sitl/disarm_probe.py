"""Does a disarmed ArduPlane still drive its control surfaces?

ArduPlane 4.7.2 SITL, instance 8, on the ground and disarmed. For each mode,
hold the roll stick (RC1) full right by RC override, then centre it, and
record the aileron (SERVO1) and throttle (SERVO3) outputs.
usage: disarm_probe.py <out dir>
"""
import os
import subprocess
import sys
import time

from pymavlink import mavutil

HERE = os.path.abspath(os.path.dirname(__file__))
OUT = os.path.abspath(sys.argv[1])
os.makedirs(OUT, exist_ok=True)
sitl = subprocess.Popen(
    [os.path.join(HERE, "arduplane"), "-w", "-M", "plane", "-O",
     "34.4258,-119.8403,3,250", "--defaults", os.path.join(HERE, "plane.parm"),
     "-I", "8"], cwd=OUT, stdout=open(OUT + "/sitl.log", "w"),
    stderr=subprocess.STDOUT, start_new_session=True)
try:
    time.sleep(2)
    m = mavutil.mavlink_connection("tcp:127.0.0.1:5840", source_system=255)
    m.wait_heartbeat(timeout=60)
    m.mav.request_data_stream_send(m.target_system, m.target_component,
                                   mavutil.mavlink.MAV_DATA_STREAM_ALL, 10, 1)
    time.sleep(15)      # let the EKF settle

    def servo_for(seconds, ail):
        vals, armed, mode = [], None, None
        t0 = time.time()
        while time.time() - t0 < seconds:
            m.mav.rc_channels_override_send(m.target_system, m.target_component,
                                            ail, 0, 0, 0, 0, 0, 0, 0)
            msg = m.recv_match(type=["SERVO_OUTPUT_RAW", "HEARTBEAT"],
                               blocking=True, timeout=0.2)
            if msg is None:
                continue
            if msg.get_type() == "SERVO_OUTPUT_RAW":
                vals.append((msg.servo1_raw, msg.servo3_raw))
            elif msg.get_srcSystem() == m.target_system:
                armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                mode = mavutil.mode_mapping_apm.get(msg.custom_mode)
        return vals[-1] if vals else None, armed, mode

    for mode in ("MANUAL", "FBWA", "CRUISE"):
        m.set_mode({v: k for k, v in mavutil.mode_mapping_apm.items()}[mode])
        time.sleep(2)
        right = servo_for(3, 2000)
        centre = servo_for(3, 1500)
        print("%-7s stick right: (SERVO1, SERVO3)=%s armed=%s mode=%s | centred: %s"
              % (mode, right[0], right[1], right[2], centre[0]), flush=True)
finally:
    os.killpg(sitl.pid, 9)
