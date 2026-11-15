"""
Stratux receiver health from its /getStatus JSON (GDL4, AER-5652).

The fixtures are in data/ (see data/README.md for where each came from).
Every test runs on the shipped database, so the STX keys, their ranges and
their tol are the real ones.
"""
import http.server
import json
import pathlib
import socket
import threading
import time
from unittest.mock import MagicMock

import pytest

import fixgw.plugins.stratux as stratux
from fixgw.plugins.stratux import status as sx
from fixgw.plugins.stratux import gdl90

DATA = pathlib.Path(__file__).parent / "data"
SOURCE = "192.168.10.1"


def _load(name):
    return json.loads((DATA / name).read_text())


def _read(database, key):
    value, _annunc, old, bad, fail, _secfail = database.read(key)
    return value, old, bad, fail


def _poller(database, fetch, host=SOURCE, status=None):
    return sx.StatusPoller(
        database.get_raw_item, MagicMock(), host, {} if status is None else status, fetch=fetch
    )


def _good(database, key):
    value, old, bad, fail = _read(database, key)
    assert not old and not bad and not fail, key
    return value


def _bad(database, key):
    _value, old, bad, _fail = _read(database, key)
    assert not old, key
    return bad


# ── Decoding the recorded and built responses ────────────────────────────────


def test_a_3d_sbas_fix(database):
    p = _poller(database, lambda url, timeout: _load("getstatus_eu023_fix_sbas.json"))
    assert p.poll_once() is True

    assert _good(database, "STXALIVE") is True
    assert _good(database, "STXGPS") is True
    assert _good(database, "STXAHRS") is True
    assert _good(database, "STXBARO") is True
    assert _good(database, "STXGPSFIX") == 4
    assert _good(database, "STXSATS") == 11
    assert _good(database, "STXSATSV") == 14
    assert _good(database, "STXGPSACC") == pytest.approx(3.6 / 0.3048)
    assert _good(database, "STXCPUT") == pytest.approx(53.692)


def test_no_fix_accuracy_sentinel_is_bad(database):
    # isGPSValid() sets GPS_position_accuracy = 999999 without a fix.  It is
    # 3.3 million feet, which the key would clamp to 100,000 ft and pass off
    # as a (terrible) real figure if it were not flagged.
    p = _poller(database, lambda url, timeout: _load("getstatus_eu023_no_fix.json"))
    p.poll_once()

    assert _good(database, "STXGPSFIX") == 1
    assert _good(database, "STXSATS") == 0
    assert _good(database, "STXSATSV") == 4
    assert _bad(database, "STXGPSACC") is True
    assert _good(database, "STXGPS") is True


def test_booting_receiver(database):
    # Before the first temperature reading CPUTemp is invalidCpuTemp = -99,
    # and with the GPS not yet found the solution is "Disconnected".
    p = _poller(database, lambda url, timeout: _load("getstatus_eu023_booting.json"))
    p.poll_once()

    assert _good(database, "STXALIVE") is True
    assert _good(database, "STXGPS") is False
    assert _good(database, "STXAHRS") is False
    assert _good(database, "STXBARO") is False
    assert _good(database, "STXGPSFIX") == 0
    assert _bad(database, "STXGPSACC") is True
    assert _bad(database, "STXCPUT") is True


def test_recorded_flightbox_3d_fix(database):
    # Recorded from the bench FlightBox (v1.6r1-eu023, u-blox 7) on
    # 2026-11-15: a 3D fix without SBAS.
    p = _poller(database, lambda url, timeout: _load("getstatus_flightbox_3d.json"))
    p.poll_once()

    assert _good(database, "STXALIVE") is True
    assert _good(database, "STXGPS") is True
    assert _good(database, "STXAHRS") is True
    assert _good(database, "STXBARO") is True
    assert _good(database, "STXGPSFIX") == 3
    assert _good(database, "STXSATS") == 7
    assert _good(database, "STXSATSV") == 9
    assert _good(database, "STXGPSACC") == pytest.approx(5.85 / 0.3048)
    assert _good(database, "STXCPUT") == pytest.approx(51.382)


def test_published_v1_4_response(database):
    # The example in Stratux's own notes/app-vendor-integration.md, with
    # the older SBAS string.
    p = _poller(database, lambda url, timeout: _load("getstatus_v1.4r2_stratux_notes.json"))
    p.poll_once()

    assert _good(database, "STXGPSFIX") == 4
    assert _good(database, "STXSATS") == 5
    assert _good(database, "STXGPSACC") == pytest.approx(10.2 / 0.3048)
    assert _good(database, "STXCPUT") == pytest.approx(47.774)
    assert _good(database, "STXAHRS") is True
    assert _good(database, "STXBARO") is True


@pytest.mark.parametrize("field, key", [("IMUConnected", "STXAHRS"), ("GPS_connected", "STXGPS")])
def test_a_missing_connected_flag_is_bad(database, field, key):
    status = _load("getstatus_eu023_fix_sbas.json")
    del status[field]
    p = _poller(database, lambda url, timeout: status)
    p.poll_once()
    assert _bad(database, key) is True


@pytest.mark.parametrize(
    "field, key",
    [
        ("GPS_position_accuracy", "STXGPSACC"),
        ("GPS_satellites_locked", "STXSATS"),
        ("GPS_satellites_seen", "STXSATSV"),
        ("CPUTemp", "STXCPUT"),
    ],
)
@pytest.mark.parametrize("sentinel", [-9999, -999.9, -99, 999999, None, "n/a", True])
def test_no_data_values_are_bad_never_a_number(database, field, key, sentinel):
    status = _load("getstatus_eu023_fix_sbas.json")
    status[field] = sentinel
    p = _poller(database, lambda url, timeout: status)
    p.poll_once()

    assert _bad(database, key) is True
    # The other keys still read.
    assert _good(database, "STXGPSFIX") == 4


@pytest.mark.parametrize("solution", ["Unknown", "RTK Fixed", "", None])
def test_an_unlisted_solution_is_bad_and_so_is_its_accuracy(database, solution):
    status = _load("getstatus_eu023_fix_sbas.json")
    status["GPS_solution"] = solution
    p = _poller(database, lambda url, timeout: status)
    p.poll_once()

    assert _bad(database, "STXGPSFIX") is True
    assert _bad(database, "STXGPSACC") is True
    assert _good(database, "STXSATS") == 11


def test_a_bad_value_recovers_on_the_next_good_poll(database):
    replies = [_load("getstatus_eu023_no_fix.json"), _load("getstatus_eu023_fix_sbas.json")]
    p = _poller(database, lambda url, timeout: replies.pop(0))
    p.poll_once()
    assert _bad(database, "STXGPSACC") is True
    p.poll_once()
    assert _bad(database, "STXGPSACC") is False


def test_the_keys_are_booked_to_stratux(database):
    p = _poller(database, lambda url, timeout: _load("getstatus_eu023_fix_sbas.json"))
    p.poll_once()
    writers = database.writers()
    for key in sx.KEYS:
        assert writers[key] == ["stratux"], key


# ── A failed or slow poll takes every key old ────────────────────────────────


def _assert_all_old(database):
    for key in sx.KEYS:
        _value, old, _bad, _fail = _read(database, key)
        assert old, key
    assert database.read("STXALIVE")[0] is False


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionRefusedError(111, "Connection refused"),
        socket.timeout("timed out"),
        json.JSONDecodeError("Expecting value", "<html>", 0),
        "not a dict",
        {"Version": "v1.6r1"},
    ],
)
def test_a_failed_poll_takes_every_key_old(database, failure):
    replies = [_load("getstatus_eu023_fix_sbas.json"), failure]

    def fetch(url, timeout):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    status = {}
    p = _poller(database, fetch, status=status)
    assert p.poll_once() is True
    assert p.poll_once() is False

    _assert_all_old(database)
    assert status["Status poll failures"] == 1


def test_recovery_clears_old(database):
    replies = [OSError("down"), _load("getstatus_eu023_fix_sbas.json")]

    def fetch(url, timeout):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    p = _poller(database, fetch)
    p.poll_once()
    _assert_all_old(database)
    p.poll_once()
    for key in sx.KEYS:
        assert not _read(database, key)[1], key
    assert database.read("STXALIVE")[0] is True


def test_a_hung_poll_goes_old_by_tol(database):
    # A poll that never returns writes nothing.  The shipped tol must then
    # take every key old: three missed 1 Hz polls.
    p = _poller(database, lambda url, timeout: _load("getstatus_eu023_fix_sbas.json"))
    p.poll_once()
    for key in sx.KEYS:
        item = database.get_raw_item(key)
        assert item.tol == 3000, key
        item.timestamp -= 3.1
        assert database.read(key)[2] is True, key


class _Handler(http.server.BaseHTTPRequestHandler):
    reply = b""
    delay = 0.0
    paths = []

    def do_GET(self):
        type(self).paths.append(self.path)
        time.sleep(self.delay)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(self.reply)
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    handler = type("H", (_Handler,), {"paths": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server, handler
    server.shutdown()
    server.server_close()


def test_real_http_poll(database, http_server):
    server, handler = http_server
    handler.reply = (DATA / "getstatus_eu023_fix_sbas.json").read_bytes()
    p = _poller(database, sx.fetch_status, host="127.0.0.1:%d" % server.server_address[1])

    assert p.poll_once() is True
    assert handler.paths == ["/getStatus"]
    assert _good(database, "STXSATS") == 11


def test_a_slow_web_server_times_out_and_goes_old(database, http_server):
    # The Pi 2's web API stalls under load: the poll gives up at TIMEOUT_S.
    server, handler = http_server
    handler.reply = (DATA / "getstatus_eu023_fix_sbas.json").read_bytes()
    p = _poller(database, sx.fetch_status, host="127.0.0.1:%d" % server.server_address[1])
    assert p.poll_once() is True

    handler.delay = sx.TIMEOUT_S + 1.0
    start = time.monotonic()
    assert p.poll_once() is False
    assert time.monotonic() - start < sx.TIMEOUT_S + 0.5
    _assert_all_old(database)


def test_a_failure_backs_off_before_the_next_poll(database):
    waits = []

    class Stop:
        def is_set(self):
            return len(waits) >= 3

        def wait(self, s):
            waits.append(s)

    replies = [OSError("down"), _load("getstatus_eu023_fix_sbas.json"), OSError("down")]

    def fetch(url, timeout):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    p = _poller(database, fetch)
    p.stop_event = Stop()
    p.run()

    assert waits == [sx.BACKOFF_S, sx.POLL_S, sx.BACKOFF_S]
    # One warning for the outage, one line for the recovery.
    assert p.log.warning.call_count == 2
    p.log.info.assert_called_once()


# ── Wiring into the plugin ───────────────────────────────────────────────────


class _FakeSocket:
    def __init__(self, packets):
        self.packets = list(packets)

    def bind(self, addr):
        pass

    def settimeout(self, t):
        pass

    def close(self):
        pass

    def recvfrom(self, _size):
        if not self.packets:
            raise socket.timeout()
        return self.packets.pop(0)


def _ahrs(roll_10):
    import struct

    payload = struct.pack(">B3xhhhhhhhhh", 0x4C, roll_10, 0, 0, 0, 0, 10, 0, 5000, 0)
    return gdl90.encode(payload)


def _thread(database, monkeypatch, config, packets=()):
    parent = MagicMock()
    parent.log = MagicMock()
    parent.config = dict(config, traffic=False, ownship=False)
    parent.status = {}
    parent.db_write = database.write
    parent.db_get_item = database.get_raw_item
    monkeypatch.setattr(stratux.socket, "socket", lambda *_a: _FakeSocket(packets))
    return stratux.MainThread(parent), parent


def test_the_poll_goes_to_the_source_address_only(database, monkeypatch):
    thread, _parent = _thread(database, monkeypatch, {"source": SOURCE})
    assert thread.poller.url == "http://192.168.10.1/getStatus"


@pytest.mark.parametrize("source", ["", "stratux.local"])
def test_no_valid_source_no_poll(database, monkeypatch, source):
    thread, _parent = _thread(database, monkeypatch, {"source": source})
    assert thread.poller is None


def test_status_false_turns_the_poll_off(database, monkeypatch):
    thread, _parent = _thread(database, monkeypatch, {"source": SOURCE, "status": False})
    assert thread.poller is None


def test_a_database_without_the_stx_block_disables_the_poll(database, monkeypatch):
    # An install whose edited database.yaml predates stratux.yaml.
    real = database.get_raw_item

    def get_item(key):
        if key.startswith("STX"):
            raise KeyError(key)
        return real(key)

    monkeypatch.setattr(database, "get_raw_item", get_item)
    thread, parent = _thread(database, monkeypatch, {"source": SOURCE})
    assert thread.poller is None
    parent.log.error.assert_called_once()


def test_a_hung_poll_never_blocks_the_udp_decode(database, monkeypatch):
    release = threading.Event()
    entered = threading.Event()

    def fetch(url, timeout):
        entered.set()
        release.wait(10)
        raise OSError("released")

    monkeypatch.setattr(sx, "fetch_status", fetch)
    packets = [(_ahrs(r), (SOURCE, 4000)) for r in (100, 200, 300)]
    thread, _parent = _thread(database, monkeypatch, {"source": SOURCE}, packets)

    runner = threading.Thread(target=thread.run)
    runner.start()
    assert entered.wait(5), "the poller never started"
    deadline = time.monotonic() + 5
    while database.read("ROLL")[0] != 30.0 and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        # Every AHRS message decoded while the poll is still stuck.
        assert database.read("ROLL")[0] == 30.0
        assert thread.poller.is_alive()
        thread.stop()
        runner.join(2)
        assert not runner.is_alive()
    finally:
        thread.stop()
        release.set()
    thread.poller.join(2)
    assert not thread.poller.is_alive()
