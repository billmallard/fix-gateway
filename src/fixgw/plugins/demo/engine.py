#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Demo engine (DEMO3, makerplane/briefs/demo_mode_redesign.md section 6.1).
#
"""A small piston-engine model driven by the flight phase, and the scripted
CHT event.

The demo's route rows already say what the airplane is doing: ``VS`` climbs
and descends, and the reference leg type ``REFPT`` says when it is in the
hold. :func:`phases` turns that into a power phase, and :func:`add_engine`
steps a first-order model through the rows and adds the engine keys
(:data:`ENGINE_KEYS`) once a second. Fast things (RPM, manifold pressure,
fuel flow) follow the throttle in seconds, cylinder heads in about a minute,
the oil in a few. ``FUELQ1``/``FUELQ2`` burn down by ``FUELF1``.

A climb opens at takeoff (go-around) power for :data:`TAKEOFF_S`, then climb
power. In the first climb, one cylinder runs hot: its CHT heads for a point
past the aircraft's configured ``highAlarm``, so the gauge crosses its warn
and then its alarm limit. Once it has sat past the alarm for
:data:`EVENT_HOT_S`, the pilot enriches: fuel flow up, EGTs down, and the
CHT falls back below the warn limit. The demo writes only the CHT. Whether
that is a warning or an alarm is for the configured limits and the shipped
``annunciate`` plugin to say, as it would be in flight.

Everything here is deterministic: the same rows and limits give the same
engine every loop, so a test can read the event off the rows.
"""

import math

ENGINE_KEYS = ("TACH1", "MAP1", "FUELF1", "FUELP1", "OILP1", "OILT1",
               "CHT11", "CHT12", "CHT13", "CHT14", "EGT11", "EGT12", "EGT13", "EGT14",
               "VOLT", "CURRNT", "FUELQ1", "FUELQ2", "OAT")

TAKEOFF, CLIMB, CRUISE, DESCENT, HOLD = "takeoff", "climb", "cruise", "descent", "hold"

# Power by phase: RPM, manifold inHg, fuel gal/hr, then the steady EGT, CHT,
# oil pressure and oil temperature it settles to, a fixed-pitch 4-cylinder
# inside the shipped init_data/default.ini limits.
POWER = {
    TAKEOFF: dict(TACH1=2550, MAP1=27.5, FUELF1=12.5, EGT=540.0, CHT=192.0, OILP1=78.0, OILT1=88.0),
    CLIMB:   dict(TACH1=2450, MAP1=25.5, FUELF1=10.5, EGT=570.0, CHT=188.0, OILP1=75.0, OILT1=90.0),
    CRUISE:  dict(TACH1=2350, MAP1=23.0, FUELF1=8.2, EGT=600.0, CHT=175.0, OILP1=70.0, OILT1=87.0),
    HOLD:    dict(TACH1=2200, MAP1=20.0, FUELF1=6.8, EGT=580.0, CHT=165.0, OILP1=68.0, OILT1=85.0),
    DESCENT: dict(TACH1=2100, MAP1=17.0, FUELF1=5.0, EGT=470.0, CHT=150.0, OILP1=66.0, OILT1=83.0),
}
# First-order time constants, seconds
TAU = dict(TACH1=2.0, MAP1=2.0, FUELF1=2.0, OILP1=4.0, EGT=8.0, CHT=45.0, OILT1=120.0)
CHT_OFFSET = (-3.0, 2.0, 5.0, -4.0)     # cylinders 1-4 do not read alike
EGT_OFFSET = (-10.0, 6.0, 12.0, -8.0)

VS_CLIMB_FPM = 300.0
VS_DESCENT_FPM = -300.0
TAKEOFF_S = 60.0
FUEL_START_GAL = 18.0                   # each tank, at the top of every loop

# The scripted event
HOT_CYLINDER = 3
EVENT_DELAY_S = 20.0                    # into the first climb
EVENT_OVER_ALARM = 10.0                 # the hot CHT heads for highAlarm + this
EVENT_TAU = 25.0
EVENT_HOT_S = 20.0                      # past the alarm before the pilot enriches
ENRICH_FUELF = 1.8
ENRICH_EGT = -45.0


def phases(rows):
    """The power phase of each row: hold on an ``HM`` leg, climb or descent
    by ``VS``, cruise otherwise. A climb's first :data:`TAKEOFF_S` is takeoff
    power; a level-off shorter than 10 s does not end the climb."""
    out = []
    phase = CRUISE
    vs = 0.0
    climb_t0 = None
    left_climb_t = None
    for t, row in rows:
        vs = row.get("VS", vs)
        if row.get("REFPT", "") == "HM":
            phase = HOLD
        elif vs > VS_CLIMB_FPM:
            phase = CLIMB
        elif vs < VS_DESCENT_FPM:
            phase = DESCENT
        else:
            phase = CRUISE
        if phase == CLIMB:
            if climb_t0 is None or (left_climb_t is not None and t - left_climb_t > 10.0):
                climb_t0 = t
            left_climb_t = None
            if t - climb_t0 < TAKEOFF_S:
                phase = TAKEOFF
        elif climb_t0 is not None and left_climb_t is None:
            left_climb_t = t
        out.append(phase)
    return out


def event_limits(warn, alarm):
    """(warn, alarm) when both are set and alarm is above warn, else None."""
    try:
        warn, alarm = float(warn), float(alarm)
    except (TypeError, ValueError):
        return None
    return (warn, alarm) if alarm > warn else None


def ambient_inhg(alt_ft):
    return 29.92 * (1.0 - 6.8756e-6 * alt_ft) ** 5.2559


def _jitter(t, period, amp, phase=0.0):
    return amp * math.sin(2.0 * math.pi * t / period + phase)


class Engine:
    """The model's state, stepped row by row. ``limits`` is the hot
    cylinder's (highWarn, highAlarm) or None for no event."""

    def __init__(self, limits=None, first=CRUISE):
        p = POWER[first]
        self.v = {k: float(p[k]) for k in ("TACH1", "MAP1", "FUELF1", "OILP1", "OILT1")}
        self.cht = [p["CHT"] + o for o in CHT_OFFSET]
        self.egt = [p["EGT"] + o for o in EGT_OFFSET]
        self.fuel = [FUEL_START_GAL, FUEL_START_GAL]
        self.limits = limits
        self.event = "pending" if limits else "none"
        self.climbs = 0
        self.climb_t0 = None
        self.hot_since = None
        self.rich = False
        self.marks = []                 # [(t, what)] for the status and the tests

    def _lag(self, value, target, tau, dt):
        return value + (target - value) * (1.0 - math.exp(-dt / tau))

    def step(self, t, dt, phase, alt_ft):
        p = POWER[phase]
        climbing = phase in (TAKEOFF, CLIMB)
        if climbing and self.climb_t0 is None:
            self.climb_t0 = t
            self.climbs += 1
        elif not climbing:
            self.climb_t0 = None
            self.rich = False

        hot = HOT_CYLINDER - 1
        if self.event == "pending" and self.climbs == 1 and climbing and t - self.climb_t0 >= EVENT_DELAY_S:
            self.event = "hot"
            self.marks.append((t, "hot"))
        if self.event == "hot":
            if self.cht[hot] > self.limits[1]:
                if self.hot_since is None:
                    self.hot_since = t
                elif t - self.hot_since >= EVENT_HOT_S:
                    self.event = "enriched"
                    self.rich = True
                    self.marks.append((t, "enrich"))
        elif self.event == "enriched" and self.cht[hot] < self.limits[0]:
            self.event = "done"
            self.marks.append((t, "recovered"))

        rich = self.rich or self.event == "enriched"
        targets = dict(p)
        targets["MAP1"] = min(p["MAP1"], ambient_inhg(alt_ft) - 0.5)
        if rich:
            targets["FUELF1"] = p["FUELF1"] + ENRICH_FUELF
        for k in ("TACH1", "MAP1", "FUELF1", "OILP1", "OILT1"):
            self.v[k] = self._lag(self.v[k], targets[k], TAU[k], dt)
        for i in range(4):
            target = p["CHT"] + CHT_OFFSET[i]
            tau = TAU["CHT"]
            if i == hot and self.event == "hot":
                target, tau = self.limits[1] + EVENT_OVER_ALARM, EVENT_TAU
            elif i == hot and self.event == "enriched":
                tau = EVENT_TAU
            self.cht[i] = self._lag(self.cht[i], target, tau, dt)
            egt = p["EGT"] + EGT_OFFSET[i] + (ENRICH_EGT if rich else 0.0)
            self.egt[i] = self._lag(self.egt[i], egt, TAU["EGT"], dt)
        burn = self.v["FUELF1"] * dt / 3600.0 / 2.0
        self.fuel = [max(0.0, q - burn) for q in self.fuel]

    def values(self, t, alt_ft):
        """The keys as the sensors would read them at ``t``."""
        out = {
            "TACH1": int(round(self.v["TACH1"] + _jitter(t, 7.0, 6.0))),
            "MAP1": round(self.v["MAP1"] + _jitter(t, 5.0, 0.05), 2),
            "FUELF1": round(self.v["FUELF1"] + _jitter(t, 9.0, 0.05), 2),
            "FUELP1": round(40.5 + _jitter(t, 11.0, 0.3), 1),
            "OILP1": round(self.v["OILP1"] + _jitter(t, 13.0, 0.4), 1),
            "OILT1": round(self.v["OILT1"], 1),
            "VOLT": round(14.1 + _jitter(t, 17.0, 0.04), 2),
            "CURRNT": round(14.0 + _jitter(t, 23.0, 0.6), 1),
            "FUELQ1": round(self.fuel[0], 2),
            "FUELQ2": round(self.fuel[1], 2),
            "OAT": round(15.0 - 1.98 * alt_ft / 1000.0, 1),
        }
        for i in range(4):
            out["CHT1{}".format(i + 1)] = round(self.cht[i] + _jitter(t, 19.0, 0.3, i), 1)
            out["EGT1{}".format(i + 1)] = round(self.egt[i] + _jitter(t, 3.0, 1.5, i), 1)
        return out


def add_engine(rows, limits=None):
    """``rows`` with the engine keys added on the first row of each whole
    second, and the :class:`Engine` that produced them (its ``marks`` say
    when the event ran). ``limits`` as for :class:`Engine`."""
    ph = phases(rows)
    eng = Engine(limits, first=ph[0] if ph else CRUISE)
    out = []
    alt = 0.0
    last_t = None
    sec = None
    for (t, row), phase in zip(rows, ph):
        alt = row.get("ALT", alt)
        eng.step(t, 0.0 if last_t is None else t - last_t, phase, alt)
        last_t = t
        if sec != int(t):
            sec = int(t)
            row = dict(row)
            row.update(eng.values(t, alt))
        out.append((t, row))
    return out, eng
