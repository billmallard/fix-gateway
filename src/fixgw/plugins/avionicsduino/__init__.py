#  Copyright (c) 2026 MakerPlane contributors
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

# AvionicsDuino input bridge.  A listen-only plugin that decodes the
# AvionicsDuino modules' own CAN messages (AHRS, wingtip remote module with
# the magnetometer, RDAM air data and engine module) and writes mapped
# values into the FIX database.  It never transmits.
#
# Their ids (10-60, 11-bit) sit in CAN-FIX's node-alarm range, so this
# plugin needs its own CAN interface: never the canfix plugin's bus.

import threading
import time
from collections import OrderedDict

import can

import fixgw.plugin as plugin

from . import mapping
from . import protocol

# Every config option the plugin reads; anything else draws a warning.
# CONFIGPATH is added by the server (server.load_plugin).
KNOWN_OPTIONS = {
    "interface",
    "channel",
    "bitrate",
    "mapfile",
    "rdam_altitude",
    "rdam_vertical_speed",
    "smoothing_tau",
    "CONFIGPATH",
}


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__()
        self.getout = False
        self.parent = parent
        self.log = parent.log
        self.mapping = parent.mapping
        self._last_count = None
        self._warned = set()

    def _warn(self, canid, message):
        if canid in self._warned:
            self.log.debug(message)
        else:
            self._warned.add(canid)
            self.log.warning(message)

    def run(self):
        while True:
            try:
                msg = self.parent.bus.recv(1.0)
                if msg is not None:
                    self.parent.recvcount += 1
                    # Never let a bad frame kill the thread.
                    try:
                        self.process(msg)
                    except Exception as e:
                        self.parent.recvinvalidcount += 1
                        self._warn(
                            getattr(msg, "arbitration_id", -1),
                            "Unexpected failure processing frame id {}: {}".format(
                                getattr(msg, "arbitration_id", "?"), e
                            ),
                        )
            finally:
                if self.getout:
                    break

    def process(self, msg):
        canid = msg.arbitration_id
        if (
            getattr(msg, "is_extended_id", False)
            or getattr(msg, "is_error_frame", False)
            or getattr(msg, "is_remote_frame", False)
            or canid not in protocol.LAYOUTS
        ):
            self.parent.recvignorecount += 1
            return
        try:
            values = protocol.decode(canid, msg.data)
        except protocol.DecodeError as e:
            self.parent.recvinvalidcount += 1
            self._warn(canid, str(e))
            return
        self.parent.note_node(protocol.LAYOUTS[canid].node)

        if canid == 10:
            # The AHRS counter steps by one per 20 Hz cycle: a gap is lost
            # frames.  A telltale only, never a gate.
            count = values["count"]
            if self._last_count is not None and count != self._last_count + 1:
                self.parent.seqgapcount += 1
            self._last_count = count

        if canid in self.mapping.interesting:
            self.mapping.inputMap(canid, values, time.monotonic())

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta=None):
        super(Plugin, self).__init__(name, config, config_meta)
        for key in config:
            if key not in KNOWN_OPTIONS:
                self.log.warning("Unknown config option '{}' ignored".format(key))
        self.interface = config["interface"]
        self.channel = config["channel"]
        self.bitrate = config.get("bitrate")
        options = {
            "rdam_altitude": bool(config.get("rdam_altitude", False)),
            "rdam_vertical_speed": bool(config.get("rdam_vertical_speed", False)),
        }
        mapfilename = config["mapfile"].format(CONFIG=config.get("CONFIGPATH", ""))
        self.mapping = mapping.Mapping(
            mapfilename,
            self.log,
            options=options,
            smoothing_tau=config.get("smoothing_tau", 0.0),
        )
        self.bus = None
        self.thread = MainThread(self)
        self.recvcount = 0
        self.recvignorecount = 0
        self.recvinvalidcount = 0
        self.seqgapcount = 0
        self.nodes_seen = {}

    def note_node(self, node):
        d = self.nodes_seen.get(node)
        if d is None:
            self.nodes_seen[node] = {"frames": 1, "last_seen": time.monotonic()}
        else:
            d["frames"] += 1
            d["last_seen"] = time.monotonic()

    def run(self):
        kwargs = {}
        if self.bitrate is not None:
            kwargs["bitrate"] = self.bitrate
        self.bus = can.ThreadSafeBus(self.channel, interface=self.interface, **kwargs)
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            try:
                # Must wait longer than the bus read in the main loop,
                # self.bus.recv(1.0)
                self.thread.join(1.2)
            except Exception:
                pass
        if self.thread.is_alive():
            raise plugin.PluginFail
        if self.bus is not None:
            self.bus.shutdown()
            self.bus = None

    def get_status(self):
        x = OrderedDict()
        x["CAN Interface"] = self.interface
        x["CAN Channel"] = self.channel
        x["Received Frames"] = self.recvcount
        x["Decoded Writes"] = self.mapping.write_count
        x["Ignored Frames"] = self.recvignorecount
        x["Invalid Frames"] = self.recvinvalidcount
        x["Non-finite Values"] = self.mapping.nonfinite_count
        x["Rejected Values"] = self.mapping.invalid_count
        x["AHRS Counter Gaps"] = self.seqgapcount
        x["RDAM Altitude"] = "on" if self.mapping.options["rdam_altitude"] else "off"
        x["RDAM Vertical Speed"] = (
            "on" if self.mapping.options["rdam_vertical_speed"] else "off"
        )
        x["Airspeed Smoothing"] = (
            "{} s".format(self.mapping.smoothing_tau)
            if self.mapping.smoothing_tau > 0
            else "off"
        )
        now = time.monotonic()
        x["Nodes Seen"] = {
            node: {
                "frames": d["frames"],
                "last_seen_age_s": round(now - d["last_seen"], 1),
            }
            for node, d in list(self.nodes_seen.items())
        }
        return x
