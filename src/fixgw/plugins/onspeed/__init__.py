#  Copyright (c) 2026 MakerPlane
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

#  Reads the OnSpeed Gen2 AOA computer's 10 Hz ONSPEED serial sentence and
#  publishes AOA and AOA.Warn.  Listen-only.  Every other field is decoded
#  for get_status() and not written: one key, one writer.  The behaviour is
#  pyEfis docs/fixgw_onspeed_in_spec.md; section numbers below refer to it.

import dataclasses
import threading
import time
from collections import OrderedDict

import serial

import fixgw.plugin as plugin
from fixgw.plugins.onspeed import sentence

FIRMWARE_BAUD = 115200
DEFAULT_MIN_IAS = 30.0  # kt; firmware MUTE_AUDIO_UNDER_IAS default (5.1)
READ_TIMEOUT = 0.5  # s; stop() joins for longer than this
MAX_LINE = 128  # bytes; a longer line is discarded unparsed (6.1)
BACKOFF_START = 1.0  # s, doubling on each failed open
BACKOFF_MAX = 10.0

# CONFIGPATH is added to every plugin's config by the server.
_KNOWN_CONFIG = {"port", "baud", "min_ias", "CONFIGPATH"}

_COUNTERS = (
    ("Lines Received", "lines"),
    ("Frames Decoded", "frames"),
    ("Length Errors", "length"),
    ("Checksum Errors", "checksum"),
    ("Grammar Errors", "grammar"),
    ("Wrong Format", "wrong_format"),
    ("Encoding Errors", "encoding"),
    ("Overlong Lines", "overlong"),
    ("DataMark Overflow Frames", "datamark_overflow"),
    ("Setpoint Order Errors", "setpoint_order_errors"),
    ("Low-IAS Frames", "low_ias"),
)

# Marks "AOA.Warn never written by this run", distinct from a written None.
_UNWRITTEN = object()


def warn_setpoint(frame):
    """AOA.Warn for a frame (5.3): degrees, or None when the box is
    uncalibrated or its setpoints are out of order.  Returns (value, ok)
    where ok is False only for an order violation."""
    warn = frame.stall_warn_aoa
    if warn == 0:
        return None, True
    if not (
        frame.tones_on_aoa
        < frame.onspeed_fast_aoa
        <= frame.onspeed_slow_aoa
        <= warn
    ):
        return None, False
    return warn, True


class MainThread(threading.Thread):
    def __init__(self, parent):
        super().__init__(daemon=True)
        self.getout = False
        self.parent = parent
        self.log = parent.log
        self._wake = threading.Event()
        self._port = None
        self._backoff = BACKOFF_START
        self._buffer = bytearray()
        # Drop bytes up to the next newline: set on open (the first line is
        # likely partial) and after an overlong line.
        self._skip_to_newline = True
        self._last_warn = _UNWRITTEN
        self._warned = set()
        self.min_ias = float(parent.config.get("min_ias", DEFAULT_MIN_IAS))
        self.counters = {key: 0 for _, key in _COUNTERS}
        self.last_frame = None
        self.last_frame_time = None

    # -- port ----------------------------------------------------------

    def _open(self):
        try:
            self._port = serial.Serial(
                self.parent.config["port"],
                baudrate=int(self.parent.config.get("baud", FIRMWARE_BAUD)),
                timeout=READ_TIMEOUT,
            )
        except (serial.SerialException, OSError) as exc:
            self._port_failed("cannot open {}: {}".format(self.parent.config["port"], exc))
            return False
        self.log.info("Opened {}".format(self.parent.config["port"]))
        self._backoff = BACKOFF_START
        self._buffer = bytearray()
        self._skip_to_newline = True
        return True

    def _close(self):
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None

    def _port_failed(self, why):
        # fail goes on AOA without a value write, so the key also ages to
        # old.  The first good frame's write clears it (5.4).
        self._once("port", "Serial port: {}".format(why), error=True)
        self._close()
        self.parent.db_get_item("AOA").fail = True

    @property
    def port_open(self):
        return self._port is not None

    # -- loop ----------------------------------------------------------

    def run(self):
        while not self.getout:
            self.step()
        self._close()

    def step(self):
        """One pass of the loop: open (with backoff) or read once."""
        if self._port is None:
            if not self._open():
                self._wake.wait(self._backoff)
                self._backoff = min(self._backoff * 2, BACKOFF_MAX)
            return
        try:
            data = self._port.read(self._port.in_waiting or 1)
        except (serial.SerialException, OSError) as exc:
            self._port_failed(exc)
            return
        self.feed(data)

    def stop(self):
        self.getout = True
        self._wake.set()

    def feed(self, data):
        """Line assembly (6.1): split on LF, strip CR, bound the buffer."""
        for b in data:
            if b == 0x0A:
                if self._skip_to_newline:
                    self._skip_to_newline = False
                else:
                    self._line(bytes(self._buffer).rstrip(b"\r"))
                self._buffer = bytearray()
            elif not self._skip_to_newline:
                self._buffer.append(b)
                if len(self._buffer) > MAX_LINE:
                    self.counters["overlong"] += 1
                    self._once("overlong", "Line over {} bytes discarded".format(MAX_LINE))
                    self._buffer = bytearray()
                    self._skip_to_newline = True

    def _line(self, line):
        self.counters["lines"] += 1
        try:
            frame = sentence.parse(line)
        except sentence.SentenceError as exc:
            self.counters[exc.reason] += 1
            if exc.reason == "wrong_format":
                self._once(
                    exc.reason,
                    "OnSpeed is set to G3X output; set SERIALOUTFORMAT to ONSPEED",
                )
            else:
                self._once(exc.reason, "Dropped sentence: {}".format(exc))
            return
        try:
            self._publish(frame)
        except Exception as exc:
            self._once("publish", "Error publishing frame: {!r}".format(exc))

    def _publish(self, frame):
        self.counters["frames"] += 1
        if frame.datamark_overflow:
            self.counters["datamark_overflow"] += 1
        # Below its mute airspeed the box sends AOA +000.  A zero reads as
        # far from stall, so it is published bad, never good (5.1).  Above
        # min_ias a zero is a real AOA (5.2).
        low = frame.ias < self.min_ias
        if low:
            self.counters["low_ias"] += 1
        self.parent.db_write("AOA", (frame.aoa, False, low, False))

        warn, ok = warn_setpoint(frame)
        if not ok:
            self.counters["setpoint_order_errors"] += 1
            self._once(
                "setpoint_order",
                "OnSpeed setpoints out of order (tones-on {} fast {} slow {} "
                "warn {}); AOA.Warn cleared".format(
                    frame.tones_on_aoa,
                    frame.onspeed_fast_aoa,
                    frame.onspeed_slow_aoa,
                    frame.stall_warn_aoa,
                ),
            )
        # Aux writes fire every callback, so write on change only (5.3).
        # Clear with the string "None": float(None) raises TypeError.
        if warn != self._last_warn:
            self.parent.db_write("AOA.Warn", "None" if warn is None else warn)
            self._last_warn = warn

        self.last_frame = frame
        self.last_frame_time = time.monotonic()

    def _once(self, reason, message, error=False):
        if reason in self._warned:
            self.log.debug(message)
        else:
            self._warned.add(reason)
            (self.log.error if error else self.log.warning)(message)


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)
        for key in sorted(set(config) - _KNOWN_CONFIG):
            self.log.warning("Unknown configuration option '{}' ignored".format(key))
        baud = int(config.get("baud", FIRMWARE_BAUD))
        if baud != FIRMWARE_BAUD:
            self.log.warning(
                "baud {} configured; the OnSpeed firmware only sends at {}".format(
                    baud, FIRMWARE_BAUD
                )
            )
        self.thread = MainThread(self)

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(1.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        t = self.thread
        status = OrderedDict()
        status["Port"] = self.config.get("port")
        status["Port Open"] = t.port_open
        for label, key in _COUNTERS:
            status[label] = t.counters[key]
        status["Last Frame Age (s)"] = (
            None
            if t.last_frame_time is None
            else round(time.monotonic() - t.last_frame_time, 1)
        )
        status["Last Frame"] = (
            None if t.last_frame is None else dataclasses.asdict(t.last_frame)
        )
        return status
