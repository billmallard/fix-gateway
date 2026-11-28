#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  RAIM availability prediction for one place over a time window.
#
#  RAIM2 (AER-6756; makerplane/briefs/flight_plan_plan.md section 3.3.2):
#  satellites from the almanac (almanac.py), those under the elevation mask,
#  unhealthy in the almanac or inside a NANU outage removed, an optional
#  baro-altimeter pseudo-measurement added, then the snapshot
#  fault-detection test of fd.py at each step against the HAL. The result
#  is available/unavailable per step plus the outage windows. Pure
#  functions, no bus: RAIM3's flightplan engine is the caller.

import math
from dataclasses import dataclass

import numpy as np

from fixgw.raim import almanac as _alm
from fixgw.raim import fd

DEFAULT_MASK_DEG = 5.0

# WGS-84
_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2.0 - _F)


@dataclass(frozen=True)
class Exclusion:
    """A satellite that may not be used from `start_unix` up to `end_unix`.

    A NANU outage (scheduled or unscheduled) as RAIM1's pack lists it.
    `end_unix` None is "until further notice".
    """

    prn: int
    start_unix: float
    end_unix: float = None

    def covers(self, unix_seconds):
        if unix_seconds < self.start_unix:
            return False
        return self.end_unix is None or unix_seconds < self.end_unix


@dataclass(frozen=True)
class Step:
    unix: float
    available: bool
    hpl_m: float  # math.inf with fewer than five measurements
    satellites: int  # satellites used, baro not counted


@dataclass(frozen=True)
class Outage:
    """A run of consecutive unavailable steps.

    `start_unix` and `end_unix` are the first and the last unavailable step,
    so a single unavailable step has start == end. `duration_s` counts the
    steps (one step long for a single one): the time the prediction says
    fault detection is lost, at the step's resolution.
    """

    start_unix: float
    end_unix: float
    step_s: float

    @property
    def duration_s(self):
        return self.end_unix - self.start_unix + self.step_s


@dataclass(frozen=True)
class Prediction:
    steps: tuple
    outages: tuple
    hal_m: float
    step_s: float

    @property
    def available(self):
        """True when every step has fault detection."""
        return not self.outages

    def longest_outage_s(self):
        return max((o.duration_s for o in self.outages), default=0.0)


def geodetic_to_ecef(lat_deg, lon_deg, alt_m):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    n = _A / math.sqrt(1.0 - _E2 * sin_lat * sin_lat)
    return np.array(
        [
            (n + alt_m) * math.cos(lat) * math.cos(lon),
            (n + alt_m) * math.cos(lat) * math.sin(lon),
            (n * (1.0 - _E2) + alt_m) * sin_lat,
        ]
    )


def _enu_rotation(lat_deg, lon_deg):
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sl, cl = math.sin(lat), math.cos(lat)
    so, co = math.sin(lon), math.cos(lon)
    return np.array(
        [
            [-so, co, 0.0],
            [-sl * co, -sl * so, cl],
            [cl * co, cl * so, sl],
        ]
    )


def line_of_sight(entries, lat_deg, lon_deg, alt_m, gps_seconds):
    """Unit east/north/up vectors to each satellite, (steps, sats, 3)."""
    user = geodetic_to_ecef(lat_deg, lon_deg, alt_m)
    sats = _alm.satellite_ecef(entries, gps_seconds)
    rel = sats - user
    rel /= np.linalg.norm(rel, axis=-1, keepdims=True)
    return rel @ _enu_rotation(lat_deg, lon_deg).T


def predict(
    entries,
    lat_deg,
    lon_deg,
    alt_m,
    start_unix,
    end_unix,
    hal_m,
    step_s=60.0,
    mask_deg=DEFAULT_MASK_DEG,
    exclusions=(),
    baro=False,
    baro_sigma_m=None,
    sigma_m=fd.DEFAULT_SIGMA_M,
    pfa=fd.DEFAULT_PFA,
    pmd=fd.DEFAULT_PMD,
    leap_seconds=_alm.GPS_UTC_LEAP_SECONDS,
):
    """Predict fault-detection availability from `start_unix` to `end_unix`.

    Steps run from `start_unix` every `step_s` up to and including
    `end_unix`. `entries` is the almanac (almanac.parse_yuma). A satellite
    is used at a step when its almanac health is 0, no exclusion covers it
    and it is at or above `mask_deg`. With `baro`, a vertical
    pseudo-measurement is added (TSO-C129 baro aiding): it counts toward
    the five measurements fault detection needs and enters the geometry
    with `baro_sigma_m` (default: the satellites' sigma).
    """
    if step_s <= 0:
        raise ValueError("step_s must be positive")
    if end_unix < start_unix:
        raise ValueError("end_unix is before start_unix")
    healthy = [e for e in entries if e.health == 0]
    count = int(math.floor((end_unix - start_unix) / step_s + 1e-9)) + 1
    times = start_unix + step_s * np.arange(count)
    steps = []
    if healthy:
        gps = _alm.unix_to_gps(times, leap_seconds)
        los = line_of_sight(healthy, lat_deg, lon_deg, alt_m, gps)
        visible = los[..., 2] >= math.sin(math.radians(mask_deg))
    baro_row = None
    if baro:
        scale = 1.0 if baro_sigma_m is None else sigma_m / baro_sigma_m
        baro_row = np.array([[0.0, 0.0, scale, 0.0]])
    by_prn = {}
    for x in exclusions:
        by_prn.setdefault(x.prn, []).append(x)
    for k, t in enumerate(times):
        rows = []
        if healthy:
            for j, e in enumerate(healthy):
                if not visible[k, j]:
                    continue
                if any(x.covers(t) for x in by_prn.get(e.prn, ())):
                    continue
                u = los[k, j]
                rows.append((-u[0], -u[1], -u[2], 1.0))
        used = len(rows)
        g = np.array(rows, dtype=float).reshape(-1, 4)
        if baro_row is not None:
            g = np.vstack([g, baro_row])
        level = fd.hpl(g, sigma_m, pfa, pmd)
        steps.append(Step(float(t), level <= hal_m, level, used))
    return Prediction(tuple(steps), _outages(steps, step_s), hal_m, step_s)


def _outages(steps, step_s):
    windows = []
    start = None
    last = None
    for s in steps:
        if not s.available:
            if start is None:
                start = s.unix
            last = s.unix
        elif start is not None:
            windows.append(Outage(start, last, step_s))
            start = None
    if start is not None:
        windows.append(Outage(start, last, step_s))
    return tuple(windows)
