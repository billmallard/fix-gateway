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

# DroneCAN (UAVCAN v0) input bridge.  Subscribes to DroneCAN broadcasts
# from commodity drone-ecosystem sensors (magnetometer, GNSS, air data,
# power monitors) and writes mapped values into the FIX database.  CAN-FIX
# remains the system's native bus protocol; DroneCAN is a federated input.
#
# Listen-only by default: with no node_id the local node is anonymous and
# the driver refuses to transmit.  Setting node_id (needed for the dynamic
# node-ID allocator) makes the node publish its own NodeStatus, as every
# DroneCAN node must.  No flight data is ever transmitted.
#
# This plugin needs its own CAN interface.  Do not share a physical bus
# with CAN-FIX: bit rates and identifier spaces collide.

import os
import threading
import time
from collections import OrderedDict

import can
import dronecan
from dronecan.app.dynamic_node_id import CentralizedServer
from dronecan.app.node_monitor import NodeMonitor
from dronecan.driver.common import DriverError
from dronecan.transport import TransferError

import fixgw.plugin as plugin

from . import mapping
from .driver import PythonCANDriver

# spin() timeout; also bounds how long stop() waits on the thread.
SPIN_TIMEOUT = 0.5
# Consecutive driver failures before the node and its driver are re-created.
REOPEN_AFTER = 3
# Minimum seconds between repeated warnings of the same kind.
WARN_INTERVAL = 60.0

# Exceptions that mean the CAN driver itself is unhealthy.
DRIVER_ERRORS = (can.CanError, OSError, DriverError)


def make_node(driver, node_id):
    """Build the dronecan Node on our driver (monkeypatched in tests)."""
    return dronecan.node.Node(driver, node_id=node_id)


class MainThread(threading.Thread):
    def __init__(self, parent):
        super(MainThread, self).__init__()
        self.getout = False
        self._wake = threading.Event()
        self.parent = parent
        self.log = parent.log
        self.node = None
        self._last_warn = {}

    def _warn(self, kind, message):
        """Rate-limited warning: at most one per kind per WARN_INTERVAL."""
        now = time.monotonic()
        last = self._last_warn.get(kind)
        if last is None or now - last >= WARN_INTERVAL:
            self._last_warn[kind] = now
            self.log.warning(message)
        else:
            self.log.debug(message)

    def run(self):
        while not self.getout:
            try:
                self.node = self.parent.open_node()
            except Exception as e:
                self.parent.open_errors += 1
                self._warn("open", "Unable to open DroneCAN node: {}".format(e))
                self._wake.wait(1.0)
                continue
            try:
                self._spin_until_dead()
            finally:
                self.parent.close_node(self.node)
                self.node = None
            if not self.getout:
                self.parent.reopen_count += 1
                self._wake.wait(1.0)

    def _spin_until_dead(self):
        consecutive = 0
        while not self.getout:
            try:
                self.node.spin(SPIN_TIMEOUT)
                consecutive = 0
            except TransferError as e:
                # Bad CRC, broken multi-frame sequence, or a message type the
                # library has no DSDL for (vendor messages).  The bus is fine.
                self.parent.transfer_error_count += 1
                self._warn("transfer", "DroneCAN transfer dropped: {}".format(e))
            except DRIVER_ERRORS as e:
                self.parent.spin_error_count += 1
                consecutive += 1
                self._warn("driver", "DroneCAN driver error: {}".format(e))
                if consecutive >= REOPEN_AFTER:
                    return
                self._wake.wait(1.0)
            except Exception as e:
                # Anything else must not kill the thread either.
                self.parent.spin_error_count += 1
                self._warn("spin", "Unexpected DroneCAN spin failure: {}".format(e))

    def stop(self):
        self.getout = True
        self._wake.set()


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.interface = config.get("interface", "socketcan")
        self.channel = config.get("channel", "can1")
        self.bitrate = config.get("bitrate", 1000000)
        self.node_id = config.get("node_id")
        self.allocator = config.get("allocator", False)
        if self.node_id is not None and (
            not isinstance(self.node_id, int)
            or isinstance(self.node_id, bool)
            or not 1 <= self.node_id <= 125
        ):
            raise ValueError("dronecan: node_id must be 1-125 or null (anonymous)")
        if not isinstance(self.allocator, bool):
            raise ValueError("dronecan: allocator must be true or false")
        if self.allocator and self.node_id is None:
            raise ValueError(
                "dronecan: allocator: true needs a node_id; an anonymous node "
                "cannot answer allocation requests"
            )
        self.allocator_db = None
        if self.allocator:
            self.allocator_db = config.get(
                "allocator_db", "{CONFIG}/dronecan/allocation.db"
            ).format(CONFIG=config["CONFIGPATH"])
        mapfilename = config["mapfile"].format(CONFIG=config["CONFIGPATH"])
        self.mapping = mapping.Mapping(mapfilename, self.log)
        self.thread = MainThread(self)

        self.driver = None
        self._monitor = None
        self._allocator = None
        self.nodes = {}
        self.nodestatus_count = 0
        self.transfer_error_count = 0
        self.spin_error_count = 0
        self.open_errors = 0
        self.reopen_count = 0

    def open_node(self):
        """Create the driver, node and handlers.  Runs on MainThread."""
        self.driver = PythonCANDriver(
            self.interface,
            self.channel,
            self.bitrate,
            listen_only=self.node_id is None,
        )
        try:
            node = make_node(self.driver, self.node_id)
            if self.node_id is not None:
                node.mode = dronecan.uavcan.protocol.NodeStatus().MODE_OPERATIONAL
            node.add_handler(dronecan.uavcan.protocol.NodeStatus, self._on_node_status)
            for message, slot in self.mapping.inputs.items():
                node.add_handler(slot["type"], self.mapping.handler_for(message))
            if self.allocator:
                folder = os.path.dirname(self.allocator_db)
                if folder:
                    os.makedirs(folder, exist_ok=True)
                self._monitor = NodeMonitor(node)
                self._allocator = CentralizedServer(
                    node, self._monitor, database_storage=self.allocator_db
                )
        except Exception:
            self.driver.close()
            raise
        return node

    def close_node(self, node):
        for obj in (self._allocator, self._monitor, node):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception as e:
                self.log.debug("Error closing {}: {}".format(type(obj).__name__, e))
        self._allocator = None
        self._monitor = None

    def _on_node_status(self, event):
        self.nodestatus_count += 1
        msg = event.message
        node_id = event.transfer.source_node_id
        health = int(msg.health)
        prev = self.nodes.get(node_id)
        self.nodes[node_id] = {
            "health": health,
            "mode": int(msg.mode),
            "uptime": int(msg.uptime_sec),
            "last_seen": time.monotonic(),
        }
        self.mapping.node_health[node_id] = health
        if prev is None:
            self.log.info(
                "DroneCAN node {} seen (health {})".format(
                    node_id, mapping.HEALTH_NAMES.get(health, health)
                )
            )
        if (prev is None or prev["health"] != health) and (
            health >= mapping.HEALTH_WARNING
        ):
            self.log.warning(
                "DroneCAN node {} reports health {}".format(
                    node_id, mapping.HEALTH_NAMES.get(health, health)
                )
            )
        elif prev is not None and prev["health"] != health:
            self.log.info(
                "DroneCAN node {} health back to {}".format(
                    node_id, mapping.HEALTH_NAMES.get(health, health)
                )
            )

    def run(self):
        self.thread.start()

    def stop(self):
        self.thread.stop()
        if self.thread.is_alive():
            try:
                # Must wait longer than one node.spin(SPIN_TIMEOUT) pass
                self.thread.join(SPIN_TIMEOUT + 0.7)
            except Exception:
                pass
        if self.thread.is_alive():
            raise plugin.PluginFail

    def get_status(self):
        x = OrderedDict()
        x["CAN Interface"] = self.interface
        x["CAN Channel"] = self.channel
        x["Bitrate"] = self.bitrate
        x["Node ID"] = "anonymous" if self.node_id is None else self.node_id
        x["Allocator"] = "on" if self.allocator else "off"
        driver = self.driver
        x["Frames Received"] = driver.frame_count if driver else 0
        x["Foreign Frames"] = driver.foreign_count if driver else 0
        x["Transfers Received"] = self.mapping.transfer_count + self.nodestatus_count
        x["Values Written"] = self.mapping.write_count
        x["Filtered"] = self.mapping.filtered_count
        x["Path Errors"] = self.mapping.path_error_count
        x["Non-finite Values"] = self.mapping.nonfinite_count
        x["Rejected Values"] = self.mapping.invalid_count
        x["Handler Errors"] = self.mapping.handler_error_count
        x["Transfer Errors"] = self.transfer_error_count
        x["Spin Errors"] = self.spin_error_count
        x["Reopens"] = self.reopen_count
        now = time.monotonic()
        x["Nodes"] = {
            node: {
                "health": mapping.HEALTH_NAMES.get(d["health"], d["health"]),
                "mode": d["mode"],
                "uptime_s": d["uptime"],
                "last_seen_age_s": round(now - d["last_seen"], 1),
            }
            for node, d in list(self.nodes.items())
        }
        return x
