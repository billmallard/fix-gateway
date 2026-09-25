#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  The flightplan navigation engine (FP2, fix-gateway#23).
#
#  Pure Python -- no fixgw.database dependency -- so it is directly unit
#  testable. fixgw/plugins/flightplan/__init__.py wires this to the FIX
#  database (callbacks in, key writes out) and to disk persistence.
#
#  Spec: makerplane/briefs/flight_plan_plan.md section 3.3 (the maos-workspace
#  repo), Appendix A/C; Bill's ruling of 2026-09-08 makes CDI scaling the full
#  DO-229 lateral behaviour including the approach phase -- there is no
#  VFR-advisory mode.
#
#  PA3 (fix-gateway#27; makerplane/briefs/procedures_and_airways_plan.md
#  section 3.2) widened the route slot from a bare point to a leg: a path
#  terminator, course, distance, altitude/speed constraint and a flags
#  bitfield (FAF/MAP/MAHP/IAF/fly-over/from-procedure) replacing the old
#  single-value FPLfROLE.
#
#  PA4 (fix-gateway#28; section 2/9 of the same brief) adds the Tier-1 leg
#  types -- IF/TF/CF/DF are 70% of approach legs and 91% of SID/STAR legs,
#  all great-circle or course-to-a-point geometry this engine already had
#  (IF/TF/DF fly the prior fix to this fix exactly as the old implicit-TF
#  engine always did; CF is the one that needs a real published course
#  rather than a computed bearing, _leg_from_anchor below) -- and the
#  guardrail-1 safety gate: a leg whose path terminator this engine cannot
#  fly rejects the WHOLE route, never a silent coercion to TF and never a
#  partial load (unsupported_leg_reason, raised from load_route as
#  RouteRejected). Vector legs (VA/VM/FM/VI -- a SID/STAR problem, not an
#  approach one) cannot be flown by the box at all; guardrail 4 makes
#  reaching one a SUSP annunciated VECTORS, never an invented heading.
#
#  PA13 (fix-gateway#29; section 2/6/9 of the same brief) adds the Tier-2
#  leg types the brief phased out of PA4: RF/AF constant-radius arcs
#  (_arc_geometry -- real circular geometry, not a straight-line trick),
#  CA/FA altitude-terminated legs (course held until a live altitude input
#  crosses the coded constraint, never a distance guess -- _leg_geometry's
#  course-anchor branch, extended from CF), and HM/HF/HA holds plus PI
#  procedure turns, all flown as an outbound/inbound pair of straight legs
#  around the fix (_advance_hold_or_pt) -- the inbound leg reuses the exact
#  CF anchor trick, the outbound leg is a synthetic point on the coded
#  reciprocal (or +-45 deg for PI). VA stays a vector leg (PT_VECTOR,
#  unchanged): it has no fix to fly to or from, only a heading, and
#  guardrail 4 ("the box does not invent a heading") already covers it --
#  moving it to Tier 2 would soften, not shrink, the rejected set. See
#  doc/plugins/flightplan.md for the full behaviour writeup and the scoping
#  decisions (direct-entry holds only, no ARINC parallel/teardrop entry).

import math

from fixgw import geo

# --- FPLfTYPE -------------------------------------------------------------
TYPE_UNKNOWN = 0
TYPE_AIRPORT = 1
TYPE_VOR = 2
TYPE_NDB = 3
TYPE_FIX = 4
TYPE_USER = 5
TYPE_MAPPOINT = 6

# --- FPLfSEG ----------------------------------------------------------------
SEG_ENROUTE = 0
SEG_DEPARTURE = 1
SEG_ARRIVAL = 2
SEG_APPROACH = 3
SEG_MISSED = 4

# --- FPLfFLAGS ---------------------------------------------------------
FLAG_FLYOVER = 0x01
FLAG_IAF = 0x02
FLAG_FAF = 0x04
FLAG_MAP = 0x08
FLAG_MAHP = 0x10
FLAG_FROM_PROCEDURE = 0x20

# --- FPLfPT ------------------------------------------------------------
PT_DEFAULT = "TF"  # the enroute/point-list default: a great-circle leg

# --- Tier-1 leg-type support (PA4) --------------------------------------
# Measured over FAACIFP18 (procedures_and_airways_plan.md section 2): IF +
# TF + CF + DF is 70% of approach legs and 91% of SID/STAR legs, and all
# four are great-circle or course-to-a-point geometry the engine already
# has -- IF/TF/DF fly the prior fix to this fix exactly as the old
# implicit-TF engine always did (no code change), CF needs the leg's
# published course rather than a computed bearing (_leg_from_anchor).
PT_TIER1 = frozenset({"IF", "TF", "CF", "DF"})

# Vector legs cannot be flown by the box at all -- they terminate on pilot
# or ATC action, never on geometry (a SID/STAR problem: VA/VM/FM/VI are
# 6,705 SID/STAR legs, essentially zero approach legs). Guardrail 4: a
# vector leg is a SUSP annunciated VECTORS, and the box never invents a
# heading for it.
PT_VECTOR = frozenset({"VA", "VM", "FM", "VI"})

# --- Tier-2 leg-type support (PA13) --------------------------------------
# Measured over FAACIFP18 (procedures_and_airways_plan.md section 2): holds
# are 16,915 approach legs (13%) and arcs 2,603 (2%) -- real, not Tier 1,
# and now flown rather than blanket-rejected.
PT_ARC = frozenset({"RF", "AF"})          # constant-radius arc to a fix
PT_ALT_TERM = frozenset({"CA", "FA"})     # course/fix held until an altitude
PT_HOLD = frozenset({"HM", "HF", "HA"})   # hold: manual / single-circuit / to-altitude
PT_PROC_TURN = frozenset({"PI"})          # 45/180 procedure turn
PT_TIER2 = PT_ARC | PT_ALT_TERM | PT_HOLD | PT_PROC_TURN

# Every path terminator this engine will load.
PT_SUPPORTED = PT_TIER1 | PT_VECTOR | PT_TIER2

# Distance behind a CF leg's fix, along the reciprocal of its published
# course, used to synthesize a FROM anchor so the existing great-circle
# cross-track/along-track math (built for a real fix-to-fix leg) applies
# unchanged to a course-to-a-fix leg. Comfortably beyond any real
# interception distance without courting antipodal weirdness. PA13 reuses
# this same trick for CA (course-to-altitude) and for the inbound leg of a
# hold/procedure-turn -- both are, geometrically, "hold course into this
# fix" exactly like CF.
CF_VIRTUAL_FROM_NM = 50.0


def unsupported_leg_reason(waypoints):
    """None if every leg's path terminator is one this engine can fly, and
    (PA13) every Tier-2 leg carries the fields its geometry needs;
    otherwise a <=64-char FPLMSG reason naming the first offender.

    Guardrail 1: an unsupported leg type rejects the WHOLE procedure, with
    a reason on FPLMSG -- never a silent coercion to TF, never a partial
    load. The most important test in PA4 is this one, run negative. PA13
    extends the same rule to a *supported* Tier-2 type whose coded data is
    incomplete (e.g. an arc with no center, a hold with no turn direction)
    -- flying it would mean inventing a center, a radius or a turn
    direction, which is exactly what guardrail 1 forbids for an
    unrecognized terminator; incomplete data for a recognized one is no
    safer a guess.
    """
    for i, w in enumerate(waypoints, start=1):
        ident = w.id or f"SLOT{i}"
        if w.pt not in PT_SUPPORTED:
            return f"UNSUPP {w.pt or '??'} {ident}"[:64]
        bad = _tier2_data_reason(w)
        if bad is not None:
            return f"{bad} {w.pt} {ident}"[:64]
    return None


def _tier2_data_reason(w):
    """None if a Tier-2 leg's type-specific fields are complete enough to
    fly without inventing anything; otherwise a short FPLMSG reason code."""
    if w.pt in PT_ARC:
        if w.turn not in ("L", "R"):
            return "BADTURN"
        if w.dst <= 0.0:
            return "BADRADIUS"
        if w.ctrlat == 0.0 and w.ctrlon == 0.0:
            return "BADCENTER"
    elif w.pt in PT_ALT_TERM:
        if _parse_alt_constraint(w.alt) is None:
            return "BADALT"
    elif w.pt in PT_HOLD:
        if w.turn not in ("L", "R"):
            return "BADTURN"
        if w.dst <= 0.0:
            return "BADLEGLEN"
        if w.pt == "HA" and _parse_alt_constraint(w.alt) is None:
            return "BADALT"
    elif w.pt in PT_PROC_TURN:
        if w.turn not in ("L", "R"):
            return "BADTURN"
        if w.dst <= 0.0:
            return "BADLEGLEN"
    return None


def _parse_alt_constraint(alt_desc):
    """Parse FPLfALT's packed guide notation into (threshold_ft,
    at_or_above) -- the termination test for an altitude-terminated leg
    (CA/FA) or an HA hold: "reached" is alt_ft >= threshold if
    at_or_above else alt_ft <= threshold. None if alt_desc does not carry
    a form this engine can evaluate (including "" -- a CA/FA/HA leg with
    no altitude at all has no way to terminate, which unsupported_leg_reason
    treats as incomplete data, not a free pass). "B lo,hi" (between) uses
    the lower bound -- the first one reached climbing or descending into
    it -- since a single-leg altitude termination has no way to express
    "stop somewhere in this band" more precisely than that.
    """
    if not alt_desc:
        return None
    sign, rest = alt_desc[0], alt_desc[1:]
    try:
        if sign == "+":
            return float(rest), True
        if sign == "-":
            return float(rest), False
        if sign == "@":
            return float(rest), True
        if sign == "B":
            lo, _hi = rest.split(",")
            return float(lo), True
    except ValueError:
        return None
    return None


def _alt_reached_wp(w, alt_ft):
    parsed = _parse_alt_constraint(w.alt)
    if parsed is None:
        return False
    threshold, at_or_above = parsed
    return alt_ft >= threshold if at_or_above else alt_ft <= threshold


# --- FPLSTATE ------------------------------------------------------------
STATE_NONE = 0
STATE_LEG = 1
STATE_DIRECT = 2
STATE_SUSP = 3

# --- Tier-2 internal sub-leg phase (PA13; not on the wire, persisted only
# for restart continuity) -- HM/HF/HA/PI fly as an outbound/inbound pair of
# straight legs around the fix; FA flies to the fix, then continues on
# course past it. None means "flying the initial approach to the fix" for
# all of these (indistinguishable from a plain DF/TF arrival).
LEGPHASE_OUTBOUND = "OUTBOUND"
LEGPHASE_INBOUND = "INBOUND"
LEGPHASE_FA_COURSE = "FA_COURSE"

# --- FPLAPR ----------------------------------------------------------------
APR_NONE = 0
APR_ARMED = 1
APR_ACTIVE = 2
APR_MISSED = 3

# --- FPLTF -----------------------------------------------------------------
TF_OFF = 0
TF_TO = 1
TF_FROM = 2

TERMINAL_NM_DEFAULT = 30.0
APPROACH_RAMP_NM_DEFAULT = 2.0
TURN_RATE_DEG_S_DEFAULT = 3.0
ALERT_S_DEFAULT = 10.0
HAL_NM_DEFAULT = {"enr": 2.0, "term": 1.0, "lnav": 0.3}
POSITION_TIMEOUT_S = 5.0
DTO_MATCH_TOLERANCE_DEG = 0.001
DIRECT_TO_NEEDS_POSITION_REASON = "NO POSITION"


class Waypoint:
    """A route slot -- a leg (PA3): a point (id/lat/lon/type) plus the leg
    that terminates on it (path terminator, course, distance, altitude/speed
    constraint, procedure segment, flags). See doc/flightplan_keys.md for the
    wire encoding this mirrors 1:1.

    PA13 adds ctrlat/ctrlon (an RF/AF arc's center point) and turn (the "L"/
    "R" turn direction an arc, hold or procedure turn needs and a Tier-1 leg
    never did) -- the two fields PA3 scoped out of the leg struct as "rare"
    before Tier 2 existed to need them (procedures_and_airways_plan.md
    section 3.2).
    """

    __slots__ = ("id", "lat", "lon", "type", "pt", "crs", "dst", "alt", "spd", "seg", "flags",
                 "ctrlat", "ctrlon", "turn")

    def __init__(self, id="", lat=0.0, lon=0.0, type=TYPE_UNKNOWN, pt=PT_DEFAULT,
                 crs=0.0, dst=0.0, alt="", spd=0, seg=SEG_ENROUTE, flags=0,
                 ctrlat=0.0, ctrlon=0.0, turn=""):
        self.id = id
        self.lat = lat
        self.lon = lon
        self.type = type
        self.pt = pt
        self.crs = crs
        self.dst = dst
        self.alt = alt
        self.spd = spd
        self.seg = seg
        self.flags = flags
        self.ctrlat = ctrlat
        self.ctrlon = ctrlon
        self.turn = turn

    def to_dict(self):
        return {
            "id": self.id, "lat": self.lat, "lon": self.lon, "type": self.type,
            "pt": self.pt, "crs": self.crs, "dst": self.dst, "alt": self.alt,
            "spd": self.spd, "seg": self.seg, "flags": self.flags,
            "ctrlat": self.ctrlat, "ctrlon": self.ctrlon, "turn": self.turn,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d.get("id", ""), d.get("lat", 0.0), d.get("lon", 0.0), d.get("type", TYPE_UNKNOWN),
            d.get("pt", PT_DEFAULT), d.get("crs", 0.0), d.get("dst", 0.0), d.get("alt", ""),
            d.get("spd", 0), d.get("seg", SEG_ENROUTE), d.get("flags", 0),
            d.get("ctrlat", 0.0), d.get("ctrlon", 0.0), d.get("turn", ""),
        )


class Point:
    """A FROM/TO anchor: either a plan waypoint or an ad hoc activation
    point / direct-to target -- always has a position, an ident is optional
    (empty for an activation point, e.g. "FROM = present position")."""

    __slots__ = ("lat", "lon", "id")

    def __init__(self, lat, lon, id=""):
        self.lat = lat
        self.lon = lon
        self.id = id

    def to_dict(self):
        return {"lat": self.lat, "lon": self.lon, "id": self.id}

    @classmethod
    def from_dict(cls, d):
        return cls(d["lat"], d["lon"], d.get("id", ""))

    @classmethod
    def from_waypoint(cls, wp):
        return cls(wp.lat, wp.lon, wp.id)


class RouteRejected(Exception):
    """Raised by Engine.load_route (PA4, guardrail 1) when a leg's path
    terminator is not one this engine can fly. The previously loaded
    route, activation state and all, is left completely untouched --
    there is no partial load."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def turn_anticipation_nm(gs_kt, delta_deg, turn_rate_deg_s=TURN_RATE_DEG_S_DEFAULT):
    """d_ta = R_turn * tan(delta/2), R_turn = GS_kt / (rate-driven constant).

    The spec's constant 188.5 corresponds to a standard-rate (3 deg/s) turn;
    scale it if a different turn_rate_deg_s is configured (rate scales R_turn
    inversely: a faster turn has a smaller radius).
    """
    delta_deg = min(abs(delta_deg), 120.0)
    r_turn = (gs_kt / 188.5) * (TURN_RATE_DEG_S_DEFAULT / turn_rate_deg_s)
    d_ta = r_turn * math.tan(math.radians(delta_deg) / 2.0)
    return max(0.1, min(5.0, d_ta))


def _course_change_deg(course_a, course_b):
    diff = (course_b - course_a + 180.0) % 360.0 - 180.0
    return abs(diff)


class Engine:
    def __init__(self, config=None):
        config = config or {}
        self.terminal_nm = float(config.get("terminal_nm", TERMINAL_NM_DEFAULT))
        self.approach_ramp_nm = float(config.get("approach_ramp_nm", APPROACH_RAMP_NM_DEFAULT))
        self.turn_rate_deg_s = float(config.get("turn_rate_deg_s", TURN_RATE_DEG_S_DEFAULT))
        self.alert_s = float(config.get("alert_s", ALERT_S_DEFAULT))
        self.hal_nm = dict(config.get("hal_nm", HAL_NM_DEFAULT))

        self.route = []          # list[Waypoint]
        self.route_name = ""
        self.seq = None          # last-applied FPLSEQ

        # Block-level procedure provenance (PA3) -- FPLDPID/FPLSTARID/
        # FPLAPRID/FPLAPRTYPE/FPLDBCYC. Carried through, not yet acted on
        # (PA9 annunciates FPLDBCYC's currency; PA7 writes these).
        self.dpid = ""
        self.starid = ""
        self.aprid = ""
        self.aprtype = ""
        self.dbcyc = ""

        self.mode = "NONE"       # NONE | LEG | DIRECT
        self.suspended = False
        self.act_leg = 0         # 1-based slot of the TO waypoint, 0 = none
        self.from_point = None   # Point
        self.to_point = None     # Point

        # Tier-2 sub-leg state (PA13) -- see LEGPHASE_* above.
        self._leg_phase = None
        self._hold_exit_requested = False  # RESUME on an HM hold: exit at next fix passage

        self.scale_manual = None  # None (AUTO) or 0.3/1.0/2.0

        self.apr = APR_NONE
        self._map_suspended = False   # sitting at the MAP awaiting RESUME
        self._missed = False          # RESUME past the MAP has been actioned
        self._approach_suppressed = False  # DTO between FAF and MAP

        self._integrity_msg_posted = True  # nothing active yet -> nothing to post
        self._prev_atd = None

        self._last_good_time = None
        self._last_lat = None
        self._last_lon = None
        self._cmd_msg = None
        self._pending_msg = None

    # ------------------------------------------------------------------
    # Route loading
    # ------------------------------------------------------------------
    def load_route(self, waypoints, name, seq, dpid="", starid="", aprid="", aprtype="", dbcyc=""):
        """waypoints: list[Waypoint]. Any FPLSEQ change is treated as a new
        plan: the previous activation is dropped (mode -> NONE) and the pilot
        re-activates it with ACT/DTO. See doc/plugins/flightplan.md for why
        edit-preserving activation was not attempted in this first cut.

        dpid/starid/aprid/aprtype/dbcyc: block-level procedure provenance
        (PA3) -- FPLDPID/FPLSTARID/FPLAPRID/FPLAPRTYPE/FPLDBCYC.

        Raises RouteRejected (PA4, guardrail 1) -- leaving the previously
        loaded route and activation state completely untouched -- if any
        leg's path terminator is not in PT_SUPPORTED."""
        reason = unsupported_leg_reason(waypoints)
        if reason is not None:
            raise RouteRejected(reason)
        self.route = list(waypoints)
        self.route_name = name
        self.seq = seq
        self.dpid = dpid
        self.starid = starid
        self.aprid = aprid
        self.aprtype = aprtype
        self.dbcyc = dbcyc
        self._reset_activation()

    def _reset_activation(self):
        self.mode = "NONE"
        self.suspended = False
        self.act_leg = 0
        self.from_point = None
        self.to_point = None
        self.apr = APR_NONE
        self._map_suspended = False
        self._missed = False
        self._approach_suppressed = False
        self._integrity_msg_posted = True
        self._prev_atd = None
        self._leg_phase = None
        self._hold_exit_requested = False

    # ------------------------------------------------------------------
    # Persistence (state_persist discipline: atomic JSON, restored after a
    # delay by the plugin wrapper -- this is just the (de)serialization).
    # ------------------------------------------------------------------
    def to_persisted_dict(self):
        return {
            "route": [wp.to_dict() for wp in self.route],
            "name": self.route_name,
            "seq": self.seq,
            "dpid": self.dpid,
            "starid": self.starid,
            "aprid": self.aprid,
            "aprtype": self.aprtype,
            "dbcyc": self.dbcyc,
            "mode": self.mode,
            "suspended": self.suspended,
            "act_leg": self.act_leg,
            "from_point": self.from_point.to_dict() if self.from_point else None,
            "to_point": self.to_point.to_dict() if self.to_point else None,
            "scale_manual": self.scale_manual,
            "apr": self.apr,
            "map_suspended": self._map_suspended,
            "missed": self._missed,
            "approach_suppressed": self._approach_suppressed,
            "integrity_msg_posted": self._integrity_msg_posted,
            "leg_phase": self._leg_phase,
            "hold_exit_requested": self._hold_exit_requested,
        }

    def restore_from_dict(self, d):
        self.route = [Waypoint.from_dict(w) for w in d.get("route", [])]
        self.route_name = d.get("name", "")
        self.seq = d.get("seq")
        self.dpid = d.get("dpid", "")
        self.starid = d.get("starid", "")
        self.aprid = d.get("aprid", "")
        self.aprtype = d.get("aprtype", "")
        self.dbcyc = d.get("dbcyc", "")
        self.mode = d.get("mode", "NONE")
        self.suspended = d.get("suspended", False)
        self.act_leg = d.get("act_leg", 0)
        fp = d.get("from_point")
        tp = d.get("to_point")
        self.from_point = Point.from_dict(fp) if fp else None
        self.to_point = Point.from_dict(tp) if tp else None
        self.scale_manual = d.get("scale_manual")
        self.apr = d.get("apr", APR_NONE)
        self._map_suspended = d.get("map_suspended", False)
        self._missed = d.get("missed", False)
        self._approach_suppressed = d.get("approach_suppressed", False)
        self._integrity_msg_posted = d.get("integrity_msg_posted", True)
        self._leg_phase = d.get("leg_phase")
        self._hold_exit_requested = d.get("hold_exit_requested", False)
        self._prev_atd = None

    # ------------------------------------------------------------------
    # Plan geometry helpers
    # ------------------------------------------------------------------
    @property
    def count(self):
        return len(self.route)

    def _faf_map_idx(self):
        faf_idx = map_idx = None
        for i, wp in enumerate(self.route, start=1):
            if wp.flags & FLAG_FAF and faf_idx is None:
                faf_idx = i
            if wp.flags & FLAG_MAP and map_idx is None:
                map_idx = i
        if faf_idx is not None and map_idx is not None and faf_idx < map_idx:
            return faf_idx, map_idx
        return None, None

    def _is_flyover_leg(self, to_slot):
        """True when the TO waypoint at `to_slot` (1-based) is the FAF or one
        of the intermediate fixes between the FAF and the MAP -- fly-over,
        abeam-only sequencing, no turn anticipation."""
        faf_idx, map_idx = self._faf_map_idx()
        if faf_idx is None:
            return False
        return faf_idx <= to_slot < map_idx

    def _is_final(self, slot):
        """A slot that never auto-sequences: the last waypoint, or a waypoint
        marked MAP."""
        if slot <= 0:
            return True
        if slot >= self.count:
            return True
        return bool(self.route[slot - 1].flags & FLAG_MAP)

    def _on_vector_leg(self):
        """True when the active (TO) leg is a vector type (PA4, guardrail
        4) -- derived fresh from route data every call, not a persisted
        flag, so a pilot RESUME (which moves act_leg off the vector leg,
        see _resume_from_vectors) is never fought by a stale flag on the
        next cycle."""
        if not (1 <= self.act_leg <= self.count):
            return False
        return self.route[self.act_leg - 1].pt in PT_VECTOR

    def _on_hm_hold(self):
        """True while the active leg is a published HM hold actually being
        flown (PA13) -- outbound or inbound, not still on the initial
        approach to the fix. RESUME's meaning here ("exit at the next fix
        passage") only makes sense once the aircraft is in the pattern."""
        if not (1 <= self.act_leg <= self.count):
            return False
        wp = self.route[self.act_leg - 1]
        return wp.pt == "HM" and self._leg_phase in (LEGPHASE_OUTBOUND, LEGPHASE_INBOUND)

    def _leg_from_anchor(self, to_wp, fr, to, magvar_deg):
        """The FROM anchor used for this leg's course/xtk/atd math: the
        real previous point for IF/TF/DF (a great circle from the prior
        fix -- the same geometry this engine has always flown), or a
        synthetic point on the leg's published magnetic course for a leg
        that is a course flown into a fix rather than a fix-to-fix path --
        CF (Tier 1), CA (PA13: course held to an altitude), and the inbound
        leg of a hold or procedure turn (PA13: turn back and re-intercept
        the coded inbound course into the fix) are geometrically the same
        problem, so they share this one synthetic-anchor construction.
        FA (PA13) only joins this set once it has passed the fix and is
        holding course past it (LEGPHASE_FA_COURSE) -- on the way to the
        fix it is flown like any other fix arrival, no special anchor.
        """
        uses_course_anchor = (
            to_wp.pt == "CF"
            or to_wp.pt == "CA"
            or (to_wp.pt == "FA" and self._leg_phase == LEGPHASE_FA_COURSE)
            or (to_wp.pt in (PT_HOLD | PT_PROC_TURN) and self._leg_phase == LEGPHASE_INBOUND)
        )
        if uses_course_anchor:
            true_course = geo.wrap360(to_wp.crs - magvar_deg)
            back_lat, back_lon = geo.destination_point(
                to.lat, to.lon, geo.wrap360(true_course + 180.0), CF_VIRTUAL_FROM_NM
            )
            return Point(back_lat, back_lon)
        return fr

    @staticmethod
    def _line_geometry(anchor, target, ac_lat, ac_lon):
        atd = geo.along_track_distance_to_waypoint_nm(anchor.lat, anchor.lon, target.lat, target.lon, ac_lat, ac_lon)
        xtk = geo.cross_track_nm(anchor.lat, anchor.lon, target.lat, target.lon, ac_lat, ac_lon)
        dtk_true = geo.desired_track_true(anchor.lat, anchor.lon, target.lat, target.lon, ac_lat, ac_lon)
        return atd, xtk, dtk_true

    def _arc_geometry(self, to_wp, ac_lat, ac_lon):
        """Real constant-radius arc guidance (RF/AF, PA13) -- not a
        straight-line trick, an actual circle: center ctrlat/ctrlon,
        radius dst, direction turn ("R" clockwise viewed from above, "L"
        counterclockwise). XTK is computed by handing the aircraft's
        position to the existing, already-tested cross_track_nm against
        the tangent line at the aircraft's own radial (the point on the
        circle directly abeam it, and one nm further along the direction
        of travel) -- this reuses the codebase's one cross-track sign
        convention instead of re-deriving inside/outside-of-turn signs by
        hand. Progress/remaining distance come from the sweep angle
        (geo.arc_sweep_deg) between the aircraft's radial and the exit
        fix's radial, in the coded turn direction, converted to arc length
        by the radius -- the same role atd plays for a straight leg.
        """
        clockwise = to_wp.turn == "R"
        ac_radial = geo.bearing_deg(to_wp.ctrlat, to_wp.ctrlon, ac_lat, ac_lon)
        to_radial = geo.bearing_deg(to_wp.ctrlat, to_wp.ctrlon, to_wp.lat, to_wp.lon)
        tangent_brg = geo.wrap360(ac_radial + (90.0 if clockwise else -90.0))

        on_arc_lat, on_arc_lon = geo.destination_point(to_wp.ctrlat, to_wp.ctrlon, ac_radial, to_wp.dst)
        ahead_lat, ahead_lon = geo.destination_point(on_arc_lat, on_arc_lon, tangent_brg, 1.0)
        xtk = geo.cross_track_nm(on_arc_lat, on_arc_lon, ahead_lat, ahead_lon, ac_lat, ac_lon)

        remaining_sweep = geo.arc_sweep_deg(ac_radial, to_radial, clockwise)
        if remaining_sweep <= 180.0:
            atd = to_wp.dst * math.radians(remaining_sweep)
        else:
            # Past the exit radial -- the short way is backward, not the
            # long way forward around the rest of the circle.
            atd = -to_wp.dst * math.radians(360.0 - remaining_sweep)

        return Point(on_arc_lat, on_arc_lon), atd, xtk, tangent_brg

    def _hold_outbound_target(self, to_wp, magvar_deg):
        """The synthetic endpoint of a hold/procedure-turn's outbound leg
        (PA13): dst nm from the fix, on the reciprocal of the coded
        inbound course (holds), offset a further +-45 deg for a PI's
        standard procedure turn (turn "R" offsets right, matching the
        published outbound track a 45/180 procedure turn flies before
        reversing to intercept the same inbound course a hold uses)."""
        inbound_true = geo.wrap360(to_wp.crs - magvar_deg)
        outbound_true = geo.wrap360(inbound_true + 180.0)
        if to_wp.pt in PT_PROC_TURN:
            outbound_true = geo.wrap360(outbound_true + (45.0 if to_wp.turn == "R" else -45.0))
        lat, lon = geo.destination_point(to_wp.lat, to_wp.lon, outbound_true, to_wp.dst)
        return Point(lat, lon)

    def _leg_geometry(self, to_wp, fr, to, ac_lat, ac_lon, magvar_deg):
        if to_wp.pt in PT_ARC:
            return self._arc_geometry(to_wp, ac_lat, ac_lon)
        if to_wp.pt in (PT_HOLD | PT_PROC_TURN) and self._leg_phase == LEGPHASE_OUTBOUND:
            anchor = Point(to_wp.lat, to_wp.lon, to_wp.id)
            target = self._hold_outbound_target(to_wp, magvar_deg)
            atd, xtk, dtk_true = self._line_geometry(anchor, target, ac_lat, ac_lon)
            return anchor, atd, xtk, dtk_true
        anchor = self._leg_from_anchor(to_wp, fr, to, magvar_deg)
        atd, xtk, dtk_true = self._line_geometry(anchor, to, ac_lat, ac_lon)
        return anchor, atd, xtk, dtk_true

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def handle_command(self, cmd_str, position, dto_staged=None):
        """position: (lat, lon, ok) -- current aircraft position and whether
        it is fresh/good. dto_staged: (id, lat, lon, type) for a bare "DTO"
        (the editor's staged direct-to keys), or None.

        Returns (ack_value, msg) to write to FPLCMDACK/FPLMSG. ack_value is
        `seq` on success, `-seq` on rejection (or -1 if seq itself could not
        be parsed).
        """
        seq, verb, arg = self._parse(cmd_str)
        if seq is None:
            return -1, "PARSE"

        self._cmd_msg = None
        try:
            self._dispatch(verb, arg, position, dto_staged)
        except _Reject as r:
            return -seq, r.reason
        return seq, (self._cmd_msg or "")

    @staticmethod
    def _parse(cmd_str):
        parts = (cmd_str or "").split()
        if len(parts) < 2:
            return None, None, None
        try:
            seq = int(parts[0])
        except ValueError:
            return None, None, None
        verb = parts[1].upper()
        arg = parts[2] if len(parts) > 2 else None
        return seq, verb, arg

    def _dispatch(self, verb, arg, position, dto_staged):
        if verb == "ACT":
            self._cmd_act(arg, position)
        elif verb == "DTO":
            self._cmd_dto(arg, position, dto_staged)
        elif verb == "DTOX":
            self._cmd_dtox(position)
        elif verb == "SUSP":
            self._cmd_susp()
        elif verb == "RESUME":
            self._cmd_resume(position)
        elif verb == "SCALE":
            self._cmd_scale(arg)
        else:
            raise _Reject("PARSE")

    def _require_position(self, position):
        if position is None or not position[2]:
            raise _Reject(DIRECT_TO_NEEDS_POSITION_REASON)
        return position[0], position[1]

    def _slot_arg(self, arg):
        if self.count == 0:
            raise _Reject("NO PLAN")
        try:
            k = int(arg)
        except (TypeError, ValueError):
            raise _Reject("BAD SLOT")
        if k < 1 or k > self.count:
            raise _Reject("BAD SLOT")
        return k

    def _begin_activation(self):
        """Common bookkeeping for any activating command (ACT/DTO/DTOX)."""
        was_none = self.mode == "NONE"
        self._missed = False
        self._map_suspended = False
        self._approach_suppressed = False
        self.suspended = False
        if was_none:
            self._integrity_msg_posted = False
        self._prev_atd = None
        # A fresh activation always starts a leg's "approach the fix" phase
        # (PA13) -- never mid-hold or past-the-fix-on-course from whatever
        # leg was previously active.
        self._leg_phase = None
        self._hold_exit_requested = False

    def _cmd_act(self, arg, position):
        k = self._slot_arg(arg)
        if k == 1:
            lat, lon = self._require_position(position)
            from_pt = Point(lat, lon)
        else:
            from_pt = Point.from_waypoint(self.route[k - 2])
        self._begin_activation()
        self.mode = "LEG"
        self.act_leg = k
        self.from_point = from_pt
        self.to_point = Point.from_waypoint(self.route[k - 1])

    def _cmd_dto(self, arg, position, dto_staged):
        if arg is not None:
            k = self._slot_arg(arg)
            self._activate_direct_to_slot(k, position)
            return

        if not dto_staged or not dto_staged[0]:
            raise _Reject("BAD SLOT")
        ident, lat, lon, wtype = dto_staged
        match_slot = None
        for i, wp in enumerate(self.route, start=1):
            if (
                wp.id == ident
                and abs(wp.lat - lat) <= DTO_MATCH_TOLERANCE_DEG
                and abs(wp.lon - lon) <= DTO_MATCH_TOLERANCE_DEG
            ):
                match_slot = i
                break
        if match_slot is not None:
            self._activate_direct_to_slot(match_slot, position)
            return

        # Off-route direct-to: the plan (if any) stays loaded but inactive.
        ac_lat, ac_lon = self._require_position(position)
        self._begin_activation()
        self.mode = "DIRECT"
        self.act_leg = 0
        self.from_point = Point(ac_lat, ac_lon)
        self.to_point = Point(lat, lon, ident)

    def _activate_direct_to_slot(self, k, position):
        ac_lat, ac_lon = self._require_position(position)
        faf_idx, map_idx = self._faf_map_idx()
        self._begin_activation()
        self.mode = "DIRECT"
        self.act_leg = k
        self.from_point = Point(ac_lat, ac_lon)
        self.to_point = Point.from_waypoint(self.route[k - 1])
        if faf_idx is not None and map_idx is not None and faf_idx < k < map_idx:
            # Direct-to a fix between the FAF and the MAP does not activate
            # approach scaling (guide 3-47).
            self._approach_suppressed = True

    def _cmd_dtox(self, position):
        if self.count == 0:
            raise _Reject("NO PLAN")
        ac_lat, ac_lon = self._require_position(position)

        best_slot = None
        best_xtk = None
        for i in range(2, self.count + 1):
            wp_from = self.route[i - 2]
            wp_to = self.route[i - 1]
            leg_len = geo.distance_nm(wp_from.lat, wp_from.lon, wp_to.lat, wp_to.lon)
            atd = geo.along_track_nm(wp_from.lat, wp_from.lon, wp_to.lat, wp_to.lon, ac_lat, ac_lon)
            if 0.0 <= atd <= leg_len:
                xtk = abs(geo.cross_track_nm(wp_from.lat, wp_from.lon, wp_to.lat, wp_to.lon, ac_lat, ac_lon))
                if best_xtk is None or xtk < best_xtk:
                    best_xtk = xtk
                    best_slot = i

        self._begin_activation()
        self.mode = "LEG"
        if best_slot is not None:
            self.act_leg = best_slot
            self.from_point = Point.from_waypoint(self.route[best_slot - 2])
            self.to_point = Point.from_waypoint(self.route[best_slot - 1])
        else:
            nearest_i = min(
                range(1, self.count + 1),
                key=lambda i: geo.distance_nm(ac_lat, ac_lon, self.route[i - 1].lat, self.route[i - 1].lon),
            )
            self.act_leg = nearest_i
            self.to_point = Point.from_waypoint(self.route[nearest_i - 1])
            if nearest_i > 1:
                self.from_point = Point.from_waypoint(self.route[nearest_i - 2])
            else:
                self.from_point = Point(ac_lat, ac_lon)

    def _cmd_susp(self):
        if self.mode == "NONE":
            raise _Reject("NO PLAN")
        self.suspended = True

    def _cmd_resume(self, position):
        if self._map_suspended:
            faf_idx, map_idx = self._faf_map_idx()
            self._missed = True
            self._map_suspended = False
            self.suspended = False
            if map_idx is not None and map_idx < self.count:
                self.from_point = Point.from_waypoint(self.route[map_idx - 1])
                self.act_leg = map_idx + 1
                self.to_point = Point.from_waypoint(self.route[map_idx])
                self.mode = "LEG"
                self._cmd_msg = f"SEQ {self.route[map_idx - 1].id} -> {self.route[map_idx].id}"
            else:
                self._cmd_msg = "NO MISSED APPROACH LEGS"
            self._prev_atd = None
            self._leg_phase = None
            self._hold_exit_requested = False
            return
        if self._on_vector_leg():
            self._resume_from_vectors(position)
            return
        if self._on_hm_hold():
            # RESUME while flying a published HM hold (PA13) means "exit at
            # completion of the current circuit", not "un-suspend" -- the
            # hold is actively flown (live CDI, not blanked like a vector
            # SUSP), so there is nothing to resume into except the plan's
            # normal suspended flag if the pilot had also frozen it.
            self._hold_exit_requested = True
            self.suspended = False
            self._cmd_msg = "HOLD EXIT ARMED"
            return
        if self.mode == "NONE":
            raise _Reject("NO PLAN")
        self.suspended = False

    def _resume_from_vectors(self, position):
        """RESUME off a vector leg (PA4, guardrail 4): the box never
        invented a heading while suspended, so there is no track to
        rejoin -- sequence onto the next leg direct from wherever the
        aircraft actually is now, exactly like a DTO. With no next leg to
        join, stay suspended (there is nothing safe to fly toward) and
        just post the reason -- unlike the MAP case, there is no extended
        final-approach course to keep flying here."""
        ac_lat, ac_lon = self._require_position(position)
        next_idx = self.act_leg + 1
        if next_idx > self.count:
            self._cmd_msg = "NO NEXT LEG"
            return
        self._begin_activation()
        self.mode = "LEG"
        self.act_leg = next_idx
        self.from_point = Point(ac_lat, ac_lon)
        self.to_point = Point.from_waypoint(self.route[next_idx - 1])
        self._cmd_msg = f"RESUME NAV -> {self.route[next_idx - 1].id}"

    def _cmd_scale(self, arg):
        mapping = {"0.3": 0.3, "1.0": 1.0, "2.0": 2.0}
        if arg == "AUTO":
            self.scale_manual = None
        elif arg in mapping:
            self.scale_manual = mapping[arg]
        else:
            raise _Reject("PARSE")

    # ------------------------------------------------------------------
    # Per-cycle guidance update
    # ------------------------------------------------------------------
    def update(self, ac_lat, ac_lon, gs_kt, magvar_deg, position_ok, fix_type_ok, accuracy_nm, now, alt_ft=None):
        """Recompute all engine outputs for one position/timer cycle.

        fix_type_ok: True/False/None (None = the fix-type key is not
        actively publishing -- skip that gate, matching the bench's X-Plane
        FMS which writes neither GPS_FIX_TYPE nor GPS_ACCURACY_HORIZ).
        accuracy_nm: horizontal accuracy in nm, or None if not published.
        alt_ft: current indicated altitude in feet, or None if not
        published (PA13) -- the termination input for CA/FA and HA holds;
        None never terminates one (guardrail 1's "never invent" extended to
        altitude the same way position_ok's staleness gate already treats
        "no data" as "don't guess").

        Returns a dict of output values (see OUTPUT_KEYS below) plus an
        optional "msg" key (only present when there is a new FPLMSG to post).
        """
        self._pending_msg = None

        if position_ok:
            self._last_good_time = now
            self._last_lat, self._last_lon = ac_lat, ac_lon
        elif self._last_lat is not None:
            ac_lat, ac_lon = self._last_lat, self._last_lon

        stale = self._last_good_time is None or (now - self._last_good_time) > POSITION_TIMEOUT_S
        if stale:
            return self._fail_outputs()

        if fix_type_ok is False:
            return self._fail_outputs()

        out = self._compute_guidance(ac_lat, ac_lon, gs_kt, magvar_deg, alt_ft)
        self._compute_phase_and_integrity(out, ac_lat, ac_lon, accuracy_nm)
        if out.pop("vectors", False):
            out["phase"] = "VECTORS"  # PA4 guardrail 4 -- wins over ENR/TERM/LNAV
        elif out.pop("hold", False):
            out["phase"] = "HOLD"     # PA13 -- flying a published HM/HF/HA circuit
        elif out.pop("pturn", False):
            out["phase"] = "PTURN"    # PA13 -- flying a PI procedure turn
        if self._pending_msg is not None:
            out["msg"] = self._pending_msg
        return out

    def _fail_outputs(self):
        out = {
            "state": self._display_state(),
            "act_leg": self.act_leg,
            "phase": "",
            "apr": self.apr,
            "integ": True,
            "cdiscale": 2.0,
            "crs": 0.0,
            "xtk": 0.0,
            "cdi": 0.0,
            "tf": TF_OFF,
            "fr_lat": 0.0,
            "fr_lon": 0.0,
            "wp_from": "",
            "wp_next": "",
            "wp_lat": 0.0,
            "wp_lon": 0.0,
            "wp_name": "",
            "wp_dis": 0.0,
            "wp_ete": 0,
            "rem_dis": 0.0,
            "rem_ete": 0,
            "alert": False,
            "fail": True,
        }
        return out

    def _display_state(self):
        if self.suspended:
            return STATE_SUSP
        return {"NONE": STATE_NONE, "LEG": STATE_LEG, "DIRECT": STATE_DIRECT}[self.mode]

    def _turn_anticipation_for(self, to_wp, anchor, to, gs_kt):
        """d_ta for the active leg, or 0.0 (fly-over: no predictive
        anticipation) for a FAF/MAP-flagged leg or (PA13) any Tier-2 leg --
        an arc's exit tangent, a hold/PT's outbound/inbound pair and a
        CA/FA's open-ended course are not the simple bearing-delta case
        this anticipation formula was built for, so those transitions are
        abeam-triggered only, never anticipated early."""
        if to_wp.pt in PT_TIER2:
            return 0.0
        if self._is_flyover_leg(self.act_leg):
            return 0.0
        next_wp = self.route[self.act_leg] if self.act_leg < self.count else None
        if next_wp is None:
            return 0.0
        cur_course = geo.bearing_deg(anchor.lat, anchor.lon, to.lat, to.lon)
        next_course = geo.bearing_deg(to.lat, to.lon, next_wp.lat, next_wp.lon)
        delta = _course_change_deg(cur_course, next_course)
        return turn_anticipation_nm(gs_kt, delta, self.turn_rate_deg_s)

    def _sequence(self, reached_to):
        self.from_point = reached_to
        self.act_leg += 1
        new_to = Point.from_waypoint(self.route[self.act_leg - 1])
        self.to_point = new_to
        self.mode = "LEG"
        label = reached_to.id or "ALT"  # PA13: CA/FA-course terminate at the aircraft's position, no ident
        self._pending_msg = f"SEQ {label} -> {new_to.id}"
        self._prev_atd = None
        self._leg_phase = None
        self._hold_exit_requested = False

    def _advance_hold_or_pt(self, to_wp, to, alt_ft):
        """Phase transition for HM/HF/HA/PI once the current sub-leg's
        endpoint is reached (PA13): None -> OUTBOUND on first reaching the
        fix, OUTBOUND -> INBOUND once the outbound leg is flown, and at
        INBOUND's end (back at the fix) either exit to the next slot or go
        around again -- HF and PI always exit (single circuit/turn), HM
        exits only once RESUME has armed it (_on_hm_hold/_cmd_resume), HA
        exits once alt_ft has reached the coded altitude."""
        if self._leg_phase is None:
            self._leg_phase = LEGPHASE_OUTBOUND
            self._prev_atd = None
            return
        if self._leg_phase == LEGPHASE_OUTBOUND:
            self._leg_phase = LEGPHASE_INBOUND
            self._prev_atd = None
            return
        exit_now = True
        if to_wp.pt == "HM":
            exit_now = self._hold_exit_requested
        elif to_wp.pt == "HA":
            exit_now = alt_ft is not None and _alt_reached_wp(to_wp, alt_ft)
        if exit_now:
            self._sequence(to)
        else:
            self._leg_phase = LEGPHASE_OUTBOUND
            self._prev_atd = None

    def _advance_leg(self, to_wp, to, alt_ft, ac_lat, ac_lon):
        """Dispatch for "this leg's termination condition has just been
        met" (PA13) -- the single place that decides what reaching a leg's
        endpoint means, since it is no longer always "sequence to the next
        slot" the way every Tier-1 leg was. An arc sequences like any
        other leg once its exit radial is reached. CA has no real
        endpoint fix -- it terminates on altitude wherever the aircraft
        happens to be, so the new FROM point is the aircraft's own
        position (guardrail-4-style: no invented ident for a point the
        data never gave one). FA reaches its real fix first (like DF) and
        only then starts the same altitude-terminated course CA flies.
        Holds and procedure turns hand off to _advance_hold_or_pt.
        """
        if to_wp.pt in PT_ARC:
            self._sequence(to)
        elif to_wp.pt == "CA":
            self._sequence(Point(ac_lat, ac_lon))
        elif to_wp.pt == "FA":
            if self._leg_phase != LEGPHASE_FA_COURSE:
                self._leg_phase = LEGPHASE_FA_COURSE
                self._prev_atd = None
            else:
                self._sequence(Point(ac_lat, ac_lon))
        elif to_wp.pt in (PT_HOLD | PT_PROC_TURN):
            self._advance_hold_or_pt(to_wp, to, alt_ft)
        else:
            self._sequence(to)

    def _compute_guidance(self, ac_lat, ac_lon, gs_kt, magvar_deg, alt_ft=None):
        if self.mode == "NONE" or self.to_point is None:
            return {
                "state": self._display_state(),
                "act_leg": 0,
                "crs": 0.0,
                "xtk": 0.0,
                "cdi": 0.0,
                "tf": TF_OFF,
                "fr_lat": 0.0,
                "fr_lon": 0.0,
                "wp_from": "",
                "wp_next": "",
                "wp_lat": 0.0,
                "wp_lon": 0.0,
                "wp_name": "",
                "wp_dis": 0.0,
                "wp_ete": 0,
                "rem_dis": 0.0,
                "rem_ete": 0,
                "alert": False,
                "fail": False,
            }

        if self._on_vector_leg():
            self.suspended = True
            return self._vector_guidance()

        to_wp = self.route[self.act_leg - 1]
        fr = self.from_point
        to = self.to_point
        anchor, atd, xtk, dtk_true = self._leg_geometry(to_wp, fr, to, ac_lat, ac_lon, magvar_deg)

        alert = False
        final = self._is_final(self.act_leg)
        # PA13: CA always, and FA once it has passed its fix, terminate on
        # altitude -- never on distance, since neither has a real endpoint
        # to be "reached". Checked every cycle regardless of atd/d_ta.
        alt_terminated = to_wp.pt == "CA" or (to_wp.pt == "FA" and self._leg_phase == LEGPHASE_FA_COURSE)
        if not final and not self.suspended:
            if alt_terminated:
                reached = alt_ft is not None and _alt_reached_wp(to_wp, alt_ft)
            else:
                d_ta = self._turn_anticipation_for(to_wp, anchor, to, gs_kt)
                alert_dist = d_ta + max(gs_kt * self.alert_s / 3600.0, 1.0)
                alert = 0.0 <= atd <= alert_dist
                reached = atd <= d_ta

            if reached:
                self._advance_leg(to_wp, to, alt_ft, ac_lat, ac_lon)
                if self._on_vector_leg():
                    self.suspended = True
                    return self._vector_guidance()
                to = self.to_point
                fr = self.from_point
                to_wp = self.route[self.act_leg - 1]
                anchor, atd, xtk, dtk_true = self._leg_geometry(to_wp, fr, to, ac_lat, ac_lon, magvar_deg)
                final = self._is_final(self.act_leg)

        elif final and self.route and self.act_leg and self.route[self.act_leg - 1].flags & FLAG_MAP:
            # Auto-suspend the first time the aircraft is found past the MAP.
            # Not edge-triggered off the previous cycle's atd (a restart or a
            # coarse timestep could land the very first post-activation cycle
            # already past the MAP) -- `not self._missed` is what stops this
            # from re-firing every cycle after a no-MAHP RESUME leaves act_leg
            # sitting on the MAP with the aircraft still flying away from it.
            if not self.suspended and not self._missed and atd < 0.0:
                self.suspended = True
                self._map_suspended = True

        elif final and to_wp.pt in (PT_HOLD | PT_PROC_TURN) and not self.suspended:
            # A hold/PT coded as the very last slot has nowhere to
            # sequence to -- keep flying the pattern (entry -> outbound ->
            # inbound -> repeat) rather than freezing on arrival, the same
            # "nothing after it" backstop _resume_from_vectors already has
            # for a vector leg with no next slot.
            if atd <= 0.0:
                self._leg_phase = LEGPHASE_OUTBOUND if self._leg_phase != LEGPHASE_OUTBOUND else LEGPHASE_INBOUND
                self._prev_atd = None

        self._prev_atd = atd

        tf = TF_TO if atd >= 0.0 else TF_FROM
        wp_dis = geo.distance_nm(ac_lat, ac_lon, to.lat, to.lon)
        if to_wp.pt in PT_ARC:
            wp_dis = abs(atd)  # arc length remaining, not the fix's straight-line chord distance
        wp_ete_bad = gs_kt < 30.0
        wp_ete = int(round(wp_dis / gs_kt * 3600.0)) if gs_kt > 0 else 0

        rem_dis = wp_dis
        if self.act_leg > 0:
            for i in range(self.act_leg, self.count):
                rem_dis += geo.distance_nm(
                    self.route[i - 1].lat, self.route[i - 1].lon, self.route[i].lat, self.route[i].lon
                )
        rem_ete = int(round(rem_dis / gs_kt * 3600.0)) if gs_kt > 0 else 0

        wp_next = ""
        if self.act_leg and self.act_leg < self.count:
            wp_next = self.route[self.act_leg].id

        result = {
            "state": self._display_state(),
            "act_leg": self.act_leg,
            "crs": geo.wrap360(dtk_true + magvar_deg),
            "xtk": xtk,
            "cdi": None,  # filled in after CDISCALE is known
            "tf": tf,
            "fr_lat": anchor.lat,  # the geometry anchor -- the synthetic CF point, not necessarily fr
            "fr_lon": anchor.lon,
            "wp_from": fr.id,
            "wp_next": wp_next,
            "wp_lat": to.lat,
            "wp_lon": to.lon,
            "wp_name": to.id,
            "wp_dis": wp_dis,
            "wp_ete": wp_ete,
            "wp_ete_bad": wp_ete_bad,
            "rem_dis": rem_dis,
            "rem_ete": rem_ete,
            "alert": alert,
            "fail": False,
        }
        if to_wp.pt in PT_HOLD and self._leg_phase in (LEGPHASE_OUTBOUND, LEGPHASE_INBOUND):
            result["hold"] = True
        elif to_wp.pt in PT_PROC_TURN and self._leg_phase in (LEGPHASE_OUTBOUND, LEGPHASE_INBOUND):
            result["pturn"] = True
        return result

    def _vector_guidance(self):
        """Output while the active leg is a vector type (PA4, guardrail
        4): identification only (state/act_leg/wp_from/wp_name) and no
        course, cross-track, distance or ETE at all -- the box does not
        invent a heading, and it does not invent a distance to one either.
        `update()` promotes FPLPHASE to "VECTORS" for this cycle."""
        fr = self.from_point
        to = self.to_point
        return {
            "state": self._display_state(),
            "act_leg": self.act_leg,
            "crs": 0.0,
            "xtk": 0.0,
            "cdi": 0.0,
            "tf": TF_OFF,
            "fr_lat": fr.lat if fr else 0.0,
            "fr_lon": fr.lon if fr else 0.0,
            "wp_from": fr.id if fr else "",
            "wp_next": "",
            "wp_lat": to.lat if to else 0.0,
            "wp_lon": to.lon if to else 0.0,
            "wp_name": to.id if to else "",
            "wp_dis": 0.0,
            "wp_ete": 0,
            "rem_dis": 0.0,
            "rem_ete": 0,
            "alert": False,
            "fail": False,
            "vectors": True,
        }

    def _compute_phase_and_integrity(self, out, ac_lat, ac_lon, accuracy_nm):
        faf_idx, map_idx = self._faf_map_idx()
        approach_marked = faf_idx is not None

        dep_wp = self.route[0] if self.route else None
        dest_wp = self.route[-1] if self.route else None

        near_dep = dep_wp is not None and dep_wp.type == TYPE_AIRPORT and \
            geo.distance_nm(ac_lat, ac_lon, dep_wp.lat, dep_wp.lon) <= self.terminal_nm
        if approach_marked:
            dist_to_map = geo.distance_nm(ac_lat, ac_lon, self.route[map_idx - 1].lat, self.route[map_idx - 1].lon)
            if dest_wp is not None and dest_wp.type == TYPE_AIRPORT:
                near_dest = geo.distance_nm(ac_lat, ac_lon, dest_wp.lat, dest_wp.lon) <= self.terminal_nm
            else:
                near_dest = dist_to_map <= self.terminal_nm
        else:
            dist_to_map = None
            near_dest = dest_wp is not None and dest_wp.type == TYPE_AIRPORT and \
                geo.distance_nm(ac_lat, ac_lon, dest_wp.lat, dest_wp.lon) <= self.terminal_nm

        base_phase = "TERM" if (near_dep or near_dest) else "ENR"
        base_scale = 1.0 if base_phase == "TERM" else 2.0
        phase_key = "term" if base_phase == "TERM" else "enr"

        apr = APR_NONE
        phase_label = base_phase
        scale = base_scale

        if approach_marked and not self._missed:
            before_or_at_faf = self.act_leg == 0 or self.act_leg <= faf_idx
            armed = dist_to_map <= self.terminal_nm and before_or_at_faf

            active = False
            if not self._approach_suppressed and self.act_leg and faf_idx <= self.act_leg <= map_idx:
                if self.act_leg == faf_idx:
                    dist_to_faf = geo.distance_nm(
                        ac_lat, ac_lon, self.route[faf_idx - 1].lat, self.route[faf_idx - 1].lon
                    )
                    active = dist_to_faf <= self.approach_ramp_nm
                else:
                    active = True
                    dist_to_faf = 0.0

            if active:
                apr = APR_ACTIVE
                phase_key = "lnav"
                phase_label = "LNAV"
                if self.act_leg == faf_idx:
                    t = max(0.0, min(1.0, (self.approach_ramp_nm - dist_to_faf) / self.approach_ramp_nm))
                    scale = 1.0 - 0.7 * t
                else:
                    scale = 0.3
            elif armed:
                apr = APR_ARMED
                phase_key = "term"
                phase_label = "TERM"
                scale = 1.0
        elif self._missed:
            apr = APR_MISSED
            phase_label = base_phase
            scale = base_scale

        # Integrity gate: horizontal accuracy above the phase's HAL.
        integ = True
        if accuracy_nm is not None:
            hal = self.hal_nm.get(phase_key, HAL_NM_DEFAULT[phase_key])
            if accuracy_nm > hal:
                integ = False
                phase_label = "LOI"
                if apr == APR_ACTIVE:
                    apr = APR_ARMED
                    scale = 1.0
        else:
            if self.mode != "NONE" and not self._integrity_msg_posted:
                self._integrity_msg_posted = True
                # Don't clobber a "SEQ X -> Y" sequencing message from this
                # same cycle -- both share FPLMSG, but a sequence event took
                # priority since it just happened this instant.
                if self._pending_msg is None:
                    self._pending_msg = "NO INTEGRITY DATA"

        effective_scale = scale
        if self.scale_manual is not None:
            effective_scale = min(scale, self.scale_manual)
            if self.scale_manual < scale and integ:
                phase_label = "%.2f NM" % self.scale_manual

        self.apr = apr
        out["phase"] = phase_label
        out["apr"] = apr
        out["integ"] = integ
        out["cdiscale"] = effective_scale
        out["bad"] = not integ

        cdi = -out["xtk"] / effective_scale if effective_scale else 0.0
        out["cdi"] = max(-1.0, min(1.0, cdi))


class _Reject(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
