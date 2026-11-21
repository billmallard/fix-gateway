#  Copyright (c) 2018 Phil Birkelbach
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
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307,
#  USA.import plugin

#  This is a compute plugin.  It calculates derivative points like averages,
#  minimums or maximums and the like.  Specific calculations for things like
#  True Airspeed could be done also.

import operator
import time
from collections import OrderedDict
import fixgw.database as database
import fixgw.plugin as plugin


def _now():
    return time.monotonic()


def _holds(condition, operand_value):
    """True when a parsed condition holds on a trusted input: one that is
    old, bad or failed never holds, nor does an unset or untrusted
    right-hand side."""
    item, oper, operand = condition
    v, _annunc, old, bad, fail, _secfail = item.value
    value = operand_value(operand)
    return not (old or bad or fail or value is None or not oper(v, value))


class AnnunciateItem(object):
    operators = {
        "<": operator.lt,
        "<=": operator.le,
        "=": operator.eq,
        "!=": operator.ne,
        ">": operator.gt,
        ">=": operator.ge,
    }

    def __init__(self, plugin, defaults, itemdef):
        self.plugin = plugin
        self.key = itemdef["key"]
        self.item = plugin.db_get_item(self.key)
        if self.item is None:
            raise ValueError("Key {} not found".format(self.key))
        low_point = defaults["low_aux_point"] if "low_aux_point" in defaults else None
        low_point = (
            itemdef["low_aux_point"] if "low_aux_point" in itemdef else low_point
        )
        if low_point is not None and low_point in self.item.aux:
            self.low_set_point = "{}.{}".format(self.key, low_point)
        else:
            self.low_set_point = None

        high_point = (
            defaults["high_aux_point"] if "high_aux_point" in defaults else None
        )
        high_point = (
            itemdef["high_aux_point"] if "high_aux_point" in itemdef else high_point
        )
        if high_point is not None and high_point in self.item.aux:
            self.high_set_point = "{}.{}".format(self.key, high_point)
        else:
            self.high_set_point = None

        start_bypass = defaults["start_bypass"] if "start_bypass" in defaults else None
        self.start_bypass = (
            itemdef["start_bypass"] if "start_bypass" in itemdef else start_bypass
        )

        deadband = defaults["deadband"] if "deadband" in defaults else None
        deadband = itemdef["deadband"] if "deadband" in itemdef else deadband
        if type(deadband) == str and "%" in deadband:
            db = float(deadband.replace("%", "").strip()) / 100
            self.deadband = abs(db * (self.item.max - self.item.min))
        else:
            self.deadband = float(deadband)

        cond_bypass = defaults["cond_bypass"] if "cond_bypass" in defaults else None
        self.cond_bypass = (
            itemdef["cond_bypass"] if "cond_bypass" in itemdef else cond_bypass
        )
        if self.cond_bypass == "None":
            self.cond_bypass = None
        # cond_bypass is one condition or a list of them.  The alarm is
        # bypassed only while every condition holds, so a list lets one
        # failed sensor fail to bypass rather than silence the alarm
        # (AER-4370).
        if self.cond_bypass is None:
            conditions = []
        elif isinstance(self.cond_bypass, str):
            conditions = [self.cond_bypass]
        else:
            conditions = list(self.cond_bypass)
        self.conditions = [self._parse_condition(c) for c in conditions]
        warmup = itemdef["warmup"] if "warmup" in itemdef else None
        self.warmup = Warmup(self, warmup) if warmup else None
        # YAML gives start_bypass: no as False, which must not arm the latch
        self.start_bypass_latch = bool(self.start_bypass)
        # No annunciation while no source has written the key: its expiry
        # fires the callback with the initial 0, below any low alarm.  For a
        # key the database defines on a count the install may not fill, as
        # FUELQT's sum leaves such a tank out (AER-5391, AER-6138).
        self.skip_unwritten = bool(itemdef.get("skip_unwritten", False))
        self.low_latch = False
        self.high_latch = False

        plugin.db_callback_add(self.key, self.evaluate)

    def _parse_condition(self, cond):
        tokens = cond.split()
        if len(tokens) != 3:
            raise ValueError(
                "Wrong number of tokens given for conditional bypass: {}".format(
                    self.key
                )
            )
        item = self.plugin.db_get_item(tokens[0])
        if item is None:
            raise ValueError(
                "Unknown Key {} for conditional bypass: {}".format(tokens[0], self.key)
            )
        try:
            oper = self.operators[tokens[1]]
        except KeyError:
            raise ValueError(
                "Unknown operator {} for conditional bypass: {}".format(
                    tokens[1], self.key
                )
            )
        return (item, oper, self._parse_operand(tokens[2], item))

    def _parse_operand(self, token, item):
        """The right side of a condition: a literal, or a FIX value read at
        each evaluation (``IAS < IAS.Vgnd``), so the setting lives in the
        database where the configurator writes it (AER-4619).
        """
        name, _, aux = token.partition(".")
        if not name[:1].isalpha():
            return item.dtype(token)
        try:
            ref = self.plugin.db_get_item(name)
        except KeyError:
            ref = None
        if ref is None:
            if aux:
                raise ValueError(
                    "Unknown Key {} for conditional bypass: {}".format(name, self.key)
                )
            return item.dtype(token)  # a word literal, e.g. True
        if aux and aux not in ref.aux:
            raise ValueError(
                "Unknown aux {} for conditional bypass: {}".format(token, self.key)
            )
        if aux and ref.get_aux_value(aux) is None:
            self.plugin.log.warning(
                "{} is not set: {} will not be bypassed".format(token, self.key)
            )
        return (ref, aux)

    def _operand_value(self, operand):
        """The operand's value, or None when it cannot be trusted: an unset
        aux, or a key that is old, bad or failed."""
        if not isinstance(operand, tuple):
            return operand
        ref, aux = operand
        if aux:
            return ref.get_aux_value(aux)
        v, _annunc, old, bad, fail, _secfail = ref.value
        return None if old or bad or fail else v

    def bypassed(self):
        """True only when every bypass condition holds on trusted inputs.

        An input that is old, bad or failed never satisfies a condition, and
        neither does a threshold that is unset or untrusted, so a lost
        sensor or a missing setting leaves the alarm armed.
        """
        if not self.conditions:
            return False
        return all(_holds(c, self._operand_value) for c in self.conditions)

    def evaluate(self, k, x, udata):
        if self.skip_unwritten and not self.item.written:
            self.item.annunciate = False
            return
        # The start bypass clears once the value is greater than the low
        # set point
        if "." in k:  # This is an aux value update
            tokens = k.split(".")
            x = self.plugin.db_read(tokens[0])
        if self.start_bypass_latch and self.low_set_point is not None:
            lp = self.plugin.db_read(self.low_set_point)
            if lp is None:
                lp = self.item.min
            if x[0] > lp:
                self.start_bypass_latch = False
        else:
            self.start_bypass_latch = False
        b = self.bypassed()
        # We are bypassed
        if b or self.start_bypass_latch:
            self.item.annunciate = False
        else:
            if self.warmup is not None and self.warmup.holding():
                self.low_latch = False
            elif self.low_set_point:
                lp = self.plugin.db_read(self.low_set_point)
                if lp is None:
                    lp = self.item.min
                if x[0] < lp or (x[0] < (lp + self.deadband) and self.low_latch):
                    self.low_latch = True
                else:
                    self.low_latch = False
            if self.high_set_point:
                hp = self.plugin.db_read(self.high_set_point)
                if hp is None:
                    hp = self.item.max
                if x[0] > hp or (x[0] > (hp - self.deadband) and self.high_latch):
                    self.high_latch = True
                else:
                    self.high_latch = False
            if self.high_latch or self.low_latch:
                self.item.annunciate = True
            else:
                self.item.annunciate = False

    def callbacks(self):
        return [self.key]

    def __str__(self):
        s = []
        s.append(self.key)
        s.append("  Low Set Point: {}".format(self.low_set_point))
        s.append("  High Set Point: {}".format(self.high_set_point))
        s.append("  Deadband: {}".format(self.deadband))
        s.append(
            "  Start Bypass Enabled: {}".format("Yes" if self.start_bypass else "No")
        )
        s.append("  Conditional Bypass: {}".format(self.cond_bypass))
        if self.warmup is not None:
            s.append("  Warm-up: {}".format(self.warmup))
        if self.skip_unwritten:
            s.append("  Skip Unwritten: Yes")
        return "\n".join(s)


class Warmup(object):
    """Hold an item's low alarm for a warm-up period after engine start.

    A cold engine reads below its low CHT and EGT alarms for its first
    minutes, so those alarms wait ``seconds`` from engine start (Bill,
    AER-6137).  Only the low side waits: a high alarm is live throughout.

    Engine start is the ``start`` condition (``TACH1 >= 500``) becoming
    true while every ``on_ground`` condition (``IAS < IAS.Vgnd``) holds.
    Both are judged as cond_bypass conditions are, so an old, bad or
    failed input never starts the timer.  A start condition that was not
    known to hold before (no reading yet, or an untrusted one) counts as
    stopped, so an engine started before the gateway was up still gets
    its warm-up; the on-ground condition keeps a restarted gateway or a
    recovered tach in flight from starting one.

    ``seconds`` is a number or a FIX value (``TACH1.warmup``) read at each
    evaluation, so the configurator sets it; ``default`` stands in while
    that value is unset.  The timer runs from the start's own callback, so
    it does not wait for the item's next write.
    """

    def __init__(self, owner, spec):
        self.owner = owner
        for name in ("start", "seconds"):
            if name not in spec:
                raise ValueError(
                    "No {} given for warm-up: {}".format(name, owner.key)
                )
        self.start = owner._parse_condition(spec["start"])
        on_ground = spec.get("on_ground") or []
        if isinstance(on_ground, str):
            on_ground = [on_ground]
        self.on_ground = [owner._parse_condition(c) for c in on_ground]
        self.seconds = self._parse_seconds(spec["seconds"])
        default = spec.get("default")
        self.default = None if default is None else float(default)
        self.spec = spec
        self.running = False
        self.started = None
        self.start_key = self.start[0].key
        owner.plugin.db_callback_add(self.start_key, self.watch)

    def _parse_seconds(self, token):
        if isinstance(token, (int, float)) and not isinstance(token, bool):
            return float(token)
        name, _, aux = str(token).partition(".")
        try:
            ref = self.owner.plugin.db_get_item(name) if aux else None
        except KeyError:
            ref = None
        if ref is None or aux not in ref.aux:
            raise ValueError(
                "Unknown value {} for warm-up seconds: {}".format(token, self.owner.key)
            )
        return (ref, aux)

    def watch(self, k, x, udata):
        if "." in k:  # an aux of the start key, not a reading
            return
        operand = self.owner._operand_value
        running = _holds(self.start, operand)
        if (
            running
            and not self.running
            and all(_holds(c, operand) for c in self.on_ground)
        ):
            self.started = _now()
        self.running = running

    def period(self):
        """The warm-up length in seconds, never negative."""
        if isinstance(self.seconds, tuple):
            ref, aux = self.seconds
            value = ref.get_aux_value(aux)
            value = self.default if value is None else value
        else:
            value = self.seconds
        return max(0.0, value or 0.0)

    def holding(self):
        return self.started is not None and _now() - self.started < self.period()

    def stop(self):
        self.owner.plugin.db_callback_del(self.start_key, self.watch)

    def __str__(self):
        return str(self.spec)


class DeviationItem(object):
    """Annunciate a target key when a measured key strays from it.

    Sets the annunciate flag on `key` (the target, e.g. ALTSEL) while
    |deviation_from - key| exceeds `threshold`, holding it until the
    deviation falls to `threshold - deadband`. pyEfis EFIS-ALTSEL-005/-009/
    -010: ALT against ALTSEL, computed here so every client sees it.

    With `arm_on_capture`, the alert only arms once the measured value has
    come within `threshold` of the target, and a new target disarms it: no
    alert on the climb to a newly selected altitude, only on leaving it
    (Bill, AER-4328, 2026-10-31).

    The `defaults` block does not apply: its aux points and percentage
    deadband describe a value against its own limits, not a deviation.
    """

    def __init__(self, plugin, itemdef):
        self.plugin = plugin
        self.key = itemdef["key"]
        self.ref_key = itemdef["deviation_from"]
        self.item = plugin.db_get_item(self.key)
        if self.item is None:
            raise ValueError("Key {} not found".format(self.key))
        try:
            self.ref_item = plugin.db_get_item(self.ref_key)
        except KeyError:
            self.ref_item = None
        if self.ref_item is None:
            raise ValueError(
                "Unknown Key {} for deviation: {}".format(self.ref_key, self.key)
            )
        if "threshold" not in itemdef:
            raise ValueError("No threshold given for deviation: {}".format(self.key))
        self.threshold = float(itemdef["threshold"])
        self.deadband = float(itemdef.get("deadband", 0))
        if self.threshold <= 0 or not 0 <= self.deadband < self.threshold:
            raise ValueError(
                "Deviation {} needs threshold > 0 and 0 <= deadband < threshold".format(
                    self.key
                )
            )
        self.arm_on_capture = bool(itemdef.get("arm_on_capture", False))
        self.armed = not self.arm_on_capture
        self.last_target = None
        self.latch = False
        plugin.db_callback_add(self.key, self.evaluate)
        plugin.db_callback_add(self.ref_key, self.evaluate)

    def evaluate(self, k, x, udata):
        target = self.plugin.db_read(self.key)
        ref = self.plugin.db_read(self.ref_key)
        # A deviation from an invalid value means nothing: clear rather than
        # alert on it. The invalid value carries its own flag to the display.
        if self.arm_on_capture and target[0] != self.last_target:
            self.armed = False                     # a new target: not yet captured
            self.latch = False
        self.last_target = target[0]
        if any(v[2] or v[3] or v[4] for v in (target, ref)):
            self.latch = False
        else:
            dev = abs(ref[0] - target[0])
            if dev <= self.threshold:
                self.armed = True
            self.latch = self.armed and (
                dev > self.threshold
                or (self.latch and dev > self.threshold - self.deadband)
            )
        self.item.annunciate = self.latch

    def callbacks(self):
        return [self.key, self.ref_key]

    def __str__(self):
        s = []
        s.append(self.key)
        s.append("  Deviation From: {}".format(self.ref_key))
        s.append("  Threshold: {}".format(self.threshold))
        s.append("  Deadband: {}".format(self.deadband))
        s.append("  Arm On Capture: {}".format("Yes" if self.arm_on_capture else "No"))
        return "\n".join(s)


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.items = []

    def run(self):
        # This directory of functions are functions that aggregate a list
        # of inputs and produce a single output.
        for item in self.config["items"]:
            if "deviation_from" in item:
                self.items.append(DeviationItem(self, item))
                continue
            # A key named with a database variable is one item per key it
            # stands for: with c: 6, CHTec is CHT11 .. CHT16, so the checks
            # follow the cylinder count (fix-gateway#199).
            for key in database.expand_key(item["key"]):
                itemdef = dict(item, key=key)
                self.items.append(
                    AnnunciateItem(self, self.config["defaults"], itemdef)
                )

    def stop(self):
        for item in self.items:
            for key in item.callbacks():
                self.db_callback_del(key, item.evaluate)
            if getattr(item, "warmup", None) is not None:
                item.warmup.stop()

    def get_status(self):
        """The get_status method should return a dict or OrderedDict that
        is basically a key/value pair of statistics"""
        return OrderedDict({"Item Count": len(self.items)})


# TODO: Add a check for Warns and alarms and annunciate appropriatly
