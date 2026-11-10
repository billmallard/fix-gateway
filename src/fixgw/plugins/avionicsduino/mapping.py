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

# Maps decoded AvionicsDuino fields onto FIX database keys.  Same shape as
# fixgw.plugins.canaerospace.mapping: items are resolved once at load time
# and the hot path is dictionary lookups.  Freshness comes from each key's
# database tol, exactly as for canaerospace: a node that goes quiet stops
# writing, and database.update() marks its keys old once the tol runs out.

import math
import os

import fixgw.database as database
from fixgw import cfg

from . import protocol

G0 = 9.80665  # m/s^2 per g
KMH_PER_KT = 1.852

# Named unit converters, applied AFTER scale/offset.
CONVERTERS = {
    "none": lambda v: v,
    "rad2deg": math.degrees,
    "mps2_to_g": lambda v: v / G0,
    "kmh2kt": lambda v: v / KMH_PER_KT,
    "ut2gauss": lambda v: v / 100.0,
    "mps2fpm": lambda v: v * 196.850393701,
    "wrap360": lambda v: v % 360.0,
}

# Config options a mapfile row may depend on with `requires:`.  Each is off
# unless the connection config turns it on.
OPTIONS = ("rdam_altitude", "rdam_vertical_speed")

# Keys this plugin must never write, whatever the mapfile says.  BARO is the
# pilot's altimeter setting; ID 46 carries the RDAM's own QNH, which pyEfis
# cannot set (briefs/avionicsduino_compatibility.md, obstacle 1).
FORBIDDEN_FIXIDS = {"BARO"}

# A smoothed value restarts from the raw sample after a gap this long, so a
# node that comes back does not ease in from a stale value.
SMOOTH_RESET_S = 1.0

_KNOWN_TOP_KEYS = {"ignore_fixid_missing", "inputs"}
_KNOWN_INPUT_KEYS = {
    "canid",
    "field",
    "fixid",
    "scale",
    "offset",
    "converter",
    "smooth",
    "requires",
}


class Mapping(object):
    def __init__(self, mapfile, log=None, options=None, smoothing_tau=0.0):
        self.log = log
        options = options or {}
        for name in options:
            if name not in OPTIONS:
                raise ValueError("Unknown mapping option '{}'".format(name))
        self.options = {name: bool(options.get(name, False)) for name in OPTIONS}
        self.smoothing_tau = float(smoothing_tau)
        if self.smoothing_tau < 0:
            raise ValueError("smoothing_tau must be zero or positive")
        self.ignore_fixid_missing = False

        # counters surfaced through the plugin's get_status()
        self.write_count = 0
        self.nonfinite_count = 0
        self.invalid_count = 0
        self.disabled_count = 0

        # canid -> [entry, ...]
        self.input_map = {}
        self.interesting = set()

        if not os.path.exists(mapfile):
            raise ValueError("Unable to open mapfile: '{}'".format(mapfile))
        maps, meta = cfg.from_yaml(mapfile, metadata=True)

        if "ignore_fixid_missing" in maps:
            if not isinstance(maps["ignore_fixid_missing"], bool):
                raise ValueError(
                    cfg.message(
                        "ignore_fixid_missing must be true or false",
                        meta,
                        "ignore_fixid_missing",
                        True,
                    )
                )
            self.ignore_fixid_missing = maps["ignore_fixid_missing"]

        for key in maps:
            if key not in _KNOWN_TOP_KEYS and self.log:
                self.log.warning(
                    "Unknown top-level key '{}' in mapfile '{}' ignored".format(
                        key, mapfile
                    )
                )

        inputs = maps.get("inputs") or []
        meta_inputs = meta.get("inputs", {})
        seen = set()
        for index, each in enumerate(inputs):
            entry = self._validate_input(each, meta_inputs, index)
            if entry is None:
                continue
            pair = (entry["canid"], entry["field"], entry["fixid"])
            if pair in seen:
                raise ValueError(
                    cfg.message(
                        "duplicate row (canid {}, field {}, fixid {})".format(*pair),
                        meta_inputs[index],
                        "canid",
                        True,
                    )
                )
            seen.add(pair)
            if entry["requires"] is not None and not self.options[entry["requires"]]:
                self.disabled_count += 1
                continue
            self.input_map.setdefault(entry["canid"], []).append(entry)
            self.interesting.add(entry["canid"])

    def _resolve_item(self, fixid):
        try:
            return database.get_raw_item(fixid)
        except KeyError:
            return None

    def _validate_input(self, data, meta, index):
        if not isinstance(data, dict):
            raise ValueError(cfg.message("Inputs should be dictionaries", meta, index))
        for k in ["canid", "field", "fixid"]:
            if k not in data:
                raise ValueError(
                    cfg.message("Key '{}' is missing".format(k), meta, index)
                )
        for k in data:
            if k not in _KNOWN_INPUT_KEYS:
                raise ValueError(
                    cfg.message("Unknown key '{}'".format(k), meta[index], k, True)
                )
        canid = data["canid"]
        if canid not in protocol.LAYOUTS:
            raise ValueError(
                cfg.message(
                    "canid {} has no AvionicsDuino layout".format(canid),
                    meta[index],
                    "canid",
                    True,
                )
            )
        field = data["field"]
        if field not in protocol.LAYOUTS[canid].fields:
            raise ValueError(
                cfg.message(
                    "canid {} has no field '{}'; it has {}".format(
                        canid, field, ", ".join(protocol.LAYOUTS[canid].fields)
                    ),
                    meta[index],
                    "field",
                    True,
                )
            )
        fixid = data["fixid"]
        if fixid in FORBIDDEN_FIXIDS:
            raise ValueError(
                cfg.message(
                    "the avionicsduino plugin never writes {}".format(fixid),
                    meta[index],
                    "fixid",
                    True,
                )
            )
        if fixid not in database.listkeys() and not self.ignore_fixid_missing:
            raise ValueError(
                cfg.message(
                    "fixid '{}' is not a valid fixid".format(fixid),
                    meta[index],
                    "fixid",
                    True,
                )
            )
        for k in ("scale", "offset"):
            if k in data and (
                isinstance(data[k], bool) or not isinstance(data[k], (int, float))
            ):
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
        smooth = data.get("smooth", False)
        if not isinstance(smooth, bool):
            raise ValueError(
                cfg.message("smooth must be true or false", meta[index], "smooth", True)
            )
        if smooth and converter == "wrap360":
            # A low-pass across the 0/360 seam averages 359 and 1 to 180.
            raise ValueError(
                cfg.message(
                    "smooth cannot be used with wrap360", meta[index], "smooth", True
                )
            )
        requires = data.get("requires")
        if requires is not None and requires not in OPTIONS:
            raise ValueError(
                cfg.message(
                    "requires '{}' unknown; choose from {}".format(
                        requires, ", ".join(OPTIONS)
                    ),
                    meta[index],
                    "requires",
                    True,
                )
            )
        item = self._resolve_item(fixid)
        if item is None:
            return None
        if item.tol == 0 and self.log:
            self.log.warning(
                "{} has no tol in the database: it will never go old if "
                "the AvionicsDuino node goes quiet".format(fixid)
            )
        return {
            "canid": canid,
            "field": field,
            "fixid": fixid,
            "item": item,
            "scale": float(data.get("scale", 1.0)),
            "offset": float(data.get("offset", 0.0)),
            "converter": CONVERTERS[converter],
            "smooth": smooth,
            "requires": requires,
            "state": None,  # (value, monotonic time) of the last smoothed write
        }

    def _smooth(self, entry, value, now):
        state = entry["state"]
        if state is not None and (now - state[1]) <= SMOOTH_RESET_S:
            alpha = 1.0 - math.exp(-(now - state[1]) / self.smoothing_tau)
            value = state[0] + alpha * (value - state[0])
        entry["state"] = (value, now)
        return value

    def inputMap(self, canid, values, now):
        """Write one decoded frame's fields into the mapped FIX items.

        values is protocol.decode()'s {field: value}; now is a monotonic
        time in seconds, used only by smoothing.
        """
        entries = self.input_map.get(canid)
        if entries is None:
            return
        for entry in entries:
            value = values[entry["field"]]
            if isinstance(value, float) and not math.isfinite(value):
                # Write nothing: the key ages to old on its own tol.
                self.nonfinite_count += 1
                continue
            value = value * entry["scale"] + entry["offset"]
            value = entry["converter"](value)
            if entry["smooth"] and self.smoothing_tau > 0:
                value = self._smooth(entry, value, now)
            item = entry["item"]
            try:
                if item.fail:
                    item.fail = False
                item.value = value
                self.write_count += 1
            except ValueError:
                self.invalid_count += 1
                if self.log:
                    self.log.debug(
                        "Database rejected {} = {}".format(entry["fixid"], value)
                    )
