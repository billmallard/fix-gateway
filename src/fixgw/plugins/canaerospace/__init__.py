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

# CANaerospace input bridge.  A listen-only plugin that decodes
# CANaerospace V1.7 frames (e.g. from a Rotax 912iS/915iS engine ECU) and
# writes mapped values into the FIX database.  CAN-FIX remains the
# system's native bus protocol; CANaerospace is consumed as a federated
# input, never transmitted (not even Node Service replies).

import threading
import time
from collections import OrderedDict

import can

import fixgw.plugin as plugin

from . import mapping
from . import protocol


class MainThread(threading.Thread):
    def __init__(self, parent, config):
        super(MainThread, self).__init__()
        self.getout = False
        self.parent = parent
        self.log = parent.log
        self.mapping = parent.mapping
        self.interesting = self.mapping.interesting
        # last Message Code per (canid, node_id) for bus-health telltale
        self._last_code = {}
        self._warned_canids = set()

    def _warn(self, canid, message):
        if canid in self._warned_canids:
            self.log.debug(message)
        else:
            self._warned_canids.add(canid)
            self.log.warning(message)

    def run(self):
        self.bus = self.parent.bus

        while True:
            try:
                msg = self.bus.recv(1.0)
                if msg is not None:
                    self.parent.recvcount += 1
                    # Never let a bad frame kill the thread.
                    try:
                        self._process(msg)
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

    def _process(self, msg):
        canid = msg.arbitration_id
        # 29-bit identifiers are legal CANaerospace but out of scope (the
        # Rotax bus does not use them), as is anything past the standard
        # 11-bit distribution.
        if getattr(msg, "is_extended_id", False) or canid > protocol.MAX_CANID:
            self.parent.recvignorecount += 1
            return

        channel = protocol.channel_for(canid)
        mapped = self.interesting[canid]

        if channel is protocol.Channel.EED:
            # Emergency Event Data is always surfaced, mapped or not.
            self.parent.emergencyeventcount += 1
            data = bytes(msg.data)
            node_id = data[0] if data else -1
            self.log.warning(
                "Emergency Event: canid={} node={} data={}".format(
                    canid, node_id, data.hex()
                )
            )
            raw_code = int.from_bytes(data[4:8], "big") if len(data) >= 8 else None
            self.mapping.eventMap(canid, raw_code)
            if not mapped:
                return
        elif not mapped:
            # The mapfile is the final authority on which ids are decoded;
            # the channel table only drives default handling for the rest.
            self.parent.recvignorecount += 1
            return

        try:
            cmsg = protocol.parse(canid, msg.data)
        except protocol.CanasUnsupportedType:
            self.parent.unsupportedtypecount += 1
            return
        except protocol.CanasDecodeError:
            self.parent.recvinvalidcount += 1
            return

        self.parent.note_node(cmsg.node_id)

        # Message Code continuity is a telltale only, never a gate.
        key = (cmsg.canid, cmsg.node_id)
        last = self._last_code.get(key)
        if last is not None and cmsg.message_code != (last + 1) % 256:
            self.parent.seqgapcount += 1
        self._last_code[key] = cmsg.message_code

        if cmsg.data_type == protocol.NODATA:
            self.parent.nodatacount += 1
            return

        self.mapping.inputMap(cmsg)

    def stop(self):
        self.getout = True


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.interface = config["interface"]
        self.channel = config["channel"]
        self.node_timeout = float(config.get("node_timeout", 2.0))
        mapfilename = config["mapfile"].format(CONFIG=config["CONFIGPATH"])
        self.mapping = mapping.Mapping(mapfilename, self.log, self.node_timeout)
        self.thread = MainThread(self, config)
        self.recvcount = 0
        self.recvignorecount = 0
        self.recvinvalidcount = 0
        self.unsupportedtypecount = 0
        self.nodatacount = 0
        self.seqgapcount = 0
        self.emergencyeventcount = 0
        self.nodes_seen = {}

    def note_node(self, node_id):
        d = self.nodes_seen.get(node_id)
        if d is None:
            self.nodes_seen[node_id] = {"frames": 1, "last_seen": time.monotonic()}
        else:
            d["frames"] += 1
            d["last_seen"] = time.monotonic()

    def run(self):
        self.bus = can.ThreadSafeBus(self.channel, interface=self.interface)
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            try:
                # Must wait longer than the can bus read
                # in the main loop self.bus.recv(1.0)
                self.thread.join(1.2)
            except:
                pass
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        x = OrderedDict()
        x["CAN Interface"] = self.interface
        x["CAN Channel"] = self.channel
        x["Received Frames"] = self.recvcount
        x["Decoded Writes"] = self.mapping.write_count
        x["Ignored Frames"] = self.recvignorecount + self.mapping.recvignorecount
        x["Invalid Frames"] = self.recvinvalidcount + self.mapping.recvinvalidcount
        x["Type Mismatches"] = self.mapping.type_mismatch_count
        x["Unsupported Types"] = self.unsupportedtypecount
        x["Error Frames"] = self.mapping.error_frame_count
        x["NoData Frames"] = self.nodatacount
        x["Sequence Gaps"] = self.seqgapcount
        x["Emergency Events"] = self.emergencyeventcount
        now = time.monotonic()
        x["Nodes Seen"] = {
            node: {
                "frames": d["frames"],
                "last_seen_age_s": round(now - d["last_seen"], 1),
            }
            for node, d in self.nodes_seen.items()
        }
        return x
