"""CAS2 real-server run (AER-6083): the shipped config through
fixgw.server:main, netfix on a private port, driven over raw netfix.

    PYTHONPATH=src python3 doc/evidence/aer-6083-cas/drive.py [PORT]

Copies src/fixgw/config to a temp dir, moves netfix to PORT (default 34983),
lists fuel tank 3 for FUEL QTY 3 NO DATA with a 10 s grace, and starts the
gateway the way the service does (never `-m fixgw.server`, which builds every
plugin twice). Prints one timestamped line per read; exits non-zero if a
check fails.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 34983
T0 = time.monotonic()
FAILS = []


def log(msg):
    print("{:7.2f} {}".format(time.monotonic() - T0, msg), flush=True)


def check(ok, what):
    log(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


class Netfix:
    def __init__(self, port):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=2)
        self.buf = b""
        self.lock = threading.Lock()

    def line(self, prefix):
        while True:
            while b"\n" in self.buf:
                ln, self.buf = self.buf.split(b"\n", 1)
                ln = ln.decode()
                if ln.startswith(prefix):
                    return ln
            chunk = self.s.recv(4096)
            if not chunk:
                raise EOFError("netfix closed")
            self.buf += chunk

    def ask(self, cmd, prefix):
        with self.lock:
            self.s.sendall((cmd + "\n").encode())
            return self.line(prefix)

    def read(self, key):
        ln = self.ask("@r" + key, "@r" + key)
        if ln.endswith("!001"):
            return None, None
        _k, value, flags = ln[2:].split(";")
        return value, flags

    def send(self, frame):
        with self.lock:
            self.s.sendall((frame + "\n").encode())


def lines(nf):
    out = []
    for n in range(1, 13):
        v, _f = nf.read("CASMSG{}".format(n))
        if v:
            out.append(v)
    return out


def wait_for(nf, pred, timeout, what):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        got = lines(nf)
        if pred(got):
            log("{} after {:.2f} s: {}".format(what, time.monotonic() - t, got))
            return time.monotonic() - t
        time.sleep(0.05)
    log("{} NOT within {} s: {}".format(what, timeout, lines(nf)))
    return None


def main():
    tmp = tempfile.mkdtemp(prefix="cas2-")
    cfgdir = os.path.join(tmp, "config")
    shutil.copytree("src/fixgw/config", cfgdir)
    p = os.path.join(cfgdir, "connections/netfix.yaml")
    s = open(p).read()
    assert s.count("port: 3490") == 1
    open(p, "w").write(s.replace("port: 3490", "port: {}".format(PORT)))
    p = os.path.join(cfgdir, "connections/cas.yaml")
    s = open(p).read()
    s = s.replace("  #fuel_no_data_tanks: [1, 2]\n  fuel_no_data_grace: 30",
                  "  fuel_no_data_tanks: [3]\n  fuel_no_data_grace: 10")
    assert "fuel_no_data_tanks: [3]" in s
    open(p, "w").write(s)

    env = dict(os.environ, PYTHONPATH=os.path.abspath("src"))
    errlog = open(os.path.join(tmp, "gateway.log"), "w")
    gw = subprocess.Popen(
        [sys.executable, "-c", "from fixgw.server import main; main()",
         "--config-file", os.path.join(cfgdir, "default.yaml")],
        stdout=errlog, stderr=subprocess.STDOUT, env=env)
    log("gateway pid {} config {}".format(gw.pid, cfgdir))
    try:
        nf = None
        for _ in range(100):
            try:
                nf = Netfix(PORT)
                break
            except OSError:
                time.sleep(0.1)
        assert nf, "netfix never came up"
        log("connected")

        # 1. The keys exist, and CAS TEST shows at start.
        for k in ["CASCNT", "CASTOP", "CASSEQ", "CASACK", "CASACKBTN",
                  "NAVFAIL", "CASMSG1", "CASMSG12"]:
            log("@r{} -> {}".format(k, nf.ask("@r" + k, "@r" + k)))
        rep = nf.ask("@qCASMSG1", "@qCASMSG1")
        log("@qCASMSG1 -> " + rep)
        check(len(rep.split(";")) == 8, "the CASMSG1 report splits into 8 fields")
        wait_for(nf, lambda l: "1|0|CASTEST|CAS TEST|" in l, 3, "CAS TEST shown")
        gone = wait_for(nf, lambda l: not any("CASTEST" in x for x in l), 8,
                        "CAS TEST gone")
        check(gone is not None, "CAS TEST clears after about 5 s")

        # Keep a NAV1 radio and the autopilot alive from feeder threads. Feed
        # the selects' inputs (NAV1DH -> NAV1CDIV -> CDIV), never an output:
        # compute republishes an output from its inputs.
        state = {"nav_fail": "0", "nav": True, "ap": True, "stop": False}

        def feeder():
            while not state["stop"]:
                if state["nav"]:
                    nf.send("NAVSRC;0")
                    nf.send("NAV1CRS;90")
                    nf.send("NAV1CDI;0.1;00{}0".format(state["nav_fail"]))
                    nf.send("NAV1DH;1")
                if state["ap"]:
                    nf.send("APENG;1")
                time.sleep(0.4)

        th = threading.Thread(target=feeder, daemon=True)
        th.start()
        time.sleep(1.5)
        log("NAVFAIL {} CDI {} CDIV {}".format(nf.read("NAVFAIL"), nf.read("CDI"),
                                               nf.read("CDIV")))
        check(nf.read("NAVFAIL")[0] == "False", "NAVFAIL false on a received NAV1")

        # 2. CDI fail -> NAV FAIL amber within 1 s.
        state["nav_fail"] = "1"
        t = wait_for(nf, lambda l: any("|NAVFAIL|NAV FAIL|" in x for x in l), 3,
                     "NAV FAIL shown")
        check(t is not None and t < 1.0, "NAV FAIL within 1 s of CDI fail")
        check(any(x.startswith("2|0|NAVFAIL|") for x in lines(nf)),
              "NAV FAIL is an unacknowledged caution")
        log("NAVFAIL key {}".format(nf.read("NAVFAIL")))

        # 3. Acknowledge by sequence.
        seq = nf.read("CASSEQ")[0]
        nf.send("CASACK;{}".format(seq))
        t = wait_for(nf, lambda l: any(x.startswith("2|1|NAVFAIL|") for x in l), 2,
                     "NAV FAIL acknowledged")
        check(t is not None, "CASACK = CASSEQ acknowledges")
        check(nf.read("CASTOP")[0] == "0", "CASTOP 0 with all acknowledged")

        # 4. Clear the flag -> line gone.
        state["nav_fail"] = "0"
        t = wait_for(nf, lambda l: not any("NAVFAIL" in x for x in l), 3,
                     "NAV FAIL gone")
        check(t is not None, "NAV FAIL clears with the flag")

        # 17. Untuned NAV2: the HSI flag but no NAV FAIL.
        state["nav"] = False
        time.sleep(0.5)
        nf.send("NAVSRC;1")
        time.sleep(1.5)
        log("NAVSRC 1 (NAV2 never received): NAVFAIL {} lines {}".format(
            nf.read("NAVFAIL"), lines(nf)))
        check(nf.read("NAVFAIL")[0] == "True", "NAVFAIL true on an untuned NAV2")
        check(not any("NAVFAIL" in x for x in lines(nf)),
              "no NAV FAIL line for a source never received")

        # 5. AP engaged, then the link stops -> AP LINK LOST warning.
        check(not any("APLINK" in x for x in lines(nf)),
              "no AP LINK LOST while the autopilot is fed")
        state["ap"] = False
        t = wait_for(nf, lambda l: any("|APLINK|AP LINK LOST|" in x for x in l), 5,
                     "AP LINK LOST shown")
        check(t is not None and t < 3.0, "AP LINK LOST within 3 s of the last APENG")
        check(any(x.startswith("3|0|APLINK|") for x in lines(nf)),
              "AP LINK LOST is a warning (engaged)")
        check(nf.read("CASTOP")[0] == "3", "CASTOP 3")
        state["ap"] = True
        held = wait_for(nf, lambda l: not any("APLINK" in x for x in l), 9,
                        "AP LINK LOST gone after the link returned")
        check(held is not None and 4.5 <= held <= 6.5,
              "AP LINK LOST clears about 5 s after the link is steady")

        # 7. FUEL QTY 3 NO DATA after the 10 s grace (tank 3 listed; the
        # demo writes tanks 1 and 2 only).
        t = wait_for(nf, lambda l: "2|0|FUELQ3ND|FUEL QTY 3 NO DATA|" in l, 15,
                     "FUEL QTY 3 NO DATA shown")
        check(t is not None, "FUEL QTY 3 NO DATA after the grace")

        # A CASACK ahead of CASSEQ is ignored.
        seq = int(nf.read("CASSEQ")[0])
        nf.send("CASACK;{}".format(seq + 100))
        time.sleep(0.6)
        check(any(x.startswith("2|0|FUELQ3ND|") for x in lines(nf)),
              "a CASACK ahead of CASSEQ acknowledges nothing")

        # Freshness: the keys are rewritten every second.
        time.sleep(3.5)
        flags = [nf.read(k)[1] for k in ("CASCNT", "CASSEQ", "CASMSG1", "NAVFAIL")]
        log("flags after 3.5 s quiet: {}".format(flags))
        check(all(f[1] == "0" for f in flags), "CAS keys not old while running")

        st = json.loads(nf.ask("@xstatus", "@xstatus")[len("@xstatus;"):])
        cas_status = [v for k, v in st.items() if "cas" in k.lower()] or \
            [v.get("cas") for v in st.values() if isinstance(v, dict) and "cas" in v]
        log("status cas: {}".format(json.dumps(cas_status)))
        log("CASCNT {} CASSEQ {} lines {}".format(nf.read("CASCNT"), nf.read("CASSEQ"),
                                                  lines(nf)))
        state["stop"] = True
        th.join()
    finally:
        gw.terminate()
        try:
            gw.wait(10)
        except subprocess.TimeoutExpired:
            gw.kill()
        errlog.close()
        log("gateway exit {}".format(gw.returncode))
        bad = [l for l in open(os.path.join(tmp, "gateway.log"))
               if "cas" in l.lower() and ("ERROR" in l or "Traceback" in l)]
        log("gateway log lines mentioning cas with ERROR: {}".format(len(bad)))
        for l in open(os.path.join(tmp, "gateway.log")):
            if "did not shutdown" in l:
                log("  " + l.rstrip())
        for l in bad[:10]:
            log("  " + l.rstrip())
    log("RESULT {} checks failed".format(len(FAILS)) if FAILS else "RESULT all checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
