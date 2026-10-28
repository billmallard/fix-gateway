#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Independent reference navigator for the synthetic flight tier (SYN1).
#
"""The truth side of a synthetic flight: course, cross-track, CDI, distance,
TO waypoint and leg type (``REFCRS REFXTK REFCDI REFDIS REFWPT REFPT``),
computed from the
procedure's ARINC 424 legs and the aircraft's true position.

**The rule this module exists to keep:** it imports nothing from the
navigation engine under test (``fixgw.plugins.flightplan``) or from the
geometry it uses (``fixgw.geo``). Geometry is WGS-84 geodesics from
GeographicLib, leg sequencing and CDI scaling are written here from the
standards, not from the engine. If the two ever share code, a bug in that
code makes both sides wrong in the same way and the comparison proves
nothing. ``tests/sim/test_independence.py`` fails if an import creeps in.

Behaviour, as a navigator built to TSO-C129/C146 and DO-229 would show it:

- Straight legs (``IF``/``TF``/``DF``/``CA``) are geodesics. ``TF`` runs
  from the previous fix, ``DF`` from the position where the leg became
  active, ``CA`` along its coded course from the end of the previous leg
  and ends when the altitude is reached.
- A fly-by fix sequences at the start of the turn (turn anticipation
  ``R * tan(delta/2)``, rate-one or 25 degree bank, whichever is the wider
  turn); a fly-over fix, and a fix with nothing after it, sequence abeam.
- The MAP never sequences on its own. Guidance continues FROM the MAP on
  the final course until the pilot initiates the missed approach
  (:meth:`ReferenceNavigator.go_missed`).
- An ``HM`` hold is flown as the published racetrack with a standard entry
  (direct, teardrop or parallel, AIM 5-3-8 sectors) and never ends on its
  own.
- CDI full scale: 2.0 nm en route, 1.0 nm within 30 nm of the destination,
  ramping linearly to 0.3 nm over the 2 nm before the FAF, 0.3 nm from the
  FAF until the missed approach is initiated, then 1.0 nm.

Sign conventions are the bus contract's (``doc/flightplan_keys.md``):
cross-track positive right of the desired track, CDI ``= -xtk / scale``
clamped to +-1, courses magnetic with ``MAG = TRUE + MAGVAR`` (``MAGVAR``
positive west).
"""

import math
from dataclasses import dataclass

from geographiclib.geodesic import Geodesic

WGS84 = Geodesic.WGS84
M_PER_NM = 1852.0
G_MPS2 = 9.80665
KT_TO_MPS = M_PER_NM / 3600.0

SUPPORTED = ("IF", "TF", "DF", "CA", "HM")


class ReferenceError_(Exception):
    pass


def wrap360(a):
    return a % 360.0


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def inverse(lat1, lon1, lat2, lon2):
    """(distance nm, azimuth at 1, azimuth at 2), azimuths true degrees."""
    r = WGS84.Inverse(lat1, lon1, lat2, lon2)
    return r["s12"] / M_PER_NM, wrap360(r["azi1"]), wrap360(r["azi2"])


def direct(lat, lon, azimuth, dist_nm):
    r = WGS84.Direct(lat, lon, azimuth, dist_nm * M_PER_NM)
    return r["lat2"], r["lon2"]


def turn_radius_nm(gs_kt, rate_deg_s=3.0, bank_max_deg=25.0):
    """Rate-one turn radius, or the bank-limited one if that is wider."""
    v = max(gs_kt, 1.0) * KT_TO_MPS
    r_rate = v / math.radians(rate_deg_s)
    r_bank = v * v / (G_MPS2 * math.tan(math.radians(bank_max_deg)))
    return max(r_rate, r_bank) / M_PER_NM


class GeoLine:
    """A geodesic from (lat, lon) along ``azimuth``; ``length_nm`` None for
    an unbounded line (a CA course)."""

    def __init__(self, lat, lon, azimuth, length_nm=None):
        self.lat, self.lon, self.azimuth, self.length_nm = lat, lon, wrap360(azimuth), length_nm
        self._line = WGS84.Line(lat, lon, azimuth)

    @classmethod
    def between(cls, lat1, lon1, lat2, lon2):
        d, az1, _ = inverse(lat1, lon1, lat2, lon2)
        return cls(lat1, lon1, az1, d)

    def position(self, s_nm):
        p = self._line.Position(s_nm * M_PER_NM)
        return p["lat2"], p["lon2"], wrap360(p["azi2"])

    def project(self, lat, lon):
        """(along-track nm from the start, cross-track nm right-positive,
        line azimuth at the abeam point). The abeam point is found by
        iterating to where the geodesic to the aircraft leaves the line at
        90 degrees -- exact on the ellipsoid, converges in a few steps at
        the distances a leg spans."""
        d, az, _ = inverse(self.lat, self.lon, lat, lon)
        s = d * math.cos(math.radians(az - self.azimuth))
        for _ in range(20):
            plat, plon, pazi = self.position(s)
            d, az_p, _ = inverse(plat, plon, lat, lon)
            ds = d * math.cos(math.radians(az_p - pazi))
            s += ds
            if abs(ds) < 1e-7:
                break
        plat, plon, pazi = self.position(s)
        d, az_p, _ = inverse(plat, plon, lat, lon)
        xtk = d * math.sin(math.radians(az_p - pazi))
        return s, xtk, pazi


@dataclass
class RefLeg:
    """One leg as the reference reads it from ARINC 424."""
    pt: str
    ident: str = ""
    lat: float = None
    lon: float = None
    flyover: bool = False
    faf: bool = False
    map: bool = False
    first_missed: bool = False
    course_true: float = None     # CA, HM inbound
    alt_ft: float = None          # CA termination
    turn: str = ""                # HM
    leg_nm: float = None          # HM straight-leg length (distance-coded)
    leg_min: float = None         # HM straight-leg time (time-coded, "T010")


def legs_from_cifp(cifp, airport, approach, transition, magvar_deg):
    """The approach as reference legs: ``transition`` then the final and
    missed approach, with the transition's last fix and the final's IF
    (the same fix, coded twice) merged. Unsupported leg types refuse the
    whole procedure -- the reference never flies a guess."""
    raw = cifp.approach_legs(airport, approach)
    trans = [r for r in raw if r.route_type == "A" and r.transition == transition]
    final = [r for r in raw if r.route_type != "A"]
    if not trans:
        raise ReferenceError_(f"{airport} {approach}: no transition {transition}")
    seq = trans + final
    legs = []
    for r in seq:
        if r.path_term not in SUPPORTED:
            raise ReferenceError_(f"{airport} {approach}: leg {r.path_term} {r.fix} not supported by the reference")
        if r.path_term == "IF" and legs and legs[-1].ident == r.fix:
            continue
        leg = RefLeg(pt=r.path_term, ident=r.fix, flyover=r.flyover, faf=r.is_faf,
                     map=r.is_map, first_missed=r.first_missed)
        if r.fix:
            f = cifp.fix(r.fix, airport=airport if r.fix_section[0] == "P" else None,
                         section=r.fix_section if r.fix_section.strip() else None)
            leg.lat, leg.lon = f.lat, f.lon
        if r.course_mag is not None:
            # MAG = TRUE + MAGVAR (positive west)  =>  TRUE = MAG - MAGVAR
            leg.course_true = wrap360(r.course_mag - magvar_deg)
        if r.path_term == "CA":
            if r.alt_desc not in ("+", "") or not r.alt1:
                raise ReferenceError_(f"CA leg without a climb altitude ({r.alt_desc!r}{r.alt1!r})")
            leg.alt_ft = float(r.alt1)
        if r.path_term == "HM":
            if r.turn not in ("L", "R") or not r.dist_or_time or r.course_mag is None:
                raise ReferenceError_("HM leg without turn, course or length")
            if r.dist_or_time.startswith("T"):
                leg.leg_min = int(r.dist_or_time[1:]) / 10.0
            else:
                leg.leg_nm = int(r.dist_or_time) / 10.0
            leg.turn = r.turn
        legs.append(leg)
    return legs


@dataclass
class RefOutput:
    crs_mag: float
    xtk_nm: float
    cdi: float
    dis_nm: float
    wpt: str
    pt: str
    scale_nm: float
    dtk_true: float
    turn_hint: str = ""           # "L"/"R" when the next heading change must go one way
    phase: str = ""
    leg_index: int = 0


@dataclass
class Sequence:
    t: float
    from_ident: str
    to_ident: str
    leg_index: int


class _Hold:
    """Racetrack geometry in a local azimuthal-equidistant plane at the
    hold fix (x east, y north, nm) -- exact for distances from the fix, and
    the distortion elsewhere is far below a millimetre over a hold."""

    def __init__(self, leg, radius_nm, arrival_track_true, leg_nm):
        self.leg = leg
        self.R = radius_nm
        self.L = leg_nm
        self.left = leg.turn == "L"
        self.inb = leg.course_true
        self.u = _vec(self.inb)
        # Holding side: left of the inbound course for left turns.
        self.n = _vec(self.inb - 90.0) if self.left else _vec(self.inb + 90.0)
        o = wrap360(self.inb + 180.0)
        delta = wrap180(arrival_track_true - o)
        to_hold_side = delta if self.left else -delta
        if 0.0 <= to_hold_side <= 70.0:
            self.entry = "TEARDROP"
            self.entry_course = wrap360(o + (30.0 if self.left else -30.0))
            self.entry_turn = "L" if self.left else "R"      # in the hold direction
        elif -110.0 <= to_hold_side < 0.0:
            self.entry = "PARALLEL"
            self.entry_course = o
            self.entry_turn = "R" if self.left else "L"      # toward the holding side
        else:
            self.entry = "DIRECT"
            self.entry_course = None
            self.entry_turn = ""
        self.phase = "ENTRY" if self.entry_course is not None else "TURN1"
        self.circuits = 0

    def xy(self, lat, lon):
        d, az, _ = inverse(self.leg.lat, self.leg.lon, lat, lon)
        return d * math.sin(math.radians(az)), d * math.cos(math.radians(az))

    def guidance(self, lat, lon, entry_len_nm):
        """(dtk_true, xtk_nm, turn_hint) after advancing the phase."""
        p = self.xy(lat, lon)
        u, n, R, L = self.u, self.n, self.R, self.L
        ph = self.phase
        if ph == "ENTRY":
            d = _vec(self.entry_course)
            if _dot(p, d) >= entry_len_nm:
                ph = "RETURN"
        if ph == "RETURN" and _dot(p, u) >= 0.0 and _dot(p, n) > -R:
            ph = "TURN1"
            self.circuits += 1
        c1 = _mul(n, R)
        if ph == "TURN1":
            q = _sub(p, c1)
            if _dot(q, u) <= 0.0 and _dot(q, n) > 0.0:
                ph = "OUTBOUND"
        if ph == "OUTBOUND" and _dot(p, u) <= -L:
            ph = "TURN2"
        c2 = _sub(_mul(n, R), _mul(u, L))
        if ph == "TURN2":
            q = _sub(p, c2)
            if _dot(q, n) < 0.0 and _dot(q, u) >= 0.0:
                ph = "INBOUND"
        if ph == "INBOUND" and _dot(p, u) >= 0.0:
            ph = "TURN1"
            self.circuits += 1
        self.phase = ph
        turn = "L" if self.left else "R"
        if ph == "ENTRY":
            d = _vec(self.entry_course)
            return self.entry_course, _right_of(p, (0.0, 0.0), d), ""
        if ph == "RETURN":
            return self.inb, _right_of(p, (0.0, 0.0), u), self.entry_turn
        if ph == "OUTBOUND":
            return wrap360(self.inb + 180.0), _right_of(p, _mul(n, 2 * R), _mul(u, -1.0)), ""
        if ph == "INBOUND":
            return self.inb, _right_of(p, (0.0, 0.0), u), ""
        centre = c1 if ph == "TURN1" else c2
        q = _sub(p, centre)
        r = math.hypot(*q) or 1e-9
        if self.left:
            tangent = (-q[1], q[0])        # counter-clockwise
            xtk = r - R                     # outside a left turn is right of track
        else:
            tangent = (q[1], -q[0])
            xtk = R - r
        return wrap360(math.degrees(math.atan2(tangent[0], tangent[1]))), xtk, turn


def _vec(az):
    a = math.radians(az)
    return math.sin(a), math.cos(a)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _sub(a, b):
    return a[0] - b[0], a[1] - b[1]


def _mul(a, k):
    return a[0] * k, a[1] * k


def _right_of(p, start, d):
    # signed distance of p from the line through start along unit d, right positive
    q = _sub(p, start)
    return q[0] * d[1] - q[1] * d[0]


class ReferenceNavigator:
    """Flies ``legs`` from the leg into ``legs[active]``.

    ``destination`` is the (lat, lon) the 30 nm terminal radius is measured
    from. ``tas_kt`` sizes the hold and its entry leg (one minute).
    ``position`` is where the aircraft is, needed only to start on a ``DF``.
    """

    def __init__(self, legs, magvar_deg, destination, active, tas_kt,
                 turn_rate_deg_s=3.0, bank_max_deg=25.0, terminal_nm=30.0, position=None):
        if not 0 < active < len(legs):
            raise ReferenceError_(f"active leg {active} out of range")
        self.legs = legs
        self.magvar = magvar_deg
        self.dest = destination
        self.tas = tas_kt
        self.rate = turn_rate_deg_s
        self.bank = bank_max_deg
        self.terminal_nm = terminal_nm
        self.i = active
        self.missed = False
        self.map_suspended = False
        self.sequences = []
        self._hold = None
        self._line = None
        self._start_leg(position)
        self._faf = next((k for k, lg in enumerate(legs) if lg.faf), None)
        self._map = next((k for k, lg in enumerate(legs) if lg.map), None)

    # -- legs ----------------------------------------------------------------
    def _prev_fix(self, k):
        for j in range(k - 1, -1, -1):
            if self.legs[j].lat is not None:
                return self.legs[j]
        return None

    def _start_leg(self, position, arrival_track=None):
        leg = self.legs[self.i]
        self._hold = None
        if leg.pt in ("TF", "IF"):
            prev = self._prev_fix(self.i)
            self._line = GeoLine.between(prev.lat, prev.lon, leg.lat, leg.lon)
        elif leg.pt == "DF":
            if position is None:
                raise ReferenceError_("cannot start on a DF leg without a position")
            self._line = GeoLine.between(position[0], position[1], leg.lat, leg.lon)
        elif leg.pt == "CA":
            prev = self._prev_fix(self.i)
            self._line = GeoLine(prev.lat, prev.lon, leg.course_true)
        elif leg.pt == "HM":
            self._line = None
            radius = turn_radius_nm(self.tas, self.rate, self.bank)
            leg_nm = leg.leg_nm if leg.leg_nm is not None else self.tas * leg.leg_min / 60.0
            track = arrival_track if arrival_track is not None else leg.course_true
            self._hold = _Hold(leg, radius, track, leg_nm)

    def _next_course_true(self, k):
        """Initial true course of leg k+1 at the fix that ends leg k."""
        if k + 1 >= len(self.legs):
            return None
        nxt = self.legs[k + 1]
        cur = self.legs[k]
        if nxt.pt in ("TF", "DF") and nxt.lat is not None:
            return inverse(cur.lat, cur.lon, nxt.lat, nxt.lon)[1]
        if nxt.pt == "CA":
            return nxt.course_true
        return None

    def _sequence(self, t, position, arrival_track):
        frm = self.legs[self.i].ident
        self.i += 1
        self._start_leg(position, arrival_track)
        self.sequences.append(Sequence(t, frm, self.legs[self.i].ident, self.i))

    def go_missed(self, t, position):
        """The pilot initiates the missed approach (RESUME at the MAP)."""
        if not self.map_suspended:
            raise ReferenceError_("go_missed before the MAP")
        self.map_suspended = False
        self.missed = True
        self._sequence(t, position, None)

    # -- scaling -------------------------------------------------------------
    def _scale(self, lat, lon):
        if self.missed:
            return 1.0, "MISSED"
        if self._faf is not None and self._map is not None:
            if self._faf < self.i <= self._map or self.map_suspended:
                return 0.3, "FINAL"
            if self.i == self._faf:
                f = self.legs[self._faf]
                d_faf = inverse(lat, lon, f.lat, f.lon)[0]
                if d_faf <= 2.0:
                    return 0.3 + 0.7 * d_faf / 2.0, "RAMP"
        if inverse(lat, lon, self.dest[0], self.dest[1])[0] <= self.terminal_nm:
            return 1.0, "TERMINAL"
        return 2.0, "ENROUTE"

    # -- the cycle -----------------------------------------------------------
    def update(self, t, lat, lon, alt_ft, gs_kt, track_true):
        for _ in range(len(self.legs)):
            if not self._maybe_sequence(t, lat, lon, alt_ft, gs_kt, track_true):
                break
        leg = self.legs[self.i]
        hint = ""
        if self._hold is not None:
            dtk_true, xtk, hint = self._hold.guidance(lat, lon, self.tas / 60.0)
            phase = "HOLD " + self._hold.phase
        else:
            _, xtk, dtk_true = self._line.project(lat, lon)
            phase = ""
        scale, sphase = self._scale(lat, lon)
        dis = inverse(lat, lon, leg.lat, leg.lon)[0] if leg.lat is not None else 0.0
        return RefOutput(
            crs_mag=wrap360(dtk_true + self.magvar),
            xtk_nm=xtk,
            cdi=max(-1.0, min(1.0, -xtk / scale)),
            dis_nm=dis,
            wpt=leg.ident,
            pt=leg.pt,
            scale_nm=scale,
            dtk_true=dtk_true,
            turn_hint=hint,
            phase=(phase or sphase),
            leg_index=self.i,
        )

    def _maybe_sequence(self, t, lat, lon, alt_ft, gs_kt, track_true):
        leg = self.legs[self.i]
        if self._hold is not None or self.map_suspended:
            return False
        if leg.pt == "CA":
            if alt_ft is not None and alt_ft >= leg.alt_ft:
                self._sequence(t, (lat, lon), track_true)
                return True
            return False
        s, _, azi = self._line.project(lat, lon)
        remaining = self._line.length_nm - s
        if leg.map:
            if remaining <= 0.0:
                self.map_suspended = True
            return False
        nxt_course = self._next_course_true(self.i)
        if self.i + 1 >= len(self.legs):
            return False
        flyby = not leg.flyover and nxt_course is not None
        lead = 0.0
        if flyby:
            delta = abs(wrap180(nxt_course - azi))
            lead = turn_radius_nm(gs_kt, self.rate, self.bank) * math.tan(math.radians(min(delta, 120.0) / 2.0))
        if remaining <= lead:
            self._sequence(t, (lat, lon), track_true)
            return True
        return False
