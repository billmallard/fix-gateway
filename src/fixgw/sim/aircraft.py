#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Point-mass aircraft for the synthetic flight tier (SYN1).
#
"""A simple aircraft and autopilot: constant true airspeed, turns at rate
one (or the bank limit), fixed climb and descent rates, optional steady
wind. Its autopilot steers on the reference navigator's guidance, the way
X-Plane's autopilot steers on X-Plane's FMS in the other tier, and can be
told to hold a lateral offset (the profile's ``offset_nm`` event).

Nothing here is meant to be a flight model. It exists to put a plausible,
repeatable track under both navigators.
"""

import math
from dataclasses import dataclass

from fixgw.sim.refnav import G_MPS2, KT_TO_MPS, direct, wrap180, wrap360


@dataclass
class Performance:
    tas_kt: float = 110.0
    climb_fpm: float = 700.0
    descent_fpm: float = 600.0
    max_descent_fpm: float = 1200.0
    bank_max_deg: float = 25.0
    turn_rate_deg_s: float = 3.0
    intercept_deg: float = 45.0      # largest intercept angle the autopilot uses
    intercept_gain_deg_per_nm: float = 150.0


@dataclass
class State:
    lat: float
    lon: float
    alt_ft: float
    heading_true: float
    track_true: float = 0.0
    gs_kt: float = 0.0
    vs_fpm: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0


class Aircraft:
    def __init__(self, perf, state, wind_from_deg=0.0, wind_kt=0.0):
        self.p = perf
        self.s = state
        self.wind_from = wind_from_deg
        self.wind_kt = wind_kt
        self.offset_nm = 0.0
        self._ground_vector()

    def _ground_vector(self):
        s = self.s
        h = math.radians(s.heading_true)
        w = math.radians(self.wind_from + 180.0)          # wind blows toward
        ve = self.p.tas_kt * math.sin(h) + self.wind_kt * math.sin(w)
        vn = self.p.tas_kt * math.cos(h) + self.wind_kt * math.cos(w)
        s.gs_kt = math.hypot(ve, vn)
        s.track_true = wrap360(math.degrees(math.atan2(ve, vn)))

    def _heading_for_track(self, track):
        # wind correction angle for a commanded ground track
        if self.wind_kt <= 0.0:
            return track
        rel = math.radians(self.wind_from - track)
        x = max(-1.0, min(1.0, self.wind_kt * math.sin(rel) / self.p.tas_kt))
        return wrap360(track + math.degrees(math.asin(x)))

    def max_turn_rate(self):
        v = self.p.tas_kt * KT_TO_MPS
        bank_rate = math.degrees(G_MPS2 * math.tan(math.radians(self.p.bank_max_deg)) / v)
        return min(self.p.turn_rate_deg_s, bank_rate)

    def step(self, dt, dtk_true, xtk_nm, target_alt_ft, turn_hint="", vs_limit_fpm=None):
        """Advance ``dt`` seconds steering to the desired track ``dtk_true``
        from cross-track ``xtk_nm`` (right positive), holding the current
        offset, and climbing or descending toward ``target_alt_ft``."""
        s, p = self.s, self.p
        err = xtk_nm - self.offset_nm
        correction = max(-p.intercept_deg, min(p.intercept_deg, err * p.intercept_gain_deg_per_nm))
        cmd_track = wrap360(dtk_true - correction)
        cmd_heading = self._heading_for_track(cmd_track)
        diff = wrap180(cmd_heading - s.heading_true)
        if turn_hint and abs(diff) > 90.0:
            # a hold entry asks for the long way round
            if turn_hint == "R" and diff < 0:
                diff += 360.0
            elif turn_hint == "L" and diff > 0:
                diff -= 360.0
        rate_max = self.max_turn_rate()
        rate = max(-rate_max, min(rate_max, diff * 0.5))
        s.heading_true = wrap360(s.heading_true + rate * dt)
        v = p.tas_kt * KT_TO_MPS
        s.roll_deg = math.degrees(math.atan(v * math.radians(rate) / G_MPS2))

        # vertical
        dz = target_alt_ft - s.alt_ft
        if dz > 0:
            vs = min(p.climb_fpm, dz * 60.0 / dt) if dt > 0 else 0.0
        else:
            lim = vs_limit_fpm if vs_limit_fpm is not None else p.descent_fpm
            vs = max(-lim, dz * 60.0 / dt) if dt > 0 else 0.0
        s.vs_fpm = vs
        s.alt_ft += vs * dt / 60.0
        s.pitch_deg = math.degrees(math.atan2(vs / 60.0 * 0.3048, v)) + 2.0

        self._ground_vector()
        dist_nm = s.gs_kt * dt / 3600.0
        if dist_nm > 0:
            s.lat, s.lon = direct(s.lat, s.lon, s.track_true, dist_nm)
