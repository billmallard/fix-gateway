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

# Maps decoded DroneCAN messages onto FIX database keys.  The dronecan
# library does all frame/transfer/DSDL decoding; this module only walks
# field paths on the decoded message objects.  Follows the closure pattern
# of the canaerospace and canfix mappings: message types, field paths and
# database items are all resolved once at load time.

import math
import os
import re
from collections import OrderedDict

import dronecan
from dronecan.dsdl.parser import CompoundType, Type

import fixgw.database as database
from fixgw import cfg

# Named unit converters, applied AFTER scale/offset.  Same names and
# behaviour as the canaerospace mapfile so a converter means one thing
# across both bridges.
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

# uavcan.protocol.NodeStatus health values.
HEALTH_OK = 0
HEALTH_WARNING = 1
HEALTH_ERROR = 2
HEALTH_CRITICAL = 3
HEALTH_NAMES = {
    HEALTH_OK: "OK",
    HEALTH_WARNING: "WARNING",
    HEALTH_ERROR: "ERROR",
    HEALTH_CRITICAL: "CRITICAL",
}

_KNOWN_TOP_KEYS = {"ignore_fixid_missing", "inputs"}
_KNOWN_INPUT_KEYS = {"message", "source_node", "where", "fields"}
_KNOWN_FIELD_KEYS = {"path", "fixid", "scale", "offset", "converter"}

# foo, foo.bar, foo[2], foo.bar[2].baz
_PATH_RE = re.compile(r"^[A-Za-z_]\w*(\[\d+\])*(\.[A-Za-z_]\w*(\[\d+\])*)*$")
_STEP_RE = re.compile(r"([A-Za-z_]\w*)|\[(\d+)\]")


class PathError(Exception):
    """A field path did not resolve on a received message object."""


def resolve_type(name):
    """Return the DSDL compound type for a dotted name, or None."""
    if not isinstance(name, str) or not name:
        return None
    obj = dronecan
    for part in name.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    if not isinstance(obj, CompoundType) or obj.kind != CompoundType.KIND_MESSAGE:
        return None
    return obj


def parse_path(path):
    """Split a field path into ('attr', name) / ('index', n) steps."""
    if not isinstance(path, str) or not _PATH_RE.match(path):
        raise ValueError("bad field path '{}'".format(path))
    steps = []
    for name, index in _STEP_RE.findall(path):
        if name:
            steps.append(("attr", name))
        else:
            steps.append(("index", int(index)))
    return steps


def check_path(dtype, steps, path):
    """Validate path steps against the DSDL schema.

    Returns the terminal primitive type.  Dynamic arrays are accepted for
    any index below their maximum size; whether a given message actually
    carries that element is a runtime question (path_error_count).
    """
    t = dtype
    for kind, arg in steps:
        if kind == "attr":
            if t.category != Type.CATEGORY_COMPOUND:
                raise ValueError(
                    "'{}': '{}' is applied to a non-compound field".format(path, arg)
                )
            fields = {f.name: f.type for f in t.fields if f.name}
            if arg not in fields:
                raise ValueError(
                    "'{}': {} has no field '{}' (fields: {})".format(
                        path, t.full_name, arg, ", ".join(sorted(fields))
                    )
                )
            t = fields[arg]
        else:
            if t.category != Type.CATEGORY_ARRAY:
                raise ValueError(
                    "'{}': index [{}] is applied to a non-array field".format(path, arg)
                )
            if arg >= t.max_size:
                raise ValueError(
                    "'{}': index [{}] is past the array size {}".format(
                        path, arg, t.max_size
                    )
                )
            t = t.value_type
    if t.category != Type.CATEGORY_PRIMITIVE:
        raise ValueError("'{}' does not end at a numeric field".format(path))
    return t


def make_accessor(steps):
    """Compile path steps into a closure that reads a message object."""

    def get(msg):
        obj = msg
        try:
            for kind, arg in steps:
                if kind == "attr":
                    obj = getattr(obj, arg)
                else:
                    obj = obj[arg]
        except (AttributeError, IndexError, TypeError, KeyError) as e:
            raise PathError(str(e))
        return obj

    return get


class Mapping(object):
    def __init__(self, mapfile, log=None):
        self.log = log
        self.ignore_fixid_missing = False

        # counters surfaced through the plugin's get_status()
        self.transfer_count = 0
        self.write_count = 0
        self.filtered_count = 0
        self.path_error_count = 0
        self.nonfinite_count = 0
        self.invalid_count = 0
        self.handler_error_count = 0

        # full DSDL name -> {"type": CompoundType, "entries": [...]}
        self.inputs = OrderedDict()
        # node_id -> last NodeStatus health; written by the plugin thread
        self.node_health = {}

        self._warned = set()

        if not os.path.exists(mapfile):
            raise ValueError("Unable to open mapfile: '{}'".format(mapfile))
        maps, meta = cfg.from_yaml(mapfile, metadata=True)
        if maps is None:
            maps, meta = {}, {}
        if not isinstance(maps, dict):
            raise ValueError("Mapfile '{}' must be a dictionary".format(mapfile))

        if "ignore_fixid_missing" in maps:
            if isinstance(maps["ignore_fixid_missing"], bool):
                self.ignore_fixid_missing = maps["ignore_fixid_missing"]
            else:
                raise ValueError(
                    cfg.message(
                        "ignore_fixid_missing must be true or false",
                        meta,
                        "ignore_fixid_missing",
                        True,
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
        if not isinstance(inputs, list):
            raise ValueError(cfg.message("inputs must be a list", meta, "inputs", True))
        meta_inputs = meta.get("inputs", {})
        for index, each in enumerate(inputs):
            entry = self._validate_input(each, meta_inputs, index)
            slot = self.inputs.setdefault(
                entry["message"], {"type": entry["type"], "entries": []}
            )
            slot["entries"].append(entry)

    def _resolve_item(self, fixid):
        try:
            return database.get_raw_item(fixid)
        except KeyError:
            # Validation already rejected unknown fixids unless
            # ignore_fixid_missing is set, so this field is simply inert.
            return None

    def _validate_input(self, data, meta, index):
        if not isinstance(data, dict):
            raise ValueError(cfg.message("Inputs should be dictionaries", meta, index))
        for k in ("message", "fields"):
            if k not in data:
                raise ValueError(
                    cfg.message("Key '{}' is missing".format(k), meta, index)
                )
        m = meta[index]
        for k in data:
            if k not in _KNOWN_INPUT_KEYS:
                raise ValueError(cfg.message("unknown key '{}'".format(k), m, k, True))

        dtype = resolve_type(data["message"])
        if dtype is None:
            raise ValueError(
                cfg.message(
                    "message '{}' is not a DroneCAN message type".format(
                        data["message"]
                    ),
                    m,
                    "message",
                    True,
                )
            )

        source_node = data.get("source_node")
        if source_node is not None and (
            not isinstance(source_node, int)
            or isinstance(source_node, bool)
            or not 1 <= source_node <= 127
        ):
            raise ValueError(
                cfg.message(
                    "source_node must be a node id 1-127 or null",
                    m,
                    "source_node",
                    True,
                )
            )

        where = data.get("where") or {}
        if not isinstance(where, dict):
            raise ValueError(
                cfg.message("where must be a dictionary", m, "where", True)
            )
        guards = []
        for path, expected in where.items():
            try:
                steps = parse_path(path)
                check_path(dtype, steps, path)
            except ValueError as e:
                raise ValueError(cfg.message("where: {}".format(e), m, "where", True))
            if not isinstance(expected, (int, float)) or isinstance(expected, bool):
                raise ValueError(
                    cfg.message(
                        "where: '{}' must compare against a number".format(path),
                        m,
                        "where",
                        True,
                    )
                )
            guards.append((path, make_accessor(steps), expected))

        fields = data["fields"]
        if not isinstance(fields, list) or not fields:
            raise ValueError(
                cfg.message("fields must be a non-empty list", m, "fields", True)
            )
        meta_fields = m.get("fields", {})
        compiled = []
        for findex, field in enumerate(fields):
            f = self._validate_field(dtype, field, meta_fields, findex)
            if f is not None:
                compiled.append(f)

        return {
            "message": dtype.full_name,
            "type": dtype,
            "source_node": source_node,
            "guards": guards,
            "fields": compiled,
        }

    def _validate_field(self, dtype, data, meta, index):
        if not isinstance(data, dict):
            raise ValueError(cfg.message("Fields should be dictionaries", meta, index))
        for k in ("path", "fixid"):
            if k not in data:
                raise ValueError(
                    cfg.message("Key '{}' is missing".format(k), meta, index)
                )
        m = meta[index]
        for k in data:
            if k not in _KNOWN_FIELD_KEYS:
                raise ValueError(cfg.message("unknown key '{}'".format(k), m, k, True))
        try:
            steps = parse_path(data["path"])
            check_path(dtype, steps, data["path"])
        except ValueError as e:
            raise ValueError(cfg.message(str(e), m, "path", True))
        if data["fixid"] not in database.listkeys() and not self.ignore_fixid_missing:
            raise ValueError(
                cfg.message(
                    "fixid '{}' is not a valid fixid".format(data["fixid"]),
                    m,
                    "fixid",
                    True,
                )
            )
        for k in ("scale", "offset"):
            if k in data and (
                not isinstance(data[k], (int, float)) or isinstance(data[k], bool)
            ):
                raise ValueError(
                    cfg.message("{} must be a number".format(k), m, k, True)
                )
        converter = data.get("converter", "none")
        if converter not in CONVERTERS:
            raise ValueError(
                cfg.message(
                    "converter '{}' unknown; choose from {}".format(
                        converter, ", ".join(sorted(CONVERTERS))
                    ),
                    m,
                    "converter",
                    True,
                )
            )
        item = self._resolve_item(data["fixid"])
        if item is None:
            return None
        return {
            "path": data["path"],
            "get": make_accessor(steps),
            "fixid": data["fixid"],
            "item": item,
            "scale": float(data.get("scale", 1.0)),
            "offset": float(data.get("offset", 0.0)),
            "converter": CONVERTERS[converter],
        }

    def _warn_once(self, key, message):
        """First occurrence per key at WARNING, subsequent at DEBUG."""
        if self.log is None:
            return
        if key in self._warned:
            self.log.debug(message)
        else:
            self._warned.add(key)
            self.log.warning(message)

    def handler_for(self, message):
        """Return the dronecan handler callback for one message type."""
        entries = self.inputs[message]["entries"]

        def handler(event):
            self.transfer_count += 1
            # A malformed or unexpected transfer must never escape into
            # node.spin() and the plugin thread.
            try:
                self.dispatch(entries, event)
            except Exception as e:
                self.handler_error_count += 1
                self._warn_once(
                    ("handler", message),
                    "Unexpected failure handling {}: {}".format(message, e),
                )

        return handler

    def dispatch(self, entries, event):
        msg = event.message
        source = event.transfer.source_node_id
        for entry in entries:
            if entry["source_node"] is not None and source != entry["source_node"]:
                self.filtered_count += 1
                continue
            if not self._guards_pass(entry, msg):
                self.filtered_count += 1
                continue
            for field in entry["fields"]:
                self._write(entry, field, msg, source)

    def _guards_pass(self, entry, msg):
        for path, get, expected in entry["guards"]:
            try:
                if get(msg) != expected:
                    return False
            except PathError:
                self.path_error_count += 1
                self._warn_once(
                    (entry["message"], path),
                    "{}: where-path '{}' did not resolve".format(
                        entry["message"], path
                    ),
                )
                return False
        return True

    def _write(self, entry, field, msg, source):
        item = field["item"]
        try:
            raw = field["get"](msg)
        except PathError as e:
            self.path_error_count += 1
            self._warn_once(
                (entry["message"], field["path"]),
                "{}: path '{}' did not resolve ({}); is the sender on a "
                "different DSDL revision?".format(entry["message"], field["path"], e),
            )
            return
        try:
            value = float(raw)
        except (TypeError, ValueError):
            self.path_error_count += 1
            self._warn_once(
                (entry["message"], field["path"]),
                "{}: path '{}' is not numeric ({!r})".format(
                    entry["message"], field["path"], raw
                ),
            )
            return

        # DroneCAN senders use NaN for "unknown" (float16 has no other
        # sentinel).  Never write it as a number: flag the key failed and
        # leave the last good value in place, like a CANaerospace ERROR.
        if not math.isfinite(value):
            self.nonfinite_count += 1
            item.fail = True
            self._warn_once(
                ("nonfinite", entry["message"], field["path"]),
                "{}: '{}' from node {} is {}; marking {} failed".format(
                    entry["message"], field["path"], source, raw, field["fixid"]
                ),
            )
            return

        value = field["converter"](value * field["scale"] + field["offset"])
        try:
            if item.fail:
                item.fail = False
            # Source-tagged quality: a sender reporting ERROR/CRITICAL in its
            # NodeStatus does not trust its own output.
            bad = self.node_health.get(source, HEALTH_OK) >= HEALTH_ERROR
            if item.bad != bad:
                item.bad = bad
            item.value = value
            self.write_count += 1
        except ValueError:
            self.invalid_count += 1
            if self.log:
                self.log.debug(
                    "Database rejected {} = {}".format(field["fixid"], value)
                )
