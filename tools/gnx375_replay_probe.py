#!/usr/bin/env python3
"""GNX 375 NMEA replay over a pty -> private fixgw -> netfix (AER-4473).

Usage: <venv>/bin/python tools/gnx375_replay_probe.py <tree> <label>
  <tree>   a fix-gateway checkout (its src/ is put on PYTHONPATH)
  <label>  name for the output and for the run-<label> directory made in the cwd

Run it with a python that has fixgw's dependencies (pynmea2, pyserial).

Starts a private fixgw from <tree> with the stock database, the stock
connections/compute.yaml and connections/garmin_gnx375.yaml (port pointed at
a pty), and netfix on port 34973. Nothing touches a running fixgw service,
its config or port 3490. The sentences are spec-formed, not a recording from
a unit. It shows where the GNX's guidance lands: EXT* always, COURSE/CDI/
TOFROM/XTRACK only through the GPSSRC and NAVSRC selects.
"""
import os
import pty
import shutil
import socket
import subprocess
import sys
import time

TREE = os.path.abspath(sys.argv[1])
LABEL = sys.argv[2]
RUN = os.path.join(os.getcwd(), "run-" + LABEL)
PORT = 34973
PY = sys.executable


def nmea(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return f"${body}*{c:02X}\r\n".encode()


RMC = nmea("GPRMC,152043.00,A,4000.000,N,08800.000,W,100.0,005.0,311026,003.1,W")
GGA = nmea("GPGGA,152043.00,4000.000,N,08800.000,W,2,09,0.9,300.0,M,-33.0,M,,")
RMB_R25 = nmea("GPRMB,A,2.50,L,ORIG,DEST,4030.000,N,08800.000,W,30.0,000.0,100.0,V")
APB_TO_M = nmea("GPAPB,A,A,2.50,L,N,V,V,087.0,M,DEST,095.0,M,092.0,M")
APB_TO_T = nmea("GPAPB,A,A,2.50,L,N,V,V,090.0,T,DEST,098.0,T,095.0,T")
APB_FROM_M = nmea("GPAPB,A,A,2.50,L,N,A,A,087.0,M,DEST,267.0,M,092.0,M")

KEYS = ["EXTCRS", "EXTCDI", "EXTTF", "EXTXTK", "GPSCRS", "GPSCDI", "GPSTF",
        "COURSE", "CDI", "TOFROM", "XTRACK", "GPS_FIX_TYPE"]


def make_config():
    if os.path.exists(RUN):
        shutil.rmtree(RUN)
    cfg = os.path.join(RUN, "config")
    shutil.copytree(os.path.join(TREE, "src/fixgw/config"), cfg)
    gnx = os.path.join(cfg, "connections/garmin_gnx375.yaml")
    with open(gnx) as f:
        text = f.read()
    assert "port: /dev/ttyUSB0" in text
    with open(gnx, "w") as f:
        f.write(text.replace("port: /dev/ttyUSB0", "port: " + os.path.join(RUN, "gnx.pty")))
    nf = os.path.join(cfg, "connections/netfix.yaml")
    with open(nf) as f:
        text = f.read()
    with open(nf, "w") as f:
        f.write(text.replace("port: 3490", f"port: {PORT}"))
    with open(os.path.join(cfg, "aer4473.yaml"), "w") as f:
        f.write(
            'database file: "{CONFIG}/database.yaml"\n'
            "auto start: true\n"
            "initialization files: []\n"
            "connections:\n"
            "  include:\n"
            "  - NETFIX_CONFIG\n"
            "  - COMPUTE_CONFIG\n"
            "  - GARMIN_GNX375_CONFIG\n"
        )
    with open(os.path.join(cfg, "preferences.yaml.custom"), "w") as f:
        # The stock preferences have no GARMIN_GNX375_CONFIG include; the Pi
        # adds it in its own preferences.yaml.custom, as here.
        f.write("includes:\n  GARMIN_GNX375_CONFIG: connections/garmin_gnx375.yaml\n"
                "enabled:\n  NETFIX: true\n  COMPUTE: true\n  GARMIN_GNX375: true\n")
    return os.path.join(cfg, "aer4473.yaml")


class Netfix:
    def __init__(self):
        for _ in range(100):
            try:
                self.s = socket.create_connection(("127.0.0.1", PORT), timeout=2)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise SystemExit("netfix never came up")
        self.buf = b""

    def _line(self, prefix):
        while True:
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                text = line.decode()
                if text.startswith(prefix):
                    return text
            self.buf += self.s.recv(4096)

    def read(self, key):
        self.s.sendall(f"@r{key}\n".encode())
        text = self._line("@r" + key)
        return text.split(";")[1] if ";" in text else text

    def write(self, key, value):
        self.s.sendall(f"@w{key};{value}\n".encode())
        time.sleep(0.2)


def snapshot(nf, title):
    row = {k: nf.read(k) for k in KEYS}
    print(f"  {title}")
    print("    " + "  ".join(f"{k}={row[k]}" for k in KEYS))
    return row


def main():
    cfgfile = make_config()
    master, slave = pty.openpty()
    os.symlink(os.ttyname(slave), os.path.join(RUN, "gnx.pty"))
    env = dict(os.environ, PYTHONPATH=os.path.join(TREE, "src"))
    log = open(os.path.join(RUN, "fixgw.log"), "w")
    gw = subprocess.Popen([PY, "-m", "fixgw.server", "--config-file", cfgfile],
                          env=env, stdout=log, stderr=subprocess.STDOUT, cwd=RUN)
    try:
        nf = Netfix()
        time.sleep(1.0)
        print(f"== {LABEL}")
        nf.write("NAVSRC", 2)
        nf.write("GPSSRC", 1)

        def send(*sentences, rounds=3):
            for _ in range(rounds):
                for s in sentences:
                    os.write(master, s)
                time.sleep(0.4)

        send(RMC, GGA, RMB_R25, APB_TO_M)
        # A run in which the plugin heard nothing proves nothing: GGA quality
        # 2 with 9 satellites must have reached the bus as GPS_FIX_TYPE 4.
        if nf.read("GPS_FIX_TYPE") != "4":
            raise SystemExit("GNX plugin received nothing (GPS_FIX_TYPE != 4); run proves nothing")
        snapshot(nf, "GPSSRC=1 NAVSRC=2; RMB 2.50 nm steer L; APB DTK 087 M, TO")
        send(RMC, GGA, RMB_R25, APB_TO_T)
        snapshot(nf, "APB DTK 090 T, RMC var 3.1 W (expect 093.1 M)")
        send(RMC, GGA, RMB_R25, APB_FROM_M)
        snapshot(nf, "APB perpendicular passed (expect FROM)")

        nf.write("GPSSRC", 0)
        seen = set()
        t_end = time.time() + 5.0
        while time.time() < t_end:
            for s in (RMC, GGA, RMB_R25, APB_TO_M):
                os.write(master, s)
            time.sleep(0.1)
            seen.add((nf.read("COURSE"), nf.read("CDI")))
        snapshot(nf, "GPSSRC=0 (internal plan) with the GNX still streaming for 5 s")
        print(f"    distinct (COURSE, CDI) sampled during those 5 s: {sorted(seen)}")
    finally:
        gw.terminate()
        try:
            gw.wait(10)
        except subprocess.TimeoutExpired:
            gw.kill()
        log.close()


if __name__ == "__main__":
    main()
