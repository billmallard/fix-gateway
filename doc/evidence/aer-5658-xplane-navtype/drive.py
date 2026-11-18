"""AER-5658: X-Plane tunes NAV1 to a VOR, then an ILS; log X-Plane's raw
nav_type[0] beside the bus NAV1TYPE/NAVTYPE, grab the HSI source tab after
each step, and push CRS on the VOR (control) and on the localizer.

Runs on the Beelink inside the sim-bench session, under the bench lock.
Usage: drive.py <outdir>
"""
import csv
import math
import socket
import struct
import subprocess
import sys
import threading
import time

import fixgw.netfix as netfix

OUT = sys.argv[1]
XP = ("10.110.10.167", 49000)
VOR_HZ, ILS_HZ = 11490, 11030           # RZS 114.90, ISBA 110.30 (CIFP 2611)
ISBA = (34.42730, -119.83027)            # localizer antenna, CIFP
CRS_XY = (691, 672)                      # CRS readout, 1920x1080 glass
RREFS = ["sim/cockpit/radios/nav_type[0]",
         "sim/cockpit2/radios/actuators/nav1_frequency_hz",
         "sim/cockpit/radios/nav1_obs_degm",
         "sim/cockpit2/radios/actuators/HSI_source_select_pilot",
         "sim/cockpit2/radios/indicators/nav1_display_horizontal"]
BUS = ["NAV1TYPE", "NAV2TYPE", "NAVTYPE", "NAVSRC", "NAV1CRS", "VOR1BRG",
       "COURSE", "LAT", "LONG", "GS"]

xp = {}
stop = threading.Event()
ev = open(f"{OUT}/drive.log", "a", buffering=1)


def log(*a):
    line = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " " + " ".join(map(str, a))
    ev.write(line + "\n")
    print(line, flush=True)


# --- X-Plane, directly (independent of fixgw) -------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 0))
sock.settimeout(0.5)


def rref(hz):
    for i, p in enumerate(RREFS):
        sock.sendto(b"RREF\0" + struct.pack("<ii400s", hz, i, p.encode()), XP)


def dref(path, val):
    sock.sendto(b"DREF\0" + struct.pack("<f500s", float(val), path.encode()), XP)
    log("DREF", path, val)


def xp_rx():
    last_sub = 0
    while not stop.is_set():
        if time.time() - last_sub > 5:   # X-Plane may start after us
            rref(10)
            last_sub = time.time()
        try:
            d, _ = sock.recvfrom(2048)
        except socket.timeout:
            continue
        if d[:4] != b"RREF":
            continue
        for k in range(5, len(d) - 7, 8):
            i, v = struct.unpack("<if", d[k:k + 8])
            if 0 <= i < len(RREFS):
                xp[RREFS[i]] = (time.time(), v)
    rref(0)


# --- the bus ------------------------------------------------------------------
c = netfix.Client("127.0.0.1", 3490)
c.connect()
time.sleep(0.5)
crs_changes = []


def on_data(x):
    if x[0] == "NAV1CRS":
        crs_changes.append((time.time(), x[1]))


c.setDataCallback(on_data)
c.subscribe("NAV1CRS")


def bus(k):
    try:
        r = c.read(k)
        return r[1:] if isinstance(r, tuple) else ("ERR%s" % r, "")
    except Exception as e:
        return ("EXC %s" % e, "")


def num(k):
    try:
        return float(bus(k)[0])
    except ValueError:
        return None


def xv(i):
    t, v = xp.get(RREFS[i], (0, None))
    return v if time.time() - t < 2 else None


def recorder():
    with open(f"{OUT}/bus_vs_xplane.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["utc", "xp_nav_type0", "xp_nav1_hz", "xp_nav1_obs", "xp_hsi_src",
                    "xp_nav1_dh"] + [f"{k}{s}" for k in BUS for s in ("", "_flags")])
        while not stop.is_set():
            now = time.time()
            row = ["%s.%d" % (time.strftime("%H:%M:%S", time.gmtime(now)), int(now * 10) % 10)]
            row += [xv(i) for i in range(len(RREFS))]
            for k in BUS:
                row += list(bus(k))
            w.writerow(row)
            f.flush()
            time.sleep(0.5)


def grab(name):
    for n in (1, 2):                      # the first grab after a step can be stale
        time.sleep(1.0)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-draw_mouse", "0",
                        "-video_size", "1920x1080", "-i", ":0", "-frames:v", "1",
                        f"{OUT}/{name}_{n}.png"], check=False)
    log("GRAB", name, {k: bus(k) for k in ("NAVSRC", "NAV1TYPE", "NAVTYPE", "NAV1CRS")},
        "xp nav_type0", xv(0), "nav1_hz", xv(1))


def wait_for(pred, timeout, what):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            log("OK", what, "after %.1f s" % (time.time() - t0))
            return True
        time.sleep(0.5)
    log("TIMEOUT", what, timeout)
    return False


def nm_from_isba():
    try:
        la, lo = float(bus("LAT")[0]), float(bus("LONG")[0])
    except ValueError:
        return 999
    dy = (la - ISBA[0]) * 60
    dx = (lo - ISBA[1]) * 60 * math.cos(math.radians(la))
    return math.hypot(dx, dy)


def tap_crs():
    subprocess.run(["xdotool", "mousemove", str(CRS_XY[0]), str(CRS_XY[1]), "click", "1"],
                   env={"DISPLAY": ":0"}, check=False)
    log("TAP CRS", CRS_XY)


def push(name):
    before = bus("NAV1CRS")
    n0 = len(crs_changes)
    t0 = time.time()
    c.write("BTN3", 1)
    time.sleep(0.3)
    c.write("BTN3", 0)
    log("PUSH BTN3", name)
    time.sleep(3.0)
    seen = [(round(t - t0, 2), v) for t, v in crs_changes[n0:]]
    log("CRS PUSH", name, "NAV1CRS before", before, "after", bus("NAV1CRS"),
        "VOR1BRG", bus("VOR1BRG"), "NAV1CRS changes in 3.3 s", seen)


def main():
    threading.Thread(target=xp_rx, daemon=True).start()
    log("bus BTN3", bus("BTN3"), "NAV1TYPE", bus("NAV1TYPE"), "NAVTYPE", bus("NAVTYPE"))
    if not wait_for(lambda: xv(0) is not None, 600, "X-Plane RREF replies"):
        return
    threading.Thread(target=recorder, daemon=True).start()
    def flying():
        try:
            return bus("LAT")[1] == "" and float(bus("GS")[0]) > 60 and nm_from_isba() < 40
        except ValueError:
            return False

    wait_for(flying, 600, "flying near KSBA (GS > 60 kt, < 40 nm)")
    time.sleep(15)
    orig_src, orig_hz = bus("NAVSRC")[0], xv(1)
    log("ORIG NAVSRC", orig_src, "nav1_hz", orig_hz, "nm from ISBA %.1f" % nm_from_isba())
    try:
        c.write("NAVSRC", 0)
        grab("nav1_default")
        dref(RREFS[1], VOR_HZ)
        wait_for(lambda: xv(0) == 3 and num("NAV1TYPE") == 1, 30, "nav_type 3 and NAV1TYPE 1")
        time.sleep(2)
        grab("nav1_vor")
        # control: on a VOR, the push centres the CDI on the station bearing
        tap_crs()
        grab("vor_crs_focus")
        push("vor")
        grab("vor_crs_push")
        time.sleep(11)                    # let the 10 s edit timeout clear any focus
        dref(RREFS[1], ILS_HZ)
        # Night 2: the localizer was heard for 1 s at 16 nm and 31 deg off,
        # then lost. Require 8 s of steady reception (inside 10 nm on this
        # route), and re-check NAVTYPE right before the push.
        on = {"since": None}

        def steady_loc():
            ok = xv(0) in (4, 5) and num("NAV1TYPE") == 2 and num("NAVTYPE") == 2
            on["since"] = (on["since"] or time.time()) if ok else None
            return ok and time.time() - on["since"] >= 8

        for attempt in (1, 2, 3):
            if not wait_for(steady_loc, 660, "localizer steady 8 s (attempt %d)" % attempt):
                break
            log("nm from ISBA %.1f" % nm_from_isba())
            grab("nav1_loc")
            tap_crs()
            grab("loc_crs_focus")
            if num("NAVTYPE") != 2:
                log("NAVTYPE left 2 before the push; retrying")
                time.sleep(11)
                continue
            push("loc")
            log("NAVTYPE after push", bus("NAVTYPE"), "xp nav_type0", xv(0))
            grab("loc_crs_push")
            break
        time.sleep(5)
    finally:
        if orig_hz is not None:
            dref(RREFS[1], orig_hz)
        c.write("NAVSRC", float(orig_src) if orig_src not in ("", None) else 2)
        time.sleep(3)
        grab("restored")
        log("RESTORED NAVSRC", bus("NAVSRC"), "nav1_hz", xv(1))
        stop.set()
        time.sleep(1)
        c.disconnect()


if __name__ == "__main__":
    main()
