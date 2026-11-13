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
  (the runway threshold, when a climb-out opens the route) and ends when
  the altitude is reached.
- A fly-by fix is two events (DO-236 section 3.2.5.4.1; the Garmin
  guides). At the start of the turn (turn anticipation ``R * tan(delta/2)``,
  ``R`` the radius of a nominal 15 degree bank, the Garmin guides' figure:
  TURN1, AER-4759) the course moves to the next leg. At the turn bisector, the geodesic
  through the fix square to the mean of the two courses, the TO waypoint
  and distance sequence. A fly-over fix, and a fix with nothing after it,
  sequence abeam, as does a straight-through fix (its bisector is the
  abeam line). A fly-by turn over 120 degrees is flown as a fly-over
  (TURN2, AER-4760): Doc 9613 and AC 90-105A define fly-by transitions
  only up to 120 degrees, and no Garmin guide says where a larger one
  starts or sequences. Bill chose the fly-over on the AER-4760 card.
  Such a turn also sequences once an aircraft that turned back short of
  the fix, and never got abeam it, has passed its closest approach to it
  (TURN2b, AER-5324, Bill's card 2026-11-12). In straight flight that
  point is abeam, so it changes nothing else.
  A turn within 0.5 degrees of 120 is refused (:class:`ReferenceError_`):
  the engine's sphere and this module's WGS-84 can read it on opposite
  sides of the limit (QA, AER-4785).
- In a fly-by turn the cross-track is from the turn's arc, from turn start
  until the aircraft is abeam the arc's end, on both sides of the bisector
  (the Garmin GNC 250XL guide, p. 84: "CDI guidance along a curved path
  segment" during the transition). The arc is tangent to both legs,
  ``lead`` from the fix on each, so its radius is ``lead / tan(delta/2)``.
- The MAP never sequences on its own. Guidance continues FROM the MAP on
  the final course until the pilot initiates the missed approach
  (:meth:`ReferenceNavigator.go_missed`).
- An ``HM`` hold is flown as the published racetrack with a standard entry
  (direct, teardrop or parallel, AIM 5-3-8 sectors) and never ends on its
  own.
- CDI full scale: 2.0 nm en route, 1.0 nm within 30 nm of the departure or
  the destination airport (terminal mode, TSO-C129a / AC 20-138D),
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
# The standards define a fly-by transition only up to 120 deg (Doc 9613 Vol
# II C-1 1.3.3.7.3; AC 90-105A App. F), and no Garmin guide says where a
# larger one starts or sequences, so the reference flies it as a fly-over
# (TURN2, AER-4760; until then it refused one).
MAX_FLYBY_TURN_DEG = 120.0
# The engine measures the turn on the sphere and the reference on WGS-84,
# and near 120 deg the two readings differ by up to 0.29 deg (QA's survey
# on AER-4785: latitudes 0 to 70, inbound 000/045/090, legs 10 to 100 nm;
# worst at the equator, inbound 045). Inside this band either answer is
# honest, so the reference refuses to judge rather than pick a side.
FLYBY_LIMIT_BAND_DEG = 0.5
# TURN2b (AER-5324): such a turn also sequences where an aircraft that
# turned back short of the fix has passed its closest approach to it,
# within the lead a fly-by turn would have taken (capped at 5 nm, as the
# engine caps it), once the distance has opened by this much.
REVERSAL_PASSED_NM = 0.05
REVERSAL_LEAD_CAP_NM = 5.0


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
    """Rate-one turn radius, or the bank-limited one if that is wider: a
    hold's turns (AIM 5-3-8, whichever needs the least bank)."""
    v = max(gs_kt, 1.0) * KT_TO_MPS
    r_rate = v / math.radians(rate_deg_s)
    r_bank = v * v / (G_MPS2 * math.tan(math.radians(bank_max_deg)))
    return max(r_rate, r_bank) / M_PER_NM


def flyby_turn_radius_nm(gs_kt, bank_deg=15.0):
    """A fly-by turn's radius: a nominal bank, 15 degrees in the Garmin
    guides ("smooths adjacent leg transitions based upon a nominal 15 deg
    bank angle", GNS 430 / 400W / G1000 NXi), not rate one (TURN1)."""
    v = max(gs_kt, 1.0) * KT_TO_MPS
    return v * v / (G_MPS2 * math.tan(math.radians(bank_deg))) / M_PER_NM


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


def climb_out_legs(cifp, profile):
    """The ``climb_out`` item as reference legs: the runway threshold, then
    a ``CA`` on the runway's magnetic bearing from it, ending at the item's
    altitude. ``[]`` when the route has none."""
    climb = profile.climb_out
    if climb is None:
        return []
    rwy, bearing_mag, _ = cifp.runway(profile.departure, climb["runway"])
    return [RefLeg(pt="TF", ident=rwy.ident, lat=rwy.lat, lon=rwy.lon),
            RefLeg(pt="CA", course_true=wrap360(bearing_mag - profile.magvar_deg), alt_ft=climb["alt_ft"])]


def legs_from_route(cifp, profile):
    """A profile's whole route as reference legs: the departure airport,
    the climb-out (:func:`climb_out_legs`), every en-route fix (an airway
    expanded to the fixes it passes through, each one a ``TF`` from the fix
    before it, except the first after a climb-out: a ``DF``, from where the
    climb ended), then the procedure as :func:`legs_from_cifp` reads it, or
    the destination airport."""
    legs = []
    if profile.departure:
        d = cifp.fix(profile.departure, section="PA")
        legs.append(RefLeg(pt="TF", ident=d.ident, lat=d.lat, lon=d.lon))
    legs += climb_out_legs(cifp, profile)
    for n, (f, _) in enumerate(cifp.expand_route(profile.enroute)):
        pt = "DF" if n == 0 and profile.climb_out else "TF"
        legs.append(RefLeg(pt=pt, ident=f.ident, lat=f.lat, lon=f.lon))
    proc = profile.procedure
    if proc is None:
        d = cifp.fix(profile.destination, section="PA")
        return legs + [RefLeg(pt="TF", ident=d.ident, lat=d.lat, lon=d.lon)]
    p_legs = legs_from_cifp(cifp, proc["airport"], proc["approach"], proc["transition"], profile.magvar_deg)
    if legs and legs[-1].ident == p_legs[0].ident:
        raise ReferenceError_(f"the en-route items end at {p_legs[0].ident}, where the procedure starts")
    return legs + p_legs


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
    from, and ``departure``, when the route starts at an airport, the other.
    ``tas_kt`` sizes the hold and its entry leg (one minute).
    ``position`` is where the aircraft is, needed only to start on a ``DF``.
    """

    def __init__(self, legs, magvar_deg, destination, active, tas_kt,
                 turn_rate_deg_s=3.0, bank_max_deg=25.0, terminal_nm=30.0, position=None,
                 flyby_bank_deg=15.0, departure=None):
        if not 0 < active < len(legs):
            raise ReferenceError_(f"active leg {active} out of range")
        self.legs = legs
        self.magvar = magvar_deg
        self.dest = destination
        self.dep = departure
        self.tas = tas_kt
        self.rate = turn_rate_deg_s
        self.bank = bank_max_deg
        self.flyby_bank = flyby_bank_deg
        self.terminal_nm = terminal_nm
        self.i = active
        self.missed = False
        self.map_suspended = False
        self.sequences = []
        self._hold = None
        self._line = None
        self._turn = None             # (bisector, inbound line) between turn start and the bisector
        self._arc = None              # the fly-by turn's arc, turn start to its end (_TurnArc)
        self._closest = None          # closest approach to a row-3 fix on this leg, nm (TURN2b)
        self._start_leg(position)
        self._faf = next((k for k, lg in enumerate(legs) if lg.faf), None)
        self._map = next((k for k, lg in enumerate(legs) if lg.map), None)

    # -- legs ----------------------------------------------------------------
    def _prev_fix(self, k):
        for j in range(k - 1, -1, -1):
            if self.legs[j].lat is not None:
                return self.legs[j]
        return None

    def _line_for(self, k, position):
        """The geodesic leg k is flown along (TF/IF/DF/CA), else None."""
        leg = self.legs[k]
        if leg.pt in ("TF", "IF"):
            prev = self._prev_fix(k)
            return GeoLine.between(prev.lat, prev.lon, leg.lat, leg.lon)
        if leg.pt == "DF":
            if position is None:
                raise ReferenceError_("cannot start on a DF leg without a position")
            return GeoLine.between(position[0], position[1], leg.lat, leg.lon)
        if leg.pt == "CA":
            prev = self._prev_fix(k)
            return GeoLine(prev.lat, prev.lon, leg.course_true)
        return None

    def _start_leg(self, position, arrival_track=None):
        leg = self.legs[self.i]
        self._hold = None
        self._turn = None
        self._arc = None
        self._closest = None
        if leg.pt in ("TF", "IF", "DF", "CA"):
            self._line = self._line_for(self.i, position)
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

    def _start_turn(self, lat, lon, inbound_azi_at_fix, next_course, lead):
        """Turn start at a fly-by fix: guidance moves to the next leg, flown
        from here if it is a DF; the TO waypoint stays until the bisector.
        The cross-track is from the turn's arc until it ends."""
        fix = self.legs[self.i]
        mean = wrap360(inbound_azi_at_fix + wrap180(next_course - inbound_azi_at_fix) / 2.0)
        self._turn = (GeoLine(fix.lat, fix.lon, mean), self._line)
        self._arc = _TurnArc.at(fix, self._line, inbound_azi_at_fix, next_course, lead, self.i)
        self._line = self._line_for(self.i + 1, (lat, lon))

    def _sequence_in_turn(self, t):
        """The bisector: the waypoint catches up with the guidance, which
        has been on the next leg since turn start."""
        frm = self.legs[self.i].ident
        self.i += 1
        self._turn = None
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
        for apt in (self.dest, self.dep):
            if apt is not None and inverse(lat, lon, apt[0], apt[1])[0] <= self.terminal_nm:
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
            arc_xtk = self._arc_xtk(lat, lon)
            if arc_xtk is not None:
                xtk = arc_xtk
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
        if self._turn is not None:
            return self._turn_done(t, lat, lon)
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
        reversal_lead = None
        if flyby:
            turn = abs(wrap180(nxt_course - self._turn_inbound_azi()))
            if abs(turn - MAX_FLYBY_TURN_DEG) <= FLYBY_LIMIT_BAND_DEG:
                raise ReferenceError_(
                    f"fly-by turn of {turn:.2f} deg at {leg.ident}: within {FLYBY_LIMIT_BAND_DEG:g} deg of "
                    f"the {MAX_FLYBY_TURN_DEG:g} deg limit, too close for the reference to tell fly-by from fly-over")
            # The turn at the fix, the one the limit below measures and the
            # arc is built on, not at the abeam point (QA on #122). Over the
            # limit there is no lead: abeam, as at a fly-over fix.
            if turn <= MAX_FLYBY_TURN_DEG:
                lead = flyby_turn_radius_nm(gs_kt, self.flyby_bank) * math.tan(math.radians(turn / 2.0))
            elif self.legs[self.i + 1].pt in ("TF", "DF"):
                reversal_lead = min(REVERSAL_LEAD_CAP_NM, flyby_turn_radius_nm(gs_kt, self.flyby_bank)
                                    * math.tan(math.radians(turn / 2.0)))
        if 0.0 < remaining <= lead:
            self._start_turn(lat, lon, self._turn_inbound_azi(), nxt_course, lead)
            return self._turn_done(t, lat, lon)
        if remaining <= 0.0 or self._turned_back_short(lat, lon, leg, reversal_lead):
            self._sequence(t, (lat, lon), track_true)
            return True
        return False

    def _turned_back_short(self, lat, lon, leg, reversal_lead):
        """TURN2b: past the closest approach to a row-3 fix, within the
        lead a fly-by turn would have taken. None for any other fix."""
        if reversal_lead is None:
            return False
        dist = inverse(lat, lon, leg.lat, leg.lon)[0]
        self._closest = dist if self._closest is None else min(self._closest, dist)
        return self._closest <= reversal_lead and dist >= self._closest + REVERSAL_PASSED_NM

    def _turn_inbound_azi(self):
        """Azimuth of the current leg's geodesic where it reaches its fix."""
        return self._line.position(self._line.length_nm)[2]

    def _turn_done(self, t, lat, lon):
        """In a turn: sequence once past the bisector, or abeam the fix on
        the inbound leg (the backstop for a turn not flown)."""
        bisector, inbound = self._turn
        if bisector.project(lat, lon)[0] >= 0.0 or inbound.project(lat, lon)[0] >= inbound.length_nm:
            self._sequence_in_turn(t)
            return True
        return False

    def _arc_xtk(self, lat, lon):
        """Cross-track from the fly-by arc, or None outside it. The arc
        belongs to the turn at legs[k]: it applies while legs[k] is the TO
        leg (turn start to the bisector) or legs[k + 1] is (the bisector to
        the arc's end), and is dropped once the aircraft is past its end."""
        arc = self._arc
        if arc is None or self.i not in (arc.k, arc.k + 1):
            self._arc = None
            return None
        where, xtk = arc.measure(lat, lon)
        if where > 0 and self.i == arc.k + 1:
            self._arc = None
        return xtk if where == 0 else None


class _TurnArc:
    """The arc of a fly-by turn: tangent to the inbound geodesic ``lead``
    before the fix and to the outbound course ``lead`` after it. Built from
    the inbound tangent point: its centre is ``radius`` square to the
    inbound geodesic there. ``test_refnav`` checks it against a centre built
    from the fix on the bisector, and against the outbound tangent point."""

    def __init__(self, lat, lon, radius_nm, right, start_az, sweep_deg, k):
        self.lat, self.lon, self.radius, self.right = lat, lon, radius_nm, right
        self.start_az, self.sweep, self.k = start_az, sweep_deg, k

    @classmethod
    def at(cls, fix, inbound, inbound_azi_at_fix, next_course, lead, k):
        delta = wrap180(next_course - inbound_azi_at_fix)
        if abs(delta) < 0.5 or lead <= 0.0:
            return None
        right = delta > 0.0
        radius = lead / math.tan(math.radians(abs(delta)) / 2.0)
        s_lat, s_lon, s_azi = inbound.position(inbound.length_nm - lead)
        c_lat, c_lon = direct(s_lat, s_lon, s_azi + (90.0 if right else -90.0), radius)
        e_lat, e_lon = direct(fix.lat, fix.lon, next_course, lead)
        start_az = inverse(c_lat, c_lon, s_lat, s_lon)[1]
        end_az = inverse(c_lat, c_lon, e_lat, e_lon)[1]
        sweep = (end_az - start_az) % 360.0 if right else (start_az - end_az) % 360.0
        return cls(c_lat, c_lon, radius, right, start_az, sweep, k)

    def measure(self, lat, lon):
        """(where, xtk): where is -1 short of the arc's start radial, 0
        between its ends, 1 past its end; xtk is right-positive from the
        arc (outside a right turn is left of the path)."""
        r, az, _ = inverse(self.lat, self.lon, lat, lon)
        gone = (az - self.start_az) % 360.0 if self.right else (self.start_az - az) % 360.0
        if gone <= self.sweep:
            where = 0
        elif gone - self.sweep < (360.0 - self.sweep) / 2.0:
            where = 1
        else:
            where = -1
        return where, (self.radius - r) if self.right else (r - self.radius)
