#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  GPS almanac: the YUMA text format and satellite positions from it.
#
#  RAIM2 (AER-6756; makerplane/briefs/flight_plan_plan.md section 3.3.2).
#  The orbit model is the one IS-GPS-200 gives for the almanac: section
#  20.3.3.5.2.1 says to use the Table 20-IV ephemeris user algorithm with the
#  almanac parameters and every harmonic correction term set to zero, toa in
#  place of toe. The constants are the IS-GPS-200 WGS-84 values.
#
#  YUMA is the USCG NAVCEN text form of the almanac (the one RAIM1's `gnss`
#  pack carries). Its "Orbital Inclination" is the full inclination i0, not
#  the delta-i from 0.3 pi the broadcast message and the SEM format use, and
#  its week is the broadcast week modulo 1024.

from dataclasses import dataclass

import numpy as np

# IS-GPS-200 20.3.3.4.3: WGS-84 values for the user algorithm.
MU = 3.986005e14  # m^3/s^2
OMEGA_E_DOT = 7.2921151467e-5  # rad/s

SECONDS_PER_WEEK = 604800
# 1980-01-06T00:00:00Z, the GPS epoch, as a Unix time.
GPS_EPOCH_UNIX = 315964800
# GPS - UTC. 18 s since 2017-01-01; no leap second has been announced since.
# A caller that holds a newer value passes it to predict().
GPS_UTC_LEAP_SECONDS = 18


@dataclass(frozen=True)
class AlmanacEntry:
    """One satellite's almanac, in IS-GPS-200 units (radians, seconds, metres)."""

    prn: int
    health: int
    eccentricity: float
    toa: float  # seconds of the almanac week
    inclination: float  # i0, rad
    omega_dot: float  # rate of right ascension, rad/s
    sqrt_a: float  # m^0.5
    omega0: float  # right ascension at week, rad
    arg_perigee: float  # omega, rad
    mean_anomaly: float  # M0, rad
    af0: float
    af1: float
    week: int  # full GPS week (not modulo 1024)

    @property
    def toa_gps_seconds(self):
        """The time of applicability as seconds since the GPS epoch."""
        return self.week * SECONDS_PER_WEEK + self.toa


class AlmanacError(ValueError):
    pass


_YUMA_FIELDS = {
    "id": "prn",
    "health": "health",
    "eccentricity": "eccentricity",
    "time of applicability(s)": "toa",
    "orbital inclination(rad)": "inclination",
    "rate of right ascen(r/s)": "omega_dot",
    "sqrt(a)  (m 1/2)": "sqrt_a",
    "right ascen at week(rad)": "omega0",
    "argument of perigee(rad)": "arg_perigee",
    "mean anom(rad)": "mean_anomaly",
    "af0(s)": "af0",
    "af1(s/s)": "af1",
    "week": "week",
}


def resolve_week(week_mod_1024, near_gps_seconds):
    """The full GPS week congruent to `week_mod_1024` nearest a reference time."""
    ref_week = near_gps_seconds / SECONDS_PER_WEEK
    base = week_mod_1024 % 1024
    k = round((ref_week - base) / 1024.0)
    return base + 1024 * k


def parse_yuma(text, near_unix):
    """Parse a YUMA almanac file into a list of AlmanacEntry.

    `near_unix` is any Unix time within ~9 years of the almanac (the
    prediction time does), used to undo the modulo-1024 week. A file whose
    records are incomplete or unreadable raises AlmanacError: a prediction
    from half an almanac would quietly drop satellites.
    """
    near_gps = near_unix - GPS_EPOCH_UNIX + GPS_UTC_LEAP_SECONDS
    entries = []
    record = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("*"):
            if record is not None:
                entries.append(_finish(record, near_gps))
            record = {}
            continue
        if record is None or ":" not in line:
            raise AlmanacError(f"line {lineno}: not a YUMA record line: {raw!r}")
        label, _, value = line.partition(":")
        field = _YUMA_FIELDS.get(label.strip().lower())
        if field is None:
            raise AlmanacError(f"line {lineno}: unknown YUMA field {label!r}")
        try:
            record[field] = float(value)
        except ValueError:
            raise AlmanacError(f"line {lineno}: bad number {value!r}") from None
    if record is not None:
        entries.append(_finish(record, near_gps))
    if not entries:
        raise AlmanacError("no almanac records")
    prns = [e.prn for e in entries]
    if len(set(prns)) != len(prns):
        raise AlmanacError("a PRN appears twice")
    return entries


def _finish(record, near_gps):
    missing = set(_YUMA_FIELDS.values()) - set(record)
    if missing:
        raise AlmanacError(f"record missing {sorted(missing)}")
    record["prn"] = int(record["prn"])
    record["health"] = int(record["health"])
    record["week"] = resolve_week(int(record["week"]), near_gps)
    return AlmanacEntry(**record)


def _solve_kepler(mean_anomaly, e):
    ecc_anomaly = mean_anomaly.copy()
    for _ in range(10):
        ecc_anomaly = mean_anomaly + e * np.sin(ecc_anomaly)
    return ecc_anomaly


def satellite_ecef(entries, gps_seconds):
    """ECEF positions (m) of each almanac entry at each GPS time.

    Returns an array shaped (len(gps_seconds), len(entries), 3). The model is
    IS-GPS-200 Table 20-IV with the almanac's parameters, as section
    20.3.3.5.2.1 directs, so the precision is the almanac's (a few km over a
    few days), which is ample for geometry.
    """
    t = np.asarray(gps_seconds, dtype=float).reshape(-1, 1)
    e = np.array([x.eccentricity for x in entries])
    a = np.array([x.sqrt_a for x in entries]) ** 2
    toa = np.array([x.toa for x in entries])
    tk = t - np.array([x.toa_gps_seconds for x in entries])
    n0 = np.sqrt(MU / a**3)
    mk = np.array([x.mean_anomaly for x in entries]) + n0 * tk
    ek = _solve_kepler(mk, e)
    vk = np.arctan2(np.sqrt(1.0 - e**2) * np.sin(ek), np.cos(ek) - e)
    phik = vk + np.array([x.arg_perigee for x in entries])
    rk = a * (1.0 - e * np.cos(ek))
    ik = np.array([x.inclination for x in entries])
    xp = rk * np.cos(phik)
    yp = rk * np.sin(phik)
    omk = (
        np.array([x.omega0 for x in entries])
        + (np.array([x.omega_dot for x in entries]) - OMEGA_E_DOT) * tk
        - OMEGA_E_DOT * toa
    )
    cos_o, sin_o, cos_i = np.cos(omk), np.sin(omk), np.cos(ik)
    out = np.empty(tk.shape + (3,))
    out[..., 0] = xp * cos_o - yp * cos_i * sin_o
    out[..., 1] = xp * sin_o + yp * cos_i * cos_o
    out[..., 2] = yp * np.sin(ik)
    return out


def unix_to_gps(unix_seconds, leap_seconds=GPS_UTC_LEAP_SECONDS):
    return unix_seconds - GPS_EPOCH_UNIX + leap_seconds


def almanac_age_days(entries, unix_seconds, leap_seconds=GPS_UTC_LEAP_SECONDS):
    """Days from the almanac's latest time of applicability to `unix_seconds`."""
    toa = max(x.toa_gps_seconds for x in entries)
    return (unix_to_gps(unix_seconds, leap_seconds) - toa) / 86400.0

