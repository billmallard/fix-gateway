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

"""Host for a Tessera touch panel (TESS1 spike, AER-5754).

The panel is a GUITION JC1060P470C running the open Tessera "Stream Deck"
firmware.  It holds no actions: this plugin pushes one page of text keys
built from FIX values and turns each press into the FIX writes the page file
names.  The protocol is in protocol.py; the page in page.py; the behaviour in
doc/plugins/tessera.md.

One thread owns the serial port.  It handshakes, pings every second, pushes
the page when a shown value changes (waiting for each push's answer, with one
resend), answers presses, and reconnects after an unplug, a refused or
silent handshake, or a panel that stops answering.
"""

import os
import threading
import time
from collections import OrderedDict

import serial

import fixgw.plugin as plugin
from . import protocol
from .page import Page, PageError

# CONFIGPATH is added to every plugin's config by the server.
_KNOWN_CONFIG = {
    "port",
    "page",
    "min_refresh",
    "identify_interval",
    "reconnect_interval",
    "CONFIGPATH",
}
READ_TIMEOUT = 0.05  # s; the loop's tick
RX_MAX = 32768  # bytes buffered without a newline before the link is reset


class LinkError(Exception):
    """The panel went away or stopped answering; the session must reconnect."""


class SerialLink:
    """Line framing over one open serial port."""

    def __init__(self, port):
        self.port = port
        self.rx = bytearray()

    @classmethod
    def open(cls, path):
        try:
            port = serial.Serial(path, baudrate=protocol.BAUD, timeout=READ_TIMEOUT)
        except (serial.SerialException, OSError) as exc:
            raise LinkError("cannot open {}: {}".format(path, exc)) from exc
        return cls(port)

    def send(self, line):
        raw = (line + "\n").encode("utf-8")
        try:
            if self.port.write(raw) != len(raw):
                raise LinkError("incomplete write")
        except (serial.SerialException, OSError) as exc:
            raise LinkError("write failed: {}".format(exc)) from exc

    def lines(self):
        try:
            self.rx.extend(self.port.read(self.port.in_waiting or 1))
        except (serial.SerialException, OSError) as exc:
            raise LinkError("read failed: {}".format(exc)) from exc
        if len(self.rx) > RX_MAX:
            self.rx.clear()
            raise LinkError("no newline in {} bytes".format(RX_MAX))
        result = []
        while (end := self.rx.find(b"\n")) >= 0:
            result.append(bytes(self.rx[:end]).decode("utf-8", "replace").strip())
            del self.rx[: end + 1]
        return result

    def discard_input(self):
        try:
            self.port.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            raise LinkError("reset failed: {}".format(exc)) from exc
        self.rx.clear()

    def close(self):
        try:
            self.port.close()
        except Exception:
            pass


class MainThread(threading.Thread):
    # The protocol's timings, as attributes so tests can shorten them.
    ping_interval = protocol.PING_INTERVAL
    ack_seconds = protocol.CFG_ACK_SECONDS
    handshake_seconds = protocol.HANDSHAKE_SECONDS

    def __init__(self, parent, page, opener=SerialLink.open, clock=time.monotonic):
        super().__init__(daemon=True)
        self.parent = parent
        self.log = parent.log
        self.page = page
        self.opener = opener
        self.clock = clock
        cfg = parent.config
        self.port_path = cfg.get("port")
        self.min_refresh = float(cfg.get("min_refresh", 0.1))
        self.identify_interval = float(cfg.get("identify_interval", 10.0))
        self.reconnect_interval = float(cfg.get("reconnect_interval", 2.0))
        self.stopping = threading.Event()
        self.dirty = threading.Event()
        self.connected = False
        self.info = {}
        self.boot_id = None
        self.last_error = ""
        self.counters = OrderedDict(
            (name, 0)
            for name in (
                "connects",
                "refused",
                "pushes",
                "acks",
                "resends",
                "invalid",
                "too_big",
                "presses",
                "alerts",
                "pings",
                "restarts",
            )
        )
        self._warned = set()
        self.reset_session()

    # -- loop ----------------------------------------------------------

    def run(self):
        while not self.stopping.is_set():
            link = None
            try:
                link = self.opener(self.port_path)
                self.session(link)
            except (LinkError, protocol.Rejected) as exc:
                if isinstance(exc, protocol.Rejected):
                    self.counters["refused"] += 1
                self.last_error = str(exc)
                self._once(str(exc), "Tessera: {}".format(exc))
            finally:
                self.connected = False
                if link is not None:
                    link.close()
            self.stopping.wait(self.reconnect_interval)

    def stop(self):
        self.stopping.set()

    def handshake(self, link):
        """Ask the device who it is.  Press events that arrive before the
        answer belong to an earlier session and are dropped unanswered."""
        link.discard_input()
        link.send(protocol.GET_INFO)
        deadline = self.clock() + self.handshake_seconds
        while self.clock() < deadline and not self.stopping.is_set():
            for line in link.lines():
                message = protocol.parse_line(line)
                if message and message.get("type") == "info":
                    protocol.check_info(message)
                    return message
        raise LinkError(
            "{} did not identify itself as a Tessera panel".format(self.port_path)
        )

    def session(self, link):
        info = self.handshake(link)
        self.info = info
        self._note_boot(info)
        self.connected = True
        self.counters["connects"] += 1
        self._warned.clear()
        self.log.info(
            "Tessera panel on {}: firmware {}, boot {}".format(
                self.port_path, info.get("firmwareVersion"), info.get("bootId")
            )
        )
        self.reset_session()
        self.dirty.set()
        while not self.stopping.is_set():
            for line in link.lines():
                self.handle(link, line)
            now = self.clock()
            if now - self.last_ping >= self.ping_interval:
                link.send(protocol.PING)
                self.counters["pings"] += 1
                self.last_ping = now
            if self.ack_due is not None and now >= self.ack_due:
                if self.attempts >= 2:
                    raise LinkError("the panel did not answer the page twice")
                self.log.warning(
                    "Tessera: no answer to the page in {} s; sending it once more".format(
                        self.ack_seconds
                    )
                )
                self.counters["resends"] += 1
                self.send_page(link, self.sent)
            self.identify(link, now)
            if (
                self.dirty.is_set()
                and self.ack_due is None
                and not self.rejected
                and now - self.last_push >= self.min_refresh
            ):
                self.dirty.clear()
                payload = protocol.layout_payload(
                    self.page.buttons(self.parent.db_read)
                )
                if payload != self.acked:
                    if len(payload) > protocol.LAYOUT_MAX:
                        self.rejected = True
                        self.log.error(
                            "Tessera: the page is {} bytes; the panel takes {}".format(
                                len(payload), protocol.LAYOUT_MAX
                            )
                        )
                    else:
                        self.attempts = 0
                        self.send_page(link, payload)

    def reset_session(self):
        self.acked = None  # the payload the panel accepted this session
        self.sent = None  # the payload awaiting an answer
        self.ack_due = None
        self.attempts = 0
        self.rejected = False
        self.last_push = float("-inf")
        self.last_ping = float("-inf")
        self.identify_due = self.clock() + self.identify_interval
        self.identify_deadline = None
        self.identify_misses = 0

    def send_page(self, link, payload):
        for line in protocol.layout_lines(payload):
            link.send(line)
        self.sent = payload
        self.attempts += 1
        self.counters["pushes"] += 1
        self.last_push = self.clock()
        self.ack_due = self.last_push + self.ack_seconds

    def identify(self, link, now):
        """Ask get_info every identify_interval: a new bootId means the panel
        restarted behind an open port, and two unanswered asks mean it hung."""
        if self.identify_deadline is not None and now >= self.identify_deadline:
            self.identify_deadline = None
            self.identify_misses += 1
            if self.identify_misses >= 2:
                raise LinkError("the panel stopped answering get_info")
        if self.identify_deadline is None and now >= self.identify_due:
            link.send(protocol.GET_INFO)
            self.identify_due = now + self.identify_interval
            self.identify_deadline = now + self.handshake_seconds

    # -- lines from the panel -------------------------------------------

    def handle(self, link, line):
        if line.startswith(protocol.CFG_END + " "):
            self.cfg_answer(line)
            return
        message = protocol.parse_line(line)
        if not message:
            return
        kind = message.get("type")
        if kind == "deck_event" and type(message.get("button")) is int:
            self.press(link, message["button"], message.get("hold") is True)
        elif kind == "info":
            self.identify_deadline = None
            self.identify_misses = 0
            if message.get("bootId") != self.boot_id:
                self._note_boot(message)
                # The restarted panel shows the layout it saved, not ours:
                # presses wait until it accepts the page again.
                self.acked = None
                self.dirty.set()

    def cfg_answer(self, line):
        if self.ack_due is None:
            return  # a late answer to a push already resent and answered
        self.ack_due = None
        answer = line[len(protocol.CFG_END) + 1 :]
        if answer.startswith("OK"):
            self.acked = self.sent
            self.counters["acks"] += 1
            return
        # INVALID and TOO_BIG are answers about the page, so sending it again
        # would get the same answer.  Stop pushing until the next connection.
        self.rejected = True
        if answer.startswith("TOO_BIG"):
            self.counters["too_big"] += 1
        else:
            self.counters["invalid"] += 1
        self.log.error("Tessera: the panel refused the page: {}".format(answer))

    def press(self, link, button, hold):
        self.counters["presses"] += 1
        ok = False
        if self.acked is None:
            # The panel is showing a layout we did not give it this session.
            self._once(
                "unacked",
                "Tessera: press on key {} before the panel accepted the page; ignored".format(
                    button
                ),
            )
        elif hold:
            self._once(
                "hold",
                "Tessera: long press on key {}; the page has no long-press actions".format(
                    button
                ),
            )
        else:
            writes = self.page.writes(button)
            try:
                for key, value in writes:
                    self.parent.db_write(key, value)
                ok = bool(writes)
            except Exception as exc:
                self.log.error(
                    "Tessera: press on key {} failed: {!r}".format(button, exc)
                )
        if not ok:
            self.counters["alerts"] += 1
        link.send(protocol.feedback(button, ok))

    # -- helpers -------------------------------------------------------

    def _note_boot(self, info):
        boot = info.get("bootId")
        if self.boot_id is not None and boot != self.boot_id:
            self.counters["restarts"] += 1
            reason = info.get("reset")
            message = "Tessera panel restarted (reset: {})".format(reason)
            if reason in protocol.CRASH_RESETS:
                self.log.warning(message)
            else:
                self.log.info(message)
        self.boot_id = boot

    def _once(self, reason, message):
        if reason in self._warned:
            self.log.debug(message)
        else:
            self._warned.add(reason)
            self.log.warning(message)

    def mark_dirty(self, *_args):
        self.dirty.set()


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super().__init__(name, config, config_meta)
        for key in sorted(set(config) - _KNOWN_CONFIG):
            self.log.warning("Unknown configuration option '{}' ignored".format(key))
        path = config.get("page", "tessera/page.yaml")
        if not os.path.isabs(path) and config.get("CONFIGPATH"):
            path = os.path.join(config["CONFIGPATH"], path)
        try:
            self.page = Page.load(path)
            for key in self.page.named_keys():
                self.db_get_item(
                    key
                )  # KeyError: the page names a key the database lacks
            for key in self.page.pulsed_keys():
                if self.db_get_item(key).typestring != "bool":
                    raise PageError("pulse key {} is not a bool".format(key))
        except (OSError, PageError, KeyError) as exc:
            self.log.error("Tessera page {}: {!r}".format(path, exc))
            raise plugin.PluginFail from exc
        self.thread = MainThread(self, self.page)

    def run(self):
        for key in self.page.shown_keys():
            self.db_callback_add(key, self.thread.mark_dirty)
        self.thread.start()

    def stop(self):
        for key in self.page.shown_keys():
            self.db_callback_del(key, self.thread.mark_dirty)
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(1.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        t = self.thread
        status = OrderedDict()
        status["Port"] = t.port_path
        status["Connected"] = t.connected
        status["Firmware"] = t.info.get("firmwareVersion")
        status["Boot Id"] = t.boot_id
        for name, value in t.counters.items():
            status[name.replace("_", " ").title()] = value
        status["Last Error"] = t.last_error
        return status
