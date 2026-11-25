"""
CAS TEST on a real server (CAS2b, AER-6497; CAS1 brief C2).

The shipped config tree through ``fixgw.server:main``, netfix on a private
port, and a display played by a raw netfix client that connects late. On the
CAS3 bench pyEfis first drew about 6 s after it connected, and 11 s after a
gateway restart under it (AER-6346), and the old fixed 5 s CAS TEST had
cleared every time. Here the client connects 15 s after the plugin started,
and again 12 s after a full gateway restart, re-sending its old CASACK as a
reconnecting display might: both times CAS TEST must still be listed,
unacknowledged, and the client's acknowledge must take it off.

The plugin's start is read from the bus, not assumed: CASSEQ is seeded from
whole seconds since ``cas.SEQ_EPOCH`` at setup and is one above that seed
after the first publish, while nothing else in this config changes the list.
"""
import os
import shutil
import socket
import subprocess
import sys
import time

import pytest

from fixgw.plugins import cas

LATE = 15.0  # the DoD's display connect, seconds after plugin start
RESTART_LATE = 12.0  # past the 11 s re-read measured on the bench


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Netfix:
    def __init__(self, port, limit=20.0):
        deadline = time.monotonic() + limit
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port),
                                                     timeout=2)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        self.buf = b""

    def write(self, key, value):
        self.sock.sendall("{};{}\n".format(key, value).encode())

    def read(self, key):
        self.sock.sendall("@r{}\n".format(key).encode())
        prefix = "@r{};".format(key).encode()
        while True:
            while b"\n" not in self.buf:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("gateway closed the connection")
                self.buf += chunk
            line, self.buf = self.buf.split(b"\n", 1)
            if line.startswith(prefix):
                return line[len(prefix):].decode().split(";")[0]

    def lines(self):
        return [v for v in (self.read("CASMSG{}".format(n))
                            for n in range(1, cas.SLOTS + 1)) if v]

    def close(self):
        self.sock.close()


class Gateway:
    def __init__(self, cfgdir, port, logpath):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [os.path.abspath("src")]
            + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        self.port = port
        self.log = open(logpath, "a")
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from fixgw.server import main; main()",
             "--config-file", os.path.join(cfgdir, "default.yaml")],
            stdout=self.log, stderr=subprocess.STDOUT, env=env)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)
        self.log.close()


@pytest.fixture
def config(tmp_path):
    """The shipped tree with netfix on a free port and only what CAS needs:
    no demo, flight plan or state file, so nothing else writes the bus."""
    cfgdir = tmp_path / "config"
    shutil.copytree("src/fixgw/config", cfgdir)
    port = free_port()
    p = cfgdir / "connections/netfix.yaml"
    s = p.read_text()
    assert s.count("port: 3490") == 1
    p.write_text(s.replace("port: 3490", "port: {}".format(port)))
    p = cfgdir / "preferences.yaml"
    s = p.read_text()
    for name in ("DEMO", "FLIGHTPLAN", "STATE_PERSIST"):
        assert s.count("  {}: true\n".format(name)) == 1, name
        s = s.replace("  {}: true\n".format(name), "  {}: false\n".format(name))
    assert "  CAS: true\n" in s and "  NETFIX: true\n" in s
    p.write_text(s)
    return str(cfgdir), port, str(tmp_path / "gateway.log")


def plugin_age(nf):
    """Seconds since the cas plugin's setup, at least: CASSEQ - 1 is the
    seed, truncated to the whole second, so the true age is no smaller."""
    seq = int(nf.read("CASSEQ"))
    return time.time() - (cas.SEQ_EPOCH + seq - 1) - 1.0, seq


def wait_gone(nf, limit=3.0):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if not any("CASTEST" in v for v in nf.lines()):
            return True
        time.sleep(0.1)
    return False


def test_a_display_connecting_late_still_reads_cas_test(config):
    cfgdir, port, logpath = config
    gw = Gateway(cfgdir, port, logpath)
    try:
        # The display connects LATE s after the plugin started. The gateway
        # takes a moment to come up, so wait from launch, then confirm the
        # age from the bus.
        time.sleep(LATE + 3.5)
        nf = Netfix(port)
        age, seq = plugin_age(nf)
        assert age >= LATE, (age, seq)
        assert nf.lines() == ["1|0|CASTEST|CAS TEST|"]
        assert nf.read("CASTOP") == "1"
        # The pilot's touch: the display writes the CASSEQ it drew.
        nf.write("CASACK", seq)
        assert wait_gone(nf)
        assert nf.read("CASCNT") == "0"
        nf.close()

        # A full gateway restart under a running display, which reconnects
        # after its re-read and re-sends the CASACK it last wrote.
        gw.stop()
        gw = Gateway(cfgdir, port, logpath)
        time.sleep(RESTART_LATE + 3.5)
        nf = Netfix(port)
        age, seq2 = plugin_age(nf)
        assert age >= RESTART_LATE, (age, seq2)
        assert seq2 > seq
        nf.write("CASACK", seq)  # the stale value, below this run's
        time.sleep(1.0)
        assert nf.lines() == ["1|0|CASTEST|CAS TEST|"]
        nf.write("CASACK", seq2)
        assert wait_gone(nf)
        nf.close()
    finally:
        gw.stop()
    with open(logpath) as f:
        log = f.read()
    assert "CAS evaluation failed" not in log
    assert "test_time" not in log  # the shipped file carries no old option
