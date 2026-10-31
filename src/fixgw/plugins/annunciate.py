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
from collections import OrderedDict
import fixgw.plugin as plugin


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
        # YAML gives start_bypass: no as False, which must not arm the latch
        self.start_bypass_latch = bool(self.start_bypass)
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
        return (item, oper, item.dtype(tokens[2]))

    def bypassed(self):
        """True only when every bypass condition holds on a trusted input.

        An input that is old, bad or failed never satisfies a condition, so
        a lost or failed bypass sensor leaves the alarm armed.
        """
        if not self.conditions:
            return False
        for item, oper, value in self.conditions:
            v, _annunc, old, bad, fail, _secfail = item.value
            if old or bad or fail or not oper(v, value):
                return False
        return True

    def evaluate(self, k, x, udata):
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
            if self.low_set_point:
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
        return "\n".join(s)


class Plugin(plugin.PluginBase):
    def __init__(self, name, config, config_meta):
        super(Plugin, self).__init__(name, config, config_meta)
        self.items = []

    def run(self):
        # This directory of functions are functions that aggregate a list
        # of inputs and produce a single output.
        for item in self.config["items"]:
            i = AnnunciateItem(self, self.config["defaults"], item)
            self.items.append(i)

    def stop(self):
        for item in self.items:
            self.db_callback_del(item.key, item.evaluate)

    def get_status(self):
        """The get_status method should return a dict or OrderedDict that
        is basically a key/value pair of statistics"""
        return OrderedDict({"Item Count": len(self.items)})


# TODO: Add a check for Warns and alarms and annunciate appropriatly
