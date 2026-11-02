"""OnSpeed plugin database writes: pyEfis docs/fixgw_onspeed_in_spec.md 7.2.

The plugin runs against the real database (the AOA entry as defined in
database/ahrs.yaml) and a callback recorder that sees every notifying
update, so a value and its flags are checked as one tuple.
"""

import io
import threading
import time

import pytest
import serial

import fixgw.database as database
import fixgw.plugin as plugin_base
import fixgw.plugins.onspeed as onspeed
from fixgw.plugins.onspeed.sentence import encode

from .test_sentence import V1, V1_RAW, V2, V4, V5

DB_CONFIG = """
variables: {}
entries:
- key: AOA
  description: Angle of attack
  type: float
  min: -180.0
  max: 180.0
  units: deg
  initial: 0.0
  tol: 300
  aux: [Min, Max, 0g, Warn, Stall]
- key: ZZLOADER
  description: DB load sentinel
  type: str
  initial: "Loaded"
"""

CRLF = b"\r\n"


def _frame(**raw):
    """V1 with named raw fields replaced."""
    values = list(V1_RAW)
    for name, value in raw.items():
        index = [f[0] for f in onspeed.sentence.FIELDS].index(name)
        values[index] = value
    return encode(values).encode()


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, key, value, udata):
        self.calls.append((key, value))

    def of(self, key):
        return [v for k, v in self.calls if k == key]


@pytest.fixture
def db():
    database.init(io.StringIO(DB_CONFIG))
    rec = Recorder()
    database.callback_add("test", "AOA", rec, None)
    return rec


def _plugin(**config):
    cfg = {"port": "/dev/null"}
    cfg.update(config)
    return onspeed.Plugin("onspeed", cfg, {})


def _fed(plugin, *lines):
    """Feed lines after an opening partial line (always discarded)."""
    plugin.thread.feed(b"partial" + CRLF + b"".join(l + CRLF for l in lines))
    return plugin


def _aoa():
    value, _ann, old, bad, fail, _sec = database.read("AOA")
    return value, old, bad, fail


# 7.2.1
def test_v1_writes_aoa_and_warn(db):
    p = _fed(_plugin(), V1)
    assert _aoa() == (9.8, False, False, False)
    assert database.read("AOA.Warn") == 13.5
    assert p.thread.counters["frames"] == 1


# 7.2.2
def test_low_ias_frame_is_published_bad_then_clears(db):
    p = _fed(_plugin(min_ias=30), V2)
    assert _aoa() == (0.0, False, True, False)
    # The value and the bad flag arrived in one update.
    assert db.of("AOA")[-1][:4] == (0.0, False, False, True)
    p.thread.feed(V1 + CRLF)
    assert _aoa() == (9.8, False, False, False)
    assert p.thread.counters["low_ias"] == 1


def test_min_ias_defaults_to_30_kt(db):
    p = _fed(_plugin(), _frame(ias=299))
    assert _aoa()[2] is True
    p.thread.feed(_frame(ias=300) + CRLF)
    assert _aoa()[2] is False


# 7.2.3
def test_zero_aoa_above_min_ias_is_good(db):
    _fed(_plugin(min_ias=20), V2)
    assert _aoa() == (0.0, False, False, False)


# 7.2.4
def test_uncalibrated_warn_is_none_then_set(db):
    p = _fed(_plugin(), V5)
    assert database.read("AOA.Warn") is None
    p.thread.feed(V1 + CRLF)
    assert database.read("AOA.Warn") == 13.5


# 7.2.5
def test_setpoints_out_of_order_clear_warn(db):
    # stall warning 10.0 deg below OnSpeed slow 11.2 deg
    p = _fed(_plugin(), V1, _frame(stall_warn_aoa=100))
    assert database.read("AOA.Warn") is None
    assert p.thread.counters["setpoint_order_errors"] == 1
    assert _aoa()[0] == 9.8


@pytest.mark.parametrize(
    "raw",
    [
        {"onspeed_slow_aoa": 140},  # slow above warn
        {"onspeed_fast_aoa": 120},  # fast above slow
        {"tones_on_aoa": 94},  # tones-on not below fast
    ],
)
def test_each_order_rule(db, raw):
    p = _fed(_plugin(), _frame(**raw))
    assert database.read("AOA.Warn") is None
    assert p.thread.counters["setpoint_order_errors"] == 1


def test_equal_fast_slow_warn_is_in_order(db):
    # fast <= slow <= warn allows equality; tones-on < fast does not.
    _fed(_plugin(), _frame(onspeed_fast_aoa=135, onspeed_slow_aoa=135))
    assert database.read("AOA.Warn") == 13.5


# 7.2.6
def test_warn_written_once_for_an_unchanging_value(db):
    _fed(_plugin(), *([V1] * 10))
    assert db.of("AOA.Warn") == [13.5]
    assert len(db.of("AOA")) == 10


# 7.2.7
def test_warn_written_again_on_change(db):
    _fed(_plugin(), V1, _frame(stall_warn_aoa=150))
    assert db.of("AOA.Warn") == [13.5, 15.0]


def test_warn_cleared_once_while_uncalibrated(db):
    _fed(_plugin(), V5, V5, V5, V1)
    assert db.of("AOA.Warn") == [None, 13.5]


# 7.2.8
def test_overflowed_frame_between_good_ones_writes_nothing(db):
    p = _fed(_plugin(), V1)
    before = list(db.calls)
    p.thread.feed(V4 + CRLF)
    assert db.calls == before
    assert p.thread.counters["grammar"] == 1
    p.thread.feed(V1 + CRLF)
    assert _aoa() == (9.8, False, False, False)
    assert p.thread.counters["frames"] == 2


def test_bad_lines_are_counted_by_reason(db):
    flipped = bytearray(V1)
    flipped[40] = ord("9")
    p = _fed(
        _plugin(),
        V1[:50],  # length
        bytes(flipped),  # checksum
        b"=1100000000+052",  # G3X
        V1[:40] + b"\xb0" + V1[41:],  # encoding
    )
    c = p.thread.counters
    assert (c["length"], c["checksum"], c["wrong_format"], c["encoding"]) == (1, 1, 1, 1)
    assert c["frames"] == 0
    assert db.calls == []


# Line assembly: chunks, not whole lines (7.2 preamble)
def test_sentence_split_across_reads_and_two_in_one_read(db):
    p = _plugin()
    p.thread.feed(b"tail of something\r\n" + V1[:30])
    assert p.thread.counters["frames"] == 0
    p.thread.feed(V1[30:] + CRLF + _frame(aoa=101) + CRLF + V1[:5])
    assert p.thread.counters["frames"] == 2
    assert [v[0] for v in db.of("AOA")] == [9.8, 10.1]
    p.thread.feed(V1[5:] + CRLF)
    assert p.thread.counters["frames"] == 3


def test_bare_lf_terminator_is_accepted(db):
    p = _plugin()
    p.thread.feed(b"x\n" + V1 + b"\n")
    assert p.thread.counters["frames"] == 1


# 7.2.9
def test_overlong_line_is_discarded_and_counted(db):
    p = _fed(_plugin())
    p.thread.feed(b"7" * 129)
    assert p.thread.counters["overlong"] == 1
    assert len(p.thread._buffer) == 0
    # The rest of that line is dropped too, up to its newline.
    p.thread.feed(b"more of the same line" + CRLF)
    assert p.thread.counters["lines"] == 0
    p.thread.feed(V1 + CRLF)
    assert p.thread.counters["frames"] == 1
    assert _aoa()[0] == 9.8


def test_buffer_never_grows_past_the_limit(db):
    p = _fed(_plugin())
    for _ in range(100):
        p.thread.feed(b"7" * 50)
        assert len(p.thread._buffer) <= onspeed.MAX_LINE
    assert p.thread.counters["overlong"] >= 1


# 7.2.10
def test_first_line_after_open_is_discarded(db):
    p = _plugin()
    p.thread.feed(V1[40:] + CRLF + V1 + CRLF)
    assert p.thread.counters["lines"] == 1
    assert p.thread.counters["frames"] == 1
    assert p.thread.counters["length"] == 0


# 7.2.11
class FakePort:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.closed = False

    @property
    def in_waiting(self):
        if self.chunks and isinstance(self.chunks[0], bytes):
            return len(self.chunks[0])
        return 0

    def read(self, n):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk

    def close(self):
        self.closed = True


@pytest.fixture
def ports(monkeypatch):
    """serial.Serial replaced by a queue of ports (or exceptions to raise)."""
    queue = []

    def factory(*args, **kwargs):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(onspeed.serial, "Serial", factory)
    return queue


def _step(p, n=1):
    p.thread._wake.set()  # no real backoff sleep
    for _ in range(n):
        p.thread.step()


def test_port_failure_sets_fail_and_a_good_frame_clears_it(db, ports):
    good = FakePort([CRLF + V1 + CRLF, serial.SerialException("unplugged")])
    again = FakePort([CRLF + V1 + CRLF])
    ports.extend([good, serial.SerialException("no such port"), again])
    p = _plugin()
    _step(p, 2)  # open, read V1
    assert _aoa() == (9.8, False, False, False)
    _step(p)  # read raises: port lost
    assert database.read("AOA")[4] is True
    assert good.closed and not p.thread.port_open
    _step(p)  # reopen fails
    assert database.read("AOA")[4] is True
    _step(p, 2)  # reopen succeeds, V1
    assert _aoa() == (9.8, False, False, False)
    assert p.get_status()["Port Open"] is True


def test_open_failure_sets_fail_and_backs_off(db, ports):
    ports.extend([serial.SerialException("busy")] * 6)
    p = _plugin()
    waits = []
    p.thread._wake.wait = lambda t: waits.append(t)
    for _ in range(6):
        p.thread.step()
    assert database.read("AOA")[4] is True
    assert waits == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


# 7.2.12
def test_staleness_comes_from_the_database_tol(db, monkeypatch):
    threads_before = threading.active_count()
    _fed(_plugin(), V1)
    assert threading.active_count() == threads_before  # no timer of its own
    now = time.time()
    monkeypatch.setattr(database.time, "time", lambda: now + 0.4)
    database.update()
    assert _aoa() == (9.8, True, False, False)
    assert db.of("AOA")[-1][2] is True  # the expiry notified


# 7.2.13
def test_run_then_stop_joins_within_one_second(db, monkeypatch):
    class SlowPort(FakePort):
        def read(self, n):
            time.sleep(onspeed.READ_TIMEOUT)
            return b""

    monkeypatch.setattr(onspeed.serial, "Serial", lambda *a, **k: SlowPort())
    p = _plugin()
    p.run()
    time.sleep(0.1)
    start = time.monotonic()
    p.stop()
    assert time.monotonic() - start < 1.0
    assert not p.thread.is_alive()


def test_hung_thread_raises_plugin_fail(db):
    p = _plugin()

    class Hung:
        def stop(self):
            pass

        def is_alive(self):
            return True

        def join(self, timeout):
            pass

    p.thread = Hung()
    with pytest.raises(plugin_base.PluginFail):
        p.stop()


# Status and configuration
def test_status_shows_every_decoded_field(db):
    p = _fed(_plugin(), V1)
    s = p.get_status()
    assert list(s)[:2] == ["Port", "Port Open"]
    assert s["Frames Decoded"] == 1
    assert s["Last Frame Age (s)"] is not None
    assert s["Last Frame"]["pitch"] == 5.2
    assert s["Last Frame"]["flaps"] == 0
    assert s["Last Frame"]["tones_on_aoa"] == 6.0


def test_only_aoa_is_written(db, monkeypatch):
    writes = []
    p = _plugin()
    monkeypatch.setattr(p, "db_write", lambda k, v: writes.append(k))
    _fed(p, V1, V2, V5)
    assert set(writes) == {"AOA", "AOA.Warn"}


def test_unknown_option_and_odd_baud_warn(db, caplog):
    caplog.set_level("WARNING")
    p = _plugin(baud=9600, colour="red", CONFIGPATH="/etc/fixgw")
    text = caplog.text
    assert "colour" in text
    assert "CONFIGPATH" not in text  # the server adds it to every plugin
    assert "9600" in text
    assert p.config["baud"] == 9600


def test_warn_cleared_when_calibration_is_lost(db):
    _fed(_plugin(), V1, V5)
    assert db.of("AOA.Warn") == [13.5, None]
    assert database.read("AOA.Warn") is None
