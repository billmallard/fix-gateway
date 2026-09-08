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

import math

from fixgw import geo

# --- FPLfTYPE / FPLfROLE -----------------------------------------------
TYPE_UNKNOWN = 0
TYPE_AIRPORT = 1
TYPE_VOR = 2
TYPE_NDB = 3
TYPE_FIX = 4
TYPE_USER = 5
TYPE_MAPPOINT = 6

ROLE_NONE = 0
ROLE_IAF = 1
ROLE_FAF = 2
ROLE_MAP = 3
ROLE_MAHP = 4

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
    __slots__ = ("id", "lat", "lon", "type", "role")

    def __init__(self, id="", lat=0.0, lon=0.0, type=TYPE_UNKNOWN, role=ROLE_NONE):
        self.id = id
        self.lat = lat
        self.lon = lon
        self.type = type
        self.role = role

    def to_dict(self):
        return {"id": self.id, "lat": self.lat, "lon": self.lon, "type": self.type, "role": self.role}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("id", ""), d.get("lat", 0.0), d.get("lon", 0.0),
                    d.get("type", TYPE_UNKNOWN), d.get("role", ROLE_NONE))


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
    def load_route(self, waypoints, name, seq):
        """waypoints: list[Waypoint]. Any FPLSEQ change is treated as a new
        plan: the previous activation is dropped (mode -> NONE) and the pilot
        re-activates it with ACT/DTO. See doc/plugins/flightplan.md for why
        edit-preserving activation was not attempted in this first cut."""
        self.route = list(waypoints)
        self.route_name = name
        self.seq = seq
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
            if wp.role == ROLE_FAF and faf_idx is None:
                faf_idx = i
            if wp.role == ROLE_MAP and map_idx is None:
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
        return self.route[slot - 1].role == ROLE_MAP

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
            self._cmd_resume()
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

    def _cmd_resume(self):
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
        if self.mode == "NONE":
            raise _Reject("NO PLAN")
        self.suspended = False

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

        fr = self.from_point
        to = self.to_point
        atd = geo.along_track_distance_to_waypoint_nm(fr.lat, fr.lon, to.lat, to.lon, ac_lat, ac_lon)
        xtk = geo.cross_track_nm(fr.lat, fr.lon, to.lat, to.lon, ac_lat, ac_lon)
        dtk_true = geo.desired_track_true(fr.lat, fr.lon, to.lat, to.lon, ac_lat, ac_lon)

        alert = False
        final = self._is_final(self.act_leg)
        if not final and not self.suspended:
            if self._is_flyover_leg(self.act_leg):
                d_ta = 0.0
            else:
                next_wp = self.route[self.act_leg] if self.act_leg < self.count else None
                if next_wp is not None:
                    cur_course = geo.bearing_deg(fr.lat, fr.lon, to.lat, to.lon)
                    next_course = geo.bearing_deg(to.lat, to.lon, next_wp.lat, next_wp.lon)
                    delta = _course_change_deg(cur_course, next_course)
                    d_ta = turn_anticipation_nm(gs_kt, delta, self.turn_rate_deg_s)
                else:
                    d_ta = 0.0

            alert_dist = d_ta + max(gs_kt * self.alert_s / 3600.0, 1.0)
            alert = 0.0 <= atd <= alert_dist

            if not self.suspended and atd <= d_ta:
                self._sequence(to)
                to = self.to_point
                fr = self.from_point
                atd = geo.along_track_distance_to_waypoint_nm(fr.lat, fr.lon, to.lat, to.lon, ac_lat, ac_lon)
                xtk = geo.cross_track_nm(fr.lat, fr.lon, to.lat, to.lon, ac_lat, ac_lon)
                dtk_true = geo.desired_track_true(fr.lat, fr.lon, to.lat, to.lon, ac_lat, ac_lon)
                final = self._is_final(self.act_leg)

        elif final and self.route and self.act_leg and self.route[self.act_leg - 1].role == ROLE_MAP:
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
            "fr_lat": fr.lat,
            "fr_lon": fr.lon,
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
