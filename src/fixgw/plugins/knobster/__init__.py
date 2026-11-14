#!/usr/bin/env python

#  Copyright (c) 2026 Bill Mallard
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
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

"""FIX-Gateway input plugin for the Sim Innovations Knobster.

The Knobster is a dual concentric encoder with a push button on a vendor USB
interface (16d0:0e8a). On Linux no kernel driver binds to it, so it produces no
evdev or hidraw events; it stays silent until a host runs the startup sequence
in protocol.py and keeps re-sending the config cycle.

Each detent writes a signed step to an encoder key, matching how pyEfis reads
its ``encoder`` key (the value of each write is a delta, not a position) and
how the demo and compute plugins drive ENC keys. The button writes True/False
to a bool key. Long press is measured host-side, press to release, because the
device has no long-press event.

pyusb is imported only when the device is opened, so the plugin, and its tests,
load without it.
"""

import errno
import threading
import time
from collections import OrderedDict

import fixgw.plugin as plugin
from . import protocol


class TransportError(Exception):
    """The device went away or cannot be used; the session must reconnect."""


class UsbTransport:
    """pyusb access to one Knobster. read() returns None on a timeout."""

    def __init__(self, dev, usb):
        self.dev = dev
        self.usb = usb

    @classmethod
    def open(cls):
        """Return a claimed transport, or None if no Knobster is attached."""
        try:
            import usb.core
            import usb.util
        except ImportError as e:
            raise TransportError("pyusb is not installed (pip install pyusb)") from e
        try:
            dev = usb.core.find(idVendor=protocol.VID, idProduct=protocol.PID)
        except usb.core.NoBackendError as e:
            raise TransportError("no libusb backend (install libusb-1.0)") from e
        if dev is None:
            return None
        try:
            try:
                if dev.is_kernel_driver_active(protocol.INTERFACE):
                    dev.detach_kernel_driver(protocol.INTERFACE)
            except (NotImplementedError, usb.core.USBError):
                pass
            dev.set_configuration()
            usb.util.claim_interface(dev, protocol.INTERFACE)
        except usb.core.USBError as e:
            usb.util.dispose_resources(dev)
            if e.errno == errno.EACCES:
                raise TransportError(
                    "permission denied opening the Knobster; install "
                    "extras/udev/70-knobster.rules"
                ) from e
            raise TransportError(f"cannot claim the Knobster: {e}") from e
        return cls(dev, usb)

    def write(self, packet):
        try:
            self.dev.write(protocol.EP_OUT, packet, timeout=500)
        except self.usb.core.USBError as e:
            raise TransportError(f"write failed: {e}") from e

    def read(self, timeout_ms):
        try:
            return bytes(
                self.dev.read(protocol.EP_IN, protocol.PACKET_SIZE, timeout=timeout_ms)
            )
        except self.usb.core.USBTimeoutError:
            return None
        except self.usb.core.USBError as e:
            raise TransportError(f"read failed: {e}") from e

    def close(self):
        try:
            self.usb.util.release_interface(self.dev, protocol.INTERFACE)
        except Exception:
            pass
        try:
            self.usb.util.dispose_resources(self.dev)
        except Exception:
            pass


class EventMapper:
    """Turns Knobster events into FIX writes."""

    def __init__(self, write, config, clock=time.monotonic):
        self.write = write
        self.clock = clock
        self.inner_key = config.get("inner_key", "ENC3")
        self.inner_step = int(config.get("inner_step", 1))
        self.outer_key = config.get("outer_key", "ENC4")
        self.outer_step = int(config.get("outer_step", 1))
        self.button_key = config.get("button_key", "BTN3")
        self.long_press_key = config.get("long_press_key") or None
        self.long_press_time = float(config.get("long_press_time", 1.0))
        self.pressed_at = None
        self.events = 0

    def handle(self, op, ident):
        if op in (protocol.OP_CW, protocol.OP_CCW):
            if ident == protocol.ID_INNER:
                key, step = self.inner_key, self.inner_step
            elif ident == protocol.ID_OUTER:
                key, step = self.outer_key, self.outer_step
            else:
                return False
            if not key:
                return False
            self.events += 1
            self.write(key, step if op == protocol.OP_CW else -step)
            return True
        if ident != protocol.ID_BUTTON:
            return False
        if op == protocol.OP_PRESS:
            self.events += 1
            self.pressed_at = self.clock()
            if self.button_key:
                self.write(self.button_key, True)
            return True
        self.events += 1
        self.release()
        return True

    def release(self):
        held = None if self.pressed_at is None else self.clock() - self.pressed_at
        self.pressed_at = None
        if self.button_key:
            self.write(self.button_key, False)
        if self.long_press_key and held is not None and held >= self.long_press_time:
            self.write(self.long_press_key, True)
            self.write(self.long_press_key, False)

    def disconnected(self):
        """Never leave the button stuck down when the device vanishes
        mid-press. This is not a long press, so it does not fire one."""
        if self.pressed_at is not None:
            self.pressed_at = None
            if self.button_key:
                self.write(self.button_key, False)


class MainThread(threading.Thread):
    def __init__(self, parent, opener=UsbTransport.open, clock=time.monotonic):
        super(MainThread, self).__init__(daemon=True)
        self.parent = parent
        self.log = parent.log
        self.opener = opener
        self.clock = clock
        cfg = parent.config
        self.reconnect_interval = float(cfg.get("reconnect_interval", 2.0))
        self.config_interval = float(
            cfg.get("config_interval", protocol.CONFIG_INTERVAL)
        )
        self.hello_retry = float(cfg.get("hello_retry", 1.0))
        self.hello_attempts = int(cfg.get("hello_attempts", 3))
        self.mapper = EventMapper(parent.db_write, cfg, clock)
        self.deframer = protocol.Deframer()
        self.stopping = threading.Event()
        self.connected = False
        self.started = False
        self.sessions = 0
        self.last_error = ""

    def run(self):
        reported = None
        while not self.stopping.is_set():
            try:
                transport = self.opener()
            except TransportError as e:
                transport = None
                if str(e) != reported:
                    self.log.error(str(e))
                    reported = str(e)
                self.last_error = str(e)
            if transport is None:
                if reported is None:
                    self.log.info("Knobster not found, waiting for it")
                    reported = "not found"
                self.stopping.wait(self.reconnect_interval)
                continue
            reported = None
            self.sessions += 1
            self.connected = True
            self.log.info("Knobster connected")
            try:
                self.session(transport)
            except TransportError as e:
                self.last_error = str(e)
                self.log.warning(f"Knobster lost: {e}")
            finally:
                self.connected = False
                self.started = False
                self.mapper.disconnected()
                transport.close()
            if not self.stopping.is_set():
                self.stopping.wait(self.reconnect_interval)

    def send(self, transport, payloads):
        for packet in protocol.frames_to_packets(payloads):
            transport.write(packet)

    def initialize(self, transport):
        self.send(transport, [protocol.START])
        self.send(transport, protocol.INIT)
        self.started = True

    def session(self, transport):
        self.deframer.reset()
        self.send(transport, [protocol.HELLO])
        hello_at = self.clock()
        hellos = 1
        next_config = None
        while not self.stopping.is_set():
            packet = transport.read(50)
            if packet:
                for payload in self.deframer.feed(packet):
                    if protocol.is_info(payload):
                        if not self.started:
                            self.log.debug(f"Knobster info {payload.hex(' ')}")
                            self.initialize(transport)
                            next_config = self.clock() + self.config_interval
                        continue
                    event = protocol.parse_event(payload)
                    if event is not None:
                        self.mapper.handle(*event)
            now = self.clock()
            if not self.started and now - hello_at >= self.hello_retry:
                hellos += 1
                if hellos > self.hello_attempts:
                    # Initialize anyway: the config cycle, not the info reply,
                    # is what makes the device report events.
                    self.log.warning("Knobster did not answer hello; initializing")
                    self.initialize(transport)
                    next_config = now + self.config_interval
                else:
                    self.send(transport, [protocol.HELLO])
                    hello_at = now
            if next_config is not None and now >= next_config:
                self.send(transport, protocol.CONFIG)
                next_config = now + self.config_interval

    def stop(self):
        self.stopping.set()


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.thread = MainThread(self)

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            self.thread.join(2.0)
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        return OrderedDict(
            {
                "Connected": self.thread.connected,
                "Initialized": self.thread.started,
                "Connections": self.thread.sessions,
                "Events": self.thread.mapper.events,
                "Last error": self.thread.last_error,
            }
        )
