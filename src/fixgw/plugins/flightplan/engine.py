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

# Every path terminator this engine will load. Everything else (RF/AF arcs,
# CA/FA altitude-terminated legs, HM/HF/HA holds, PI procedure turns, ...)
# is Tier 2 (PA13) and rejects the whole procedure until then (guardrail 1).
PT_SUPPORTED = PT_TIER1 | PT_VECTOR

# Distance behind a CF leg's fix, along the reciprocal of its published
# course, used to synthesize a FROM anchor so the existing great-circle
# cross-track/along-track math (built for a real fix-to-fix leg) applies
# unchanged to a course-to-a-fix leg. Comfortably beyond any real
# interception distance without courting antipodal weirdness.
CF_VIRTUAL_FROM_NM = 50.0


def unsupported_leg_reason(waypoints):
    """None if every leg's path terminator is one this engine can fly;
    otherwise a <=64-char FPLMSG reason naming the first offender.

    Guardrail 1: an unsupported leg type rejects the WHOLE procedure, with
    a reason on FPLMSG -- never a silent coercion to TF, never a partial
    load. The most important test in PA4 is this one, run negative.
    """
    for i, w in enumerate(waypoints, start=1):
        if w.pt not in PT_SUPPORTED:
            ident = w.id or f"SLOT{i}"
            return f"UNSUPP {w.pt or '??'} {ident}"[:64]
    return None


# --- FPLSTATE ------------------------------------------------------------
STATE_NONE = 0
STATE_LEG = 1
STATE_DIRECT = 2
STATE_SUSP = 3

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
    wire encoding this mirrors 1:1."""

    __slots__ = ("id", "lat", "lon", "type", "pt", "crs", "dst", "alt", "spd", "seg", "flags")

    def __init__(self, id="", lat=0.0, lon=0.0, type=TYPE_UNKNOWN, pt=PT_DEFAULT,
                 crs=0.0, dst=0.0, alt="", spd=0, seg=SEG_ENROUTE, flags=0):
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

    def to_dict(self):
        return {
            "id": self.id, "lat": self.lat, "lon": self.lon, "type": self.type,
            "pt": self.pt, "crs": self.crs, "dst": self.dst, "alt": self.alt,
            "spd": self.spd, "seg": self.seg, "flags": self.flags,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d.get("id", ""), d.get("lat", 0.0), d.get("lon", 0.0), d.get("type", TYPE_UNKNOWN),
            d.get("pt", PT_DEFAULT), d.get("crs", 0.0), d.get("dst", 0.0), d.get("alt", ""),
            d.get("spd", 0), d.get("seg", SEG_ENROUTE), d.get("flags", 0),
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

    def _leg_from_anchor(self, to_wp, fr, to, magvar_deg):
        """The FROM anchor used for this leg's course/xtk/atd math: the
        real previous point for IF/TF/DF (a great circle from the prior
        fix -- the same geometry this engine has always flown), or a
        synthetic point on the leg's published magnetic course for CF, the
        one Tier-1 terminator that is not a fix-to-fix path but a course
        flown into a fix."""
        if to_wp.pt == "CF":
            true_course = geo.wrap360(to_wp.crs - magvar_deg)
            back_lat, back_lon = geo.destination_point(
                to.lat, to.lon, geo.wrap360(true_course + 180.0), CF_VIRTUAL_FROM_NM
            )
            return Point(back_lat, back_lon)
        return fr

    def _leg_geometry(self, to_wp, fr, to, ac_lat, ac_lon, magvar_deg):
        anchor = self._leg_from_anchor(to_wp, fr, to, magvar_deg)
        atd = geo.along_track_distance_to_waypoint_nm(anchor.lat, anchor.lon, to.lat, to.lon, ac_lat, ac_lon)
        xtk = geo.cross_track_nm(anchor.lat, anchor.lon, to.lat, to.lon, ac_lat, ac_lon)
        dtk_true = geo.desired_track_true(anchor.lat, anchor.lon, to.lat, to.lon, ac_lat, ac_lon)
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
            return
        if self._on_vector_leg():
            self._resume_from_vectors(position)
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
    def update(self, ac_lat, ac_lon, gs_kt, magvar_deg, position_ok, fix_type_ok, accuracy_nm, now):
        """Recompute all engine outputs for one position/timer cycle.

        fix_type_ok: True/False/None (None = the fix-type key is not
        actively publishing -- skip that gate, matching the bench's X-Plane
        FMS which writes neither GPS_FIX_TYPE nor GPS_ACCURACY_HORIZ).
        accuracy_nm: horizontal accuracy in nm, or None if not published.

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

        out = self._compute_guidance(ac_lat, ac_lon, gs_kt, magvar_deg)
        self._compute_phase_and_integrity(out, ac_lat, ac_lon, accuracy_nm)
        if out.pop("vectors", False):
            out["phase"] = "VECTORS"  # PA4 guardrail 4 -- wins over ENR/TERM/LNAV
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

    def _compute_guidance(self, ac_lat, ac_lon, gs_kt, magvar_deg):
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
        if not final and not self.suspended:
            if self._is_flyover_leg(self.act_leg):
                d_ta = 0.0
            else:
                next_wp = self.route[self.act_leg] if self.act_leg < self.count else None
                if next_wp is not None:
                    cur_course = geo.bearing_deg(anchor.lat, anchor.lon, to.lat, to.lon)
                    next_course = geo.bearing_deg(to.lat, to.lon, next_wp.lat, next_wp.lon)
                    delta = _course_change_deg(cur_course, next_course)
                    d_ta = turn_anticipation_nm(gs_kt, delta, self.turn_rate_deg_s)
                else:
                    d_ta = 0.0

            alert_dist = d_ta + max(gs_kt * self.alert_s / 3600.0, 1.0)
            alert = 0.0 <= atd <= alert_dist

            if not self.suspended and atd <= d_ta:
                self._sequence(to)
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

        self._prev_atd = atd

        tf = TF_TO if atd >= 0.0 else TF_FROM
        wp_dis = geo.distance_nm(ac_lat, ac_lon, to.lat, to.lon)
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

        return {
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

    def _sequence(self, reached_to):
        self.from_point = reached_to
        self.act_leg += 1
        new_to = Point.from_waypoint(self.route[self.act_leg - 1])
        self.to_point = new_to
        self.mode = "LEG"
        self._pending_msg = f"SEQ {reached_to.id} -> {new_to.id}"
        self._prev_atd = None

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
