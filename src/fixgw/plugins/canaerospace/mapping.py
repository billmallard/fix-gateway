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

# Maps decoded CANaerospace messages onto FIX database keys.  Follows the
# closure pattern of fixgw.plugins.canfix.mapping: database items are
# resolved once at load time and the hot path is dictionary lookups only.

import os
import time

import fixgw.database as database
from fixgw import cfg

from . import protocol

# Named unit converters, applied AFTER scale/offset.
CONVERTERS = {
    "none": lambda v: v,
    "k2c": lambda v: v - 273.15,
    "c2f": lambda v: v * 9.0 / 5.0 + 32.0,
    "kpa2inhg": lambda v: v * 0.2953,
    "kpa2psi": lambda v: v * 0.14503773773,
    "mps2knots": lambda v: v * 1.94384449,
    "m2ft": lambda v: v * 3.28083989501,
    "pct": lambda v: v * 100.0,
}

_KNOWN_TOP_KEYS = {"ignore_fixid_missing", "strict_types", "inputs", "events"}


class Mapping(object):
    def __init__(self, mapfile, log=None, node_timeout=2.0):
        self.log = log
        self.node_timeout = node_timeout
        self.ignore_fixid_missing = False
        self.strict_types = False

        # counters surfaced through the plugin's get_status()
        self.write_count = 0
        self.recvignorecount = 0
        self.recvinvalidcount = 0
        self.type_mismatch_count = 0
        self.error_frame_count = 0
        self.event_count = 0

        # canid -> {element -> entry}; also a flat boolean list for the
        # hot-path reject, exactly like canfix's 'interesting' array.
        self.input_map = {}
        self.event_map = {}
        self.interesting = [False] * 2048

        self._warned_canids = set()

        if not os.path.exists(mapfile):
            raise ValueError("Unable to open mapfile: '{}'".format(mapfile))
        maps, meta = cfg.from_yaml(mapfile, metadata=True)

        for key, attr in (
            ("ignore_fixid_missing", "ignore_fixid_missing"),
            ("strict_types", "strict_types"),
        ):
            if key in maps:
                if isinstance(maps[key], bool):
                    setattr(self, attr, maps[key])
                else:
                    raise ValueError(
                        cfg.message(
                            "{} must be true or false".format(key), meta, key, True
                        )
                    )

        for key in maps:
            if key not in _KNOWN_TOP_KEYS and self.log:
                self.log.warning(
                    "Unknown top-level key '{}' in mapfile '{}' ignored".format(
                        key, mapfile
                    )
                )

        inputs = maps.get("inputs") or []
        meta_inputs = meta.get("inputs", {})
        for index, each in enumerate(inputs):
            entry = self._validate_input(each, meta_inputs, index)
            if entry is None:
                continue  # missing fixid with ignore_fixid_missing: true
            elements = self.input_map.setdefault(entry["canid"], {})
            if entry["element"] in elements:
                raise ValueError(
                    cfg.message(
                        "duplicate (canid, element) pair ({}, {})".format(
                            entry["canid"], entry["element"]
                        ),
                        meta_inputs[index],
                        "canid",
                        True,
                    )
                )
            elements[entry["element"]] = entry
            self.interesting[entry["canid"]] = True

        events = maps.get("events") or []
        meta_events = meta.get("events", {})
        for index, each in enumerate(events):
            entry = self._validate_event(each, meta_events, index)
            if entry is None:
                continue
            if entry["canid"] in self.event_map:
                raise ValueError(
                    cfg.message(
                        "duplicate event canid {}".format(entry["canid"]),
                        meta_events[index],
                        "canid",
                        True,
                    )
                )
            self.event_map[entry["canid"]] = entry

    def _resolve_item(self, fixid):
        try:
            return database.get_raw_item(fixid)
        except KeyError:
            # Validation already rejected unknown fixids unless
            # ignore_fixid_missing is set, so this entry is simply inert.
            return None

    def _validate_input(self, data, meta, index):
        if not isinstance(data, dict):
            raise ValueError(cfg.message("Inputs should be dictionaries", meta, index))
        for k in ["canid", "fixid"]:
            if k not in data:
                raise ValueError(
                    cfg.message("Key '{}' is missing".format(k), meta, index)
                )
        canid = data["canid"]
        if not isinstance(canid, int) or canid < 0 or canid > protocol.MAX_CANID:
            raise ValueError(
                cfg.message(
                    "canid must be an integer 0-{}".format(protocol.MAX_CANID),
                    meta[index],
                    "canid",
                    True,
                )
            )
        if data["fixid"] not in database.listkeys() and not self.ignore_fixid_missing:
            raise ValueError(
                cfg.message(
                    "fixid '{}' is not a valid fixid".format(data["fixid"]),
                    meta[index],
                    "fixid",
                    True,
                )
            )
        type_code = None
        if "type" in data:
            if data["type"] not in protocol.TYPE_CODES:
                raise ValueError(
                    cfg.message(
                        "type '{}' is not a supported CANaerospace data type".format(
                            data["type"]
                        ),
                        meta[index],
                        "type",
                        True,
                    )
                )
            type_code = protocol.TYPE_CODES[data["type"]]
        element = data.get("element", 0)
        if not isinstance(element, int) or element < 0 or element > 3:
            raise ValueError(
                cfg.message(
                    "element must be an integer 0-3", meta[index], "element", True
                )
            )
        for k in ("scale", "offset"):
            if k in data and not isinstance(data[k], (int, float)):
                raise ValueError(
                    cfg.message("{} must be a number".format(k), meta[index], k, True)
                )
        converter = data.get("converter", "none")
        if converter not in CONVERTERS:
            raise ValueError(
                cfg.message(
                    "converter '{}' unknown; choose from {}".format(
                        converter, ", ".join(sorted(CONVERTERS))
                    ),
                    meta[index],
                    "converter",
                    True,
                )
            )
        nodes = data.get("nodes")
        if nodes is not None:
            if (
                not isinstance(nodes, list)
                or not nodes
                or not all(isinstance(n, int) and 0 <= n <= 255 for n in nodes)
            ):
                raise ValueError(
                    cfg.message(
                        "nodes must be a non-empty list of node ids 0-255",
                        meta[index],
                        "nodes",
                        True,
                    )
                )
        item = self._resolve_item(data["fixid"])
        if item is None:
            return None
        return {
            "canid": canid,
            "fixid": data["fixid"],
            "item": item,
            "element": element,
            "type_code": type_code,
            "scale": float(data.get("scale", 1.0)),
            "offset": float(data.get("offset", 0.0)),
            "converter": CONVERTERS[converter],
            "nodes": nodes,
            "last_seen": {},
        }

    def _validate_event(self, data, meta, index):
        if not isinstance(data, dict):
            raise ValueError(cfg.message("Events should be dictionaries", meta, index))
        for k in ["canid", "fixid"]:
            if k not in data:
                raise ValueError(
                    cfg.message("Key '{}' is missing".format(k), meta, index)
                )
        canid = data["canid"]
        if not isinstance(canid, int) or canid < 0 or canid > 127:
            raise ValueError(
                cfg.message(
                    "event canid must be in the Emergency Event range 0-127",
                    meta[index],
                    "canid",
                    True,
                )
            )
        if data["fixid"] not in database.listkeys() and not self.ignore_fixid_missing:
            raise ValueError(
                cfg.message(
                    "fixid '{}' is not a valid fixid".format(data["fixid"]),
                    meta[index],
                    "fixid",
                    True,
                )
            )
        match_code = data.get("match_code")
        if match_code is not None and not isinstance(match_code, int):
            raise ValueError(
                cfg.message(
                    "match_code must be an integer or null",
                    meta[index],
                    "match_code",
                    True,
                )
            )
        item = self._resolve_item(data["fixid"])
        if item is None:
            return None
        return {
            "canid": canid,
            "fixid": data["fixid"],
            "item": item,
            "match_code": match_code,
        }

    def _warn(self, canid, message):
        """First occurrence per canid at WARNING, subsequent at DEBUG."""
        if self.log is None:
            return
        if canid in self._warned_canids:
            self.log.debug(message)
        else:
            self._warned_canids.add(canid)
            self.log.warning(message)

    def inputMap(self, msg):
        """Write a decoded CanasMessage into the mapped FIX items."""
        elements = self.input_map.get(msg.canid)
        if elements is None:
            self.recvignorecount += 1
            return
        now = time.monotonic()
        for element, entry in elements.items():
            item = entry["item"]

            # Source-node filter and lane priority (Rotax lane A / lane B):
            # record the sender as alive, then accept only if every
            # higher-priority node has been quiet for node_timeout.
            nodes = entry["nodes"]
            if nodes is not None:
                if msg.node_id not in nodes:
                    self.recvignorecount += 1
                    continue
                entry["last_seen"][msg.node_id] = now
                blocked = False
                for higher in nodes[: nodes.index(msg.node_id)]:
                    seen = entry["last_seen"].get(higher)
                    if seen is not None and (now - seen) <= self.node_timeout:
                        blocked = True
                        break
                if blocked:
                    self.recvignorecount += 1
                    continue

            if msg.data_type == protocol.ERROR:
                # Mark the element-0 mapping failed until the next good
                # value; ERROR frames carry no element addressing.
                if element == 0:
                    self.error_frame_count += 1
                    self._warn(
                        msg.canid,
                        "ERROR frame on canid {} (code 0x{:08X}); marking {} failed".format(
                            msg.canid, msg.values[0], entry["fixid"]
                        ),
                    )
                    item.fail = True
                continue

            # The wire's type byte is authoritative for decoding; the
            # mapfile 'type' is an expectation check only (unless
            # strict_types drops mismatches).
            if entry["type_code"] is not None and msg.data_type != entry["type_code"]:
                self.type_mismatch_count += 1
                self._warn(
                    msg.canid,
                    "canid {} arrived as {} but the mapfile expects {}".format(
                        msg.canid,
                        protocol.type_name(msg.data_type),
                        protocol.type_name(entry["type_code"]),
                    ),
                )
                if self.strict_types:
                    continue

            if element >= len(msg.values):
                self.recvinvalidcount += 1
                continue

            value = msg.values[element] * entry["scale"] + entry["offset"]
            value = entry["converter"](value)
            try:
                if item.fail:
                    item.fail = False
                item.value = value
                self.write_count += 1
            except ValueError:
                self.recvinvalidcount += 1
                if self.log:
                    self.log.debug(
                        "Database rejected {} = {}".format(entry["fixid"], value)
                    )

    def eventMap(self, canid, raw_code):
        """Latch an Emergency Event onto its mapped annunciator fixid.

        raw_code is the 4 payload bytes as an unsigned big-endian int, or
        None when the frame carried no 4-byte payload.
        """
        entry = self.event_map.get(canid)
        if entry is None:
            return
        if entry["match_code"] is not None and raw_code != entry["match_code"]:
            return
        try:
            entry["item"].value = True
            self.event_count += 1
        except ValueError:
            self.recvinvalidcount += 1
