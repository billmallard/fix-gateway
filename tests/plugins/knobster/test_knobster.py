"""Knobster plugin tests, driven by the vendor-library USB trace.

The fixture holds raw packets captured under the vendor's own libknobster.dll,
each IN packet paired with the event name that library reported for it, so the
decoder is checked against the vendor's interpretation rather than our own.
"""

import os
import threading
import time

import pytest

import fixgw.cfg as cfg
import fixgw.plugins.knobster as knobster
from fixgw.plugins.knobster import protocol

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "libknobster_trace.txt")

VENDOR_EVENTS = {
    "MINOR_CW": (protocol.OP_CW, protocol.ID_INNER),
    "MINOR_CCW": (protocol.OP_CCW, protocol.ID_INNER),
    "MAJOR_CW": (protocol.OP_CW, protocol.ID_OUTER),
    "MAJOR_CCW": (protocol.OP_CCW, protocol.ID_OUTER),
    "BUTTON_PRESSED": (protocol.OP_PRESS, protocol.ID_BUTTON),
    "BUTTON_RELEASED": (protocol.OP_RELEASE, protocol.ID_BUTTON),
}


def load_trace():
    trace = []
    with open(FIXTURE) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            direction, packet = parts[0], bytes.fromhex(" ".join(parts[1:17]))
            label = parts[17] if len(parts) > 17 else None
            trace.append((direction, packet, label))
    return trace


TRACE = load_trace()
IN_PACKETS = [(p, label) for d, p, label in TRACE if d == "IN"]
OUT_PACKETS = [p for d, p, _ in TRACE if d == "OUT"]


# --- protocol -----------------------------------------------------------


def test_every_vendor_packet_decodes_to_the_vendor_event():
    df = protocol.Deframer()
    decoded = []
    for packet, label in IN_PACKETS:
        frames = df.feed(packet)
        assert len(frames) == 1, (packet.hex(" "), frames)
        if label == "CHANNEL_A":
            assert protocol.is_info(frames[0])
            assert protocol.parse_event(frames[0]) is None
            continue
        decoded.append(protocol.parse_event(frames[0]))
        assert decoded[-1] == VENDOR_EVENTS[label], label
    assert len(decoded) == 36


def test_stale_padding_is_ignored():
    # The info packet's padding is "fb ff 10 3f"; reading past the count byte
    # would start a bogus 16-byte frame and swallow the next real event.
    info = IN_PACKETS[0][0]
    assert info[1 + info[0] :].find(b"\xff") >= 0
    df = protocol.Deframer()
    df.feed(info)
    assert df.buf == b""


def test_vendor_host_stream_is_our_startup_and_config():
    """Deframing the vendor library's own OUT traffic (which splits frames
    across packets) gives hello, 16, INIT, then the repeating CONFIG cycle."""
    df = protocol.Deframer()
    frames = []
    for packet in OUT_PACKETS:
        frames += df.feed(packet)
    assert frames[0] == protocol.HELLO
    assert frames[1] == protocol.START
    assert frames[2 : 2 + len(protocol.INIT)] == protocol.INIT
    rest = frames[2 + len(protocol.INIT) :]
    # The vendor sends the cycle one frame per ~108 ms; it begins mid-cycle
    # straight after INIT, so align on its first full pass.
    start = rest.index(protocol.CONFIG[0])
    assert rest[start : start + len(protocol.CONFIG)] == protocol.CONFIG


def test_outer_ring_enable_frame_is_only_in_init():
    outer = bytes.fromhex("04 01 01 02 02 03 02")
    assert protocol.INIT[-1] == outer
    assert outer not in protocol.CONFIG


def test_frames_to_packets_round_trip():
    payloads = [protocol.START] + protocol.INIT + protocol.CONFIG
    packets = protocol.frames_to_packets(payloads)
    assert all(len(p) == protocol.PACKET_SIZE for p in packets)
    assert all(p[0] <= protocol.PACKET_SIZE - 1 for p in packets)
    df = protocol.Deframer()
    out = []
    for p in packets:
        out += df.feed(p)
    assert out == payloads


def test_hello_packet_matches_vendor_bytes():
    assert protocol.frames_to_packets([protocol.HELLO]) == [OUT_PACKETS[0]]


# --- event mapping ------------------------------------------------------


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def mapper(**config):
    writes = []
    clock = Clock()
    m = knobster.EventMapper(lambda k, v: writes.append((k, v)), config, clock)
    return m, writes, clock


def test_trace_maps_to_pyefis_encoder_writes():
    m, writes, _ = mapper()
    df = protocol.Deframer()
    for packet, _ in IN_PACKETS:
        for frame in df.feed(packet):
            ev = protocol.parse_event(frame)
            if ev:
                m.handle(*ev)
    assert writes.count(("ENC3", -1)) == 13
    assert writes.count(("ENC3", 1)) == 3
    assert writes.count(("ENC4", -1)) == 13
    assert writes.count(("ENC4", 1)) == 3
    assert [w for w in writes if w[0] == "BTN3"] == [
        ("BTN3", True),
        ("BTN3", False),
        ("BTN3", True),
        ("BTN3", False),
    ]
    assert m.events == 36


def test_keys_and_steps_are_configurable():
    m, writes, _ = mapper(
        inner_key="ENC1", outer_key="ENC3", outer_step=10, button_key="BTN5"
    )
    m.handle(protocol.OP_CW, protocol.ID_INNER)
    m.handle(protocol.OP_CCW, protocol.ID_OUTER)
    m.handle(protocol.OP_PRESS, protocol.ID_BUTTON)
    assert writes == [("ENC1", 1), ("ENC3", -10), ("BTN5", True)]


def test_empty_outer_key_drops_outer_ring():
    m, writes, _ = mapper(outer_key="")
    assert not m.handle(protocol.OP_CW, protocol.ID_OUTER)
    assert writes == []


def test_long_press_is_timed_press_to_release():
    m, writes, clock = mapper(long_press_key="BTN4", long_press_time=1.0)
    m.handle(protocol.OP_PRESS, protocol.ID_BUTTON)
    clock.t += 0.4
    m.handle(protocol.OP_RELEASE, protocol.ID_BUTTON)
    assert writes == [("BTN3", True), ("BTN3", False)]
    writes.clear()
    m.handle(protocol.OP_PRESS, protocol.ID_BUTTON)
    clock.t += 1.4
    m.handle(protocol.OP_RELEASE, protocol.ID_BUTTON)
    assert writes == [("BTN3", True), ("BTN3", False), ("BTN4", True), ("BTN4", False)]


def test_long_press_off_by_default():
    m, writes, clock = mapper()
    m.handle(protocol.OP_PRESS, protocol.ID_BUTTON)
    clock.t += 5
    m.handle(protocol.OP_RELEASE, protocol.ID_BUTTON)
    assert writes == [("BTN3", True), ("BTN3", False)]


def test_unplug_mid_press_releases_button_without_long_press():
    m, writes, clock = mapper(long_press_key="BTN4")
    m.handle(protocol.OP_PRESS, protocol.ID_BUTTON)
    clock.t += 3
    m.disconnected()
    assert writes == [("BTN3", True), ("BTN3", False)]
    m.disconnected()
    assert len(writes) == 2


def test_unknown_frames_are_ignored():
    m, writes, _ = mapper()
    assert not m.handle(protocol.OP_CW, 0x07)
    assert not m.handle(protocol.OP_PRESS, 0x00)
    assert protocol.parse_event(b"\x0b\x00\x00") is None
    assert writes == []


# --- device session and reconnect ---------------------------------------


class FakeKnobster:
    """Replays the vendor trace once the plugin has sent hello. Raises
    TransportError (an unplug) after `unplug_after` events, if given."""

    def __init__(self, unplug_after=None, answer_hello=True):
        self.sent = []
        self.unplug_after = unplug_after
        self.answer_hello = answer_hello
        self.queue = []
        self.closed = False
        self.lock = threading.Lock()

    def frames_sent(self):
        df = protocol.Deframer()
        out = []
        with self.lock:
            for p in self.sent:
                out += df.feed(p)
        return out

    def write(self, packet):
        assert len(packet) == protocol.PACKET_SIZE
        with self.lock:
            self.sent.append(packet)
            first = len(self.sent) == 1
        if first and self.answer_hello:
            self.queue = [p for p, _ in IN_PACKETS]

    def read(self, timeout_ms):
        if self.unplug_after is not None and self.unplug_after <= 0:
            raise knobster.TransportError("No such device (it may have been disconnected)")
        if not self.queue:
            time.sleep(timeout_ms / 1000.0)
            return None
        packet = self.queue.pop(0)
        if self.unplug_after is not None and packet[3] != protocol.INFO_TYPE:
            self.unplug_after -= 1
        return packet

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, devices):
        self.devices = list(devices)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.devices.pop(0) if self.devices else None


def wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def make_plugin(database):
    plugins = []

    def make(opener, extra=""):
        config = cfg.from_yaml(
            "load: yes\nmodule: fixgw.plugins.knobster\n"
            "reconnect_interval: 0.05\nconfig_interval: 0.1\nhello_retry: 0.1\n"
            + extra
        )
        pl = knobster.Plugin("knobster", config, None)
        pl.thread.opener = opener
        writes = []
        for key in ("ENC3", "ENC4", "BTN3"):
            database.callback_add(
                "test", key, lambda k, v, u: writes.append((k, v[0])), None
            )
        plugins.append(pl)
        return pl, writes

    yield make
    for pl in plugins:
        pl.stop()


def test_session_initializes_and_writes_fix_database(make_plugin, database):
    dev = FakeKnobster()
    pl, writes = make_plugin(Opener([dev]))
    pl.run()
    assert wait_for(lambda: pl.get_status()["Events"] == 36)
    frames = dev.frames_sent()
    assert frames[0] == protocol.HELLO
    assert frames[1] == protocol.START
    assert frames[2 : 2 + len(protocol.INIT)] == protocol.INIT
    # The config cycle keeps going after INIT.
    assert wait_for(
        lambda: dev.frames_sent()[2 + len(protocol.INIT) :][: len(protocol.CONFIG)]
        == protocol.CONFIG
    )
    assert writes.count(("ENC3", -1)) == 13
    assert writes.count(("ENC4", 1)) == 3
    assert database.read("BTN3")[0] is False
    status = pl.get_status()
    assert status["Connected"] and status["Initialized"]


def test_silent_device_is_initialized_after_hello_retries(make_plugin):
    dev = FakeKnobster(answer_hello=False)
    pl, _ = make_plugin(Opener([dev]), "hello_attempts: 2\n")
    pl.run()
    assert wait_for(lambda: protocol.INIT[-1] in dev.frames_sent())
    frames = dev.frames_sent()
    assert frames[:2] == [protocol.HELLO] * 2
    assert frames[2] == protocol.START


def test_survives_unplug_and_replug(make_plugin, database):
    # Unplug while the button is held (the trace's first press is event 33),
    # then nothing attached for a while, then the device comes back.
    first = FakeKnobster(unplug_after=33)
    second = FakeKnobster()
    devices = [None, first, None, None, second]
    opener = Opener(devices)
    pl, writes = make_plugin(opener)
    pl.run()
    assert wait_for(lambda: pl.get_status()["Connections"] == 2)
    assert first.closed
    assert "disconnected" in pl.get_status()["Last error"]
    # The held button was released when the device vanished.
    assert writes[32:34] == [("BTN3", True), ("BTN3", False)]
    assert wait_for(lambda: pl.get_status()["Events"] == 33 + 36)
    # The new session ran the full startup again, outer-ring frame included.
    assert protocol.INIT[-1] in second.frames_sent()
    assert database.read("BTN3")[0] is False


def test_missing_pyusb_or_permission_is_reported_and_retried(make_plugin):
    calls = []

    def opener():
        calls.append(1)
        if len(calls) < 3:
            raise knobster.TransportError("permission denied opening the Knobster")
        return FakeKnobster()

    pl, _ = make_plugin(opener)
    pl.run()
    assert wait_for(lambda: pl.get_status()["Connected"])
    assert "permission denied" in pl.get_status()["Last error"]


def test_stop_is_prompt_while_waiting_for_device(make_plugin):
    pl, _ = make_plugin(Opener([]), "reconnect_interval: 30\n")
    pl.run()
    time.sleep(0.05)
    t0 = time.time()
    pl.stop()
    assert time.time() - t0 < 1.0
    assert not pl.thread.is_alive()
