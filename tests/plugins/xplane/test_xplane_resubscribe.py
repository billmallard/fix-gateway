"""RREF subscriptions survive an X-Plane that starts late or restarts.

fix-gateway #51: X-Plane forgets every RREF subscription when it exits, and
one sent while it is not running goes nowhere. The plugin has to keep
re-sending them, or a gateway started before the sim (or a sim restarted
under a running gateway) streams nothing until fix-gateway is restarted.

These tests run the real MainThread over real loopback UDP against a fake
X-Plane that, like the real one, streams only the datarefs it has been asked
for since it started.
"""

import socket
import struct
import threading
import time
from unittest.mock import MagicMock

import pytest

from fixgw.plugins.xplane import MainThread

DATAREF = "sim/cockpit/autopilot/heading_mag"


def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class FakeXPlane:
    """Answers RREF subscriptions the way X-Plane's legacy receiver does:
    each (sender, index) with freq > 0 is streamed back as "RREF," + (index,
    value) pairs until cancelled, and a new instance starts with none."""

    def __init__(self, port, value):
        self.value = value
        self.subs = {}                       # (addr, index) -> dref
        self.requests = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", port))
        self.sock.settimeout(0.02)
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(1024)
            except socket.timeout:
                data = None
            except OSError:
                return
            if data and data[:4] == b"RREF" and len(data) == 413:
                freq, index, dref = struct.unpack("<xxxxxii400s", data)
                self.requests += 1
                key = (addr, index)
                if freq > 0:
                    self.subs[key] = dref.rstrip(b"\x00").decode()
                else:
                    self.subs.pop(key, None)
            for (addr, index), dref in list(self.subs.items()):
                if dref == DATAREF:
                    self.sock.sendto(
                        b"RREF," + struct.pack("<If", index, self.value), addr)

    def stop(self):
        self._stop = True
        self._thread.join(1.0)
        self.sock.close()


@pytest.fixture
def gateway():
    parent = MagicMock()
    parent.config = {
        "ipaddress": "127.0.0.1",
        "udp_in": _free_udp_port(),
        "udp_out": _free_udp_port(),
        "send_interval": 0.05,
        "rref_resubscribe": 0.3,
        "datarefs": {"HEADBUG": DATAREF},
    }
    writes = []
    parent.db_write.side_effect = lambda key, value: writes.append((key, value))
    t = MainThread(parent)
    t.daemon = True
    t.start()
    yield t, parent.config["udp_out"], writes
    t.getout = True
    t.join(2.0)
    t.close()


def _wait_for(writes, value, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ("HEADBUG", pytest.approx(value)) in writes:
            return True
        time.sleep(0.02)
    return False


def test_xplane_started_after_gateway_streams(gateway):
    t, xp_port, writes = gateway
    time.sleep(0.5)                  # gateway's first subscription goes nowhere
    assert writes == []
    xp = FakeXPlane(xp_port, 270.0)
    try:
        assert _wait_for(writes, 270.0, 2.0), \
            "no RREF data from an X-Plane started after the gateway"
    finally:
        xp.stop()


def test_xplane_restart_under_running_gateway_streams_again(gateway):
    t, xp_port, writes = gateway
    xp = FakeXPlane(xp_port, 90.0)
    try:
        assert _wait_for(writes, 90.0, 2.0)
    finally:
        xp.stop()
    time.sleep(0.5)                  # X-Plane down; its subscriptions are gone
    xp = FakeXPlane(xp_port, 180.0)  # fresh sim, empty subscription table
    try:
        assert _wait_for(writes, 180.0, 2.0), \
            "no RREF data after X-Plane restarted under a running gateway"
    finally:
        xp.stop()


def test_resubscribe_cadence_is_wall_clock_under_traffic(gateway):
    """The re-send interval is seconds, not loop iterations: a busy link
    (every received packet is a loop pass) must not multiply it."""
    t, xp_port, writes = gateway
    xp = FakeXPlane(xp_port, 45.0)   # streams ~50 packets/s while subscribed
    try:
        assert _wait_for(writes, 45.0, 2.0)
        before = xp.requests
        time.sleep(1.5)
        resent = xp.requests - before
    finally:
        xp.stop()
    # 0.3 s interval over 1.5 s -> 5 re-sends; allow scheduling slack.
    assert 3 <= resent <= 7, "%d re-subscriptions in 1.5 s" % resent
