"""
Unit tests for the Stratux GDL90 FIX-Gateway plugin.

Covers:
  1. gdl90.decode_datagram — framing, byte-stuffing, CRC validation
  2. Stratux MainThread — AHRS (0x4C) and ownship (0x0A) message dispatch

Traffic (0x14) and the TFC block are covered in test_traffic.py.

The socket is never opened; we call the parse logic directly.
"""
import io
import struct
import unittest
from unittest.mock import MagicMock

import pytest

import fixgw.database as database
import fixgw.plugin as plugin_base
import fixgw.plugins.stratux as stratux
from fixgw.plugins.stratux import gdl90 as gdl90mod
from fixgw.plugins.stratux import MainThread, Plugin

DB_CONFIG = """
variables:
  e: 1
  c: 1
  a: 1
  b: 1
  r: 1
  t: 1

entries:
- key: PITCH
  description: Pitch
  type: float
  min: -180.0
  max: 180.0
  units: deg
  initial: 0.0
  tol: 2000
- key: ROLL
  description: Roll
  type: float
  min: -180.0
  max: 180.0
  units: deg
  initial: 0.0
  tol: 2000
- key: HEAD
  description: Heading
  type: float
  min: 0.0
  max: 360.0
  units: deg
  initial: 0.0
  tol: 2000
- key: ALAT
  description: Lateral Acceleration
  type: float
  min: -5.0
  max: 5.0
  units: g
  initial: 0.0
  tol: 2000
- key: ALT
  description: Altitude
  type: float
  min: -1500.0
  max: 60000.0
  units: ft
  initial: 0.0
  tol: 2000
- key: VS
  description: Vertical Speed
  type: int
  min: -6000
  max: 6000
  units: fpm
  initial: 0
  tol: 2000
- key: IAS
  description: Indicated Airspeed
  type: int
  min: 0
  max: 9999
  units: knots
  initial: 0
  tol: 2000
- key: GS
  description: Ground Speed
  type: float
  min: 0.0
  max: 2000.0
  units: knots
  initial: 0.0
  tol: 2000
- key: ZZLOADER
  description: DB load sentinel
  type: str
  initial: "Loaded"
"""


# ── GDL90 framing helpers ────────────────────────────────────────────────────

def _stuff(payload: bytes) -> bytes:
    """Apply GDL90 byte stuffing: 0x7E → 0x7D 0x5E, 0x7D → 0x7D 0x5D."""
    out = bytearray()
    for b in payload:
        if b == 0x7E:
            out += b"\x7D\x5E"
        elif b == 0x7D:
            out += b"\x7D\x5D"
        else:
            out.append(b)
    return bytes(out)


def _frame(payload: bytes) -> bytes:
    """Wrap payload in GDL90 framing (start 0x7E, CRC LSB first, end 0x7E).

    The FCS is stuffed along with the data (G§2.2.1).  This helper used to
    append it raw, which the old decoder tolerated because it read the last
    two bytes without unstuffing: the stuffing tests below have FCS values
    0x7E01 and 0x7D02, so they only passed with both halves wrong.
    """
    crc = gdl90mod.calc_crc(payload)
    return b"\x7E" + _stuff(payload + struct.pack("<H", crc)) + b"\x7E"


def _build_ahrs(roll_10=0, pitch_10=0, heading_10=0, slipskid_10=0,
                yawrate_10=0, g_10=10, ias_10=0, alt_offset=0, vs=0) -> bytes:
    """Build a framed GDL90 AHRS message (type 0x4C)."""
    payload = struct.pack(">B3xhhhhhhhhh",
                          0x4C,                # msg type
                          roll_10,             # [4:6]
                          pitch_10,            # [6:8]
                          heading_10,          # [8:10]
                          slipskid_10,         # [10:12]
                          yawrate_10,          # [12:14]
                          g_10,                # [14:16]
                          ias_10,              # [16:18]
                          alt_offset,          # [18:20] — stored as (alt + 5000.5) rounded
                          vs)                  # [20:22]
    return _frame(payload)


def _build_ownship(alt=0, speed_raw=0) -> bytes:
    """Build a framed GDL90 ownship message (type 0x0A).

    speed_raw: 12-bit ground speed value; packed as two bytes where
               gnd_speed = (tmp[0] << 4) | (tmp[1] >> 4)
    """
    # Bytes 14-15 encode the 12-bit speed: first 8 bits in byte14, next 4 in high nibble of byte15
    byte14 = (speed_raw >> 4) & 0xFF
    byte15 = (speed_raw & 0x0F) << 4
    # Pad to 22 bytes total (enough to cover msg[11:16])
    payload = bytearray(22)
    payload[0] = 0x0A
    struct.pack_into(">h", payload, 11, alt)
    payload[14] = byte14
    payload[15] = byte15
    return _frame(bytes(payload))


def _decode1(framed: bytes) -> bytes:
    """The single message in a one-frame datagram, or b"" if it was discarded."""
    msgs = gdl90mod.decode_datagram(framed)
    return msgs[0] if msgs else b""


# ── Thread factory ───────────────────────────────────────────────────────────

def _make_thread():
    parent = MagicMock()
    parent.config = {}
    parent.log = MagicMock()
    # Patch socket so __init__ doesn't bind a real port
    import socket
    parent_socket = MagicMock(spec=socket.socket)
    thread = MainThread.__new__(MainThread)
    thread.getout = False
    thread.parent = parent
    thread.log = parent.log
    thread.s = parent_socket
    thread.traffic = None
    parent.db_write = lambda key, val: database.write(key, val)
    return thread


def _db(key):
    return database.read(key)[0]


# ── GDL90 codec tests ────────────────────────────────────────────────────────

class TestGdl90Codec(unittest.TestCase):
    def test_round_trip_simple(self):
        payload = bytes([0x00, 0x81, 0x41, 0xDB])
        framed = _frame(payload)
        decoded = _decode1(framed)
        self.assertEqual(decoded, payload)

    def test_crc_mismatch_returns_empty(self):
        payload = bytes([0x4C, 0x00, 0x00, 0x00])
        framed = bytearray(_frame(payload))
        # Corrupt one CRC byte
        framed[-2] ^= 0xFF
        self.assertEqual(_decode1(bytes(framed)), b"")

    def test_byte_stuffing_0x7E(self):
        payload = bytes([0x7E, 0x01])
        framed = _frame(payload)
        decoded = _decode1(framed)
        self.assertEqual(decoded, payload)

    def test_byte_stuffing_0x7D(self):
        payload = bytes([0x7D, 0x02])
        framed = _frame(payload)
        decoded = _decode1(framed)
        self.assertEqual(decoded, payload)

    def test_known_crc_value(self):
        # CRC of single byte 0x00 is deterministic
        crc = gdl90mod.calc_crc(b"\x00")
        self.assertIsInstance(crc, int)
        self.assertGreaterEqual(crc, 0)
        self.assertLessEqual(crc, 0xFFFF)


# ── AHRS (0x4C) dispatch tests ───────────────────────────────────────────────

class TestStratuxAhrs(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def _dispatch(self, raw_framed: bytes):
        msg = _decode1(raw_framed)
        self.assertGreater(len(msg), 0, "GDL90 decode failed — check test helper")
        self.thread.dispatch(msg)

    def test_pitch(self):
        self._dispatch(_build_ahrs(pitch_10=150))
        self.assertAlmostEqual(_db("PITCH"), 15.0, places=3)

    def test_roll_negative(self):
        self._dispatch(_build_ahrs(roll_10=-300))
        self.assertAlmostEqual(_db("ROLL"), -30.0, places=3)

    def test_heading(self):
        self._dispatch(_build_ahrs(heading_10=900))
        self.assertAlmostEqual(_db("HEAD"), 90.0, places=3)

    def test_altitude(self):
        # alt_offset = round(1500 + 5000.5) = 6500 or 6501; decoded as 6500 - 5000.5 = 1499.5
        alt_stored = round(1500 + 5000.5)
        self._dispatch(_build_ahrs(alt_offset=alt_stored))
        self.assertAlmostEqual(_db("ALT"), alt_stored - 5000.5, places=1)

    def test_vertical_speed(self):
        self._dispatch(_build_ahrs(vs=500))
        self.assertEqual(_db("VS"), 500)

    def test_alat_zero_slipskid(self):
        # No slip → ALAT = -sin(0) = 0
        self._dispatch(_build_ahrs(slipskid_10=0))
        self.assertAlmostEqual(_db("ALAT"), 0.0, places=5)

    def test_alat_sign(self):
        # slipskid = 30° → ALAT = -sin(30°) = -0.5
        self._dispatch(_build_ahrs(slipskid_10=300))
        self.assertAlmostEqual(_db("ALAT"), -0.5, places=3)


# ── Ownship (0x0A) dispatch tests ────────────────────────────────────────────

class TestStratuxOwnship(unittest.TestCase):
    def setUp(self):
        database.init(io.StringIO(DB_CONFIG))
        self.thread = _make_thread()

    def _dispatch(self, raw_framed: bytes):
        msg = _decode1(raw_framed)
        self.assertGreater(len(msg), 0)
        self.thread.dispatch(msg)

    def test_groundspeed_zero(self):
        self._dispatch(_build_ownship(speed_raw=0))
        self.assertEqual(_db("GS"), 0)

    def test_groundspeed_120(self):
        self._dispatch(_build_ownship(speed_raw=120))
        self.assertEqual(_db("GS"), 120)

    def test_groundspeed_does_not_write_ias(self):
        # Ownship horizontal velocity is ground speed. It must never land on
        # the airspeed tape.
        database.write("IAS", 87)
        self._dispatch(_build_ownship(speed_raw=120))
        self.assertEqual(_db("IAS"), 87)

    def test_groundspeed_no_data_not_written(self):
        # GDL90 reserves 0xFFF for "no horizontal velocity available".
        # Leave GS alone so it times out rather than showing 4095 kt.
        self._dispatch(_build_ownship(speed_raw=55))
        self._dispatch(_build_ownship(speed_raw=0xFFF))
        self.assertEqual(_db("GS"), 55)

    def test_groundspeed_max_value(self):
        # 0xFFE means 4094 kt or more; the GS key clamps at its max.
        self._dispatch(_build_ownship(speed_raw=0xFFE))
        self.assertEqual(_db("GS"), 2000.0)


class FakeSocket:
    def __init__(self, packets=None):
        self.packets = list(packets or [])
        self.bound = None

    def bind(self, address):
        self.bound = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True

    def recvfrom(self, _size):
        packet = self.packets.pop(0)
        return packet, ("127.0.0.1", 12345)


def test_main_thread_initializes_socket_and_stop(monkeypatch, capsys):
    fake_socket = FakeSocket()
    parent = MagicMock()
    parent.config = {"traffic": False}
    parent.log = MagicMock()
    monkeypatch.setattr(stratux.socket, "socket", lambda *_args: fake_socket)

    thread = MainThread(parent)
    thread.stop()

    assert "running stratux plugin" in capsys.readouterr().out
    assert fake_socket.bound == ("", 4000)
    assert thread.getout is True


def test_run_decodes_ahrs_ownship_and_skips_invalid_frames(monkeypatch):
    database.init(io.StringIO(DB_CONFIG))
    fake_socket = FakeSocket(
        [
            b"not a frame",
            _build_ahrs(roll_10=100, pitch_10=-50, heading_10=1234, slipskid_10=100, alt_offset=6000, vs=-123),
            _build_ownship(speed_raw=321),
        ]
    )
    parent = MagicMock()
    parent.log = MagicMock()
    parent.config = {}
    parent.db_write = lambda key, value: database.write(key, value)
    # This database has no TFC block, so the traffic writer stays off.
    parent.db_get_item = database.get_raw_item
    monkeypatch.setattr(stratux.socket, "socket", lambda *_args: fake_socket)
    thread = MainThread(parent)
    original_recvfrom = fake_socket.recvfrom

    def stop_after_last_packet(size):
        packet, address = original_recvfrom(size)
        if not fake_socket.packets:
            thread.getout = True
        return packet, address

    fake_socket.recvfrom = stop_after_last_packet
    thread.run()

    assert thread.running is False
    assert _db("ROLL") == 10.0
    assert _db("PITCH") == -5.0
    assert _db("HEAD") == 123.4
    assert _db("ALT") == 999.5
    assert _db("VS") == -123
    assert _db("GS") == 321
    assert _db("IAS") == 0
    assert thread.traffic is None
    assert fake_socket.closed is True


def test_plugin_lifecycle_status_and_failure(monkeypatch):
    class DummyThread:
        def __init__(self, _parent):
            self.started = False
            self.stopped = False
            self.alive = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.joined = timeout

    monkeypatch.setattr(stratux, "MainThread", DummyThread)
    plugin = Plugin("stratux", {}, {})

    plugin.run()
    plugin.stop()

    assert plugin.thread.started is True
    assert plugin.thread.stopped is True
    assert plugin.get_status() == stratux.OrderedDict()

    failing = Plugin("stratux", {}, {})
    failing.thread.alive = True
    with pytest.raises(plugin_base.PluginFail):
        failing.stop()


if __name__ == "__main__":
    unittest.main()
