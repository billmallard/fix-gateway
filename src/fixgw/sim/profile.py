#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Scenario profile loader (SYN1). Format: src/fixgw/sim/PROFILE_FORMAT.md
#
"""Load and validate a scenario profile. Both tiers read the same file, so
validation here is about the scenario, not about how either tier flies it:
an unknown key is an error (a typo must not silently become "not set"),
and nothing X-Plane- or generator-specific is accepted."""

import os
from dataclasses import dataclass, field

import yaml


class ProfileError(Exception):
    pass


_TOP = {"name", "description", "cifp", "aircraft", "environment", "start", "route",
        "pilot", "events", "end", "checks"}
_AIRCRAFT = {"tas_kt", "climb_fpm", "descent_fpm", "bank_max_deg", "turn_rate_deg_s"}
_ENV = {"magvar_deg", "wind"}
_START = {"fix", "before_nm", "alt_ft", "heading"}
_PROC = {"airport", "approach", "transition", "missed"}
_PILOT = {"missed_after_map_s"}
_END = {"after_event", "max_s"}
_CHECK_KINDS = {"key_pair", "sequence", "flagged"}
_CHECK_KEYS = {"key_pair", "sequence", "flagged", "keys", "tol", "tol_deg", "tol_rel", "same_sign",
               "except_s_around_sequence", "skip_when", "after_s", "note", "unless_flagged",
               "tolerate_s", "while_no_fix", "while_silent", "within_s", "clear_within_s", "around_keys",
               "skip_when_silent"}
_DO = ("offset_nm", "hold_min", "gps_loss_s", "gps_link_loss_s", "gps_status_loss_s", "gps_position_loss_s")


@dataclass
class Event:
    id: str
    at: dict
    do: dict


@dataclass
class Profile:
    name: str
    path: str
    description: str
    cifp_cycle: str
    cifp_file: str
    aircraft: dict
    magvar_deg: float
    wind_from_deg: float
    wind_kt: float
    start: dict
    procedure: dict
    pilot: dict
    events: list
    end: dict
    checks: list = field(default_factory=list)


def _only(d, allowed, where):
    if not isinstance(d, dict):
        raise ProfileError(f"{where}: expected a mapping")
    extra = set(d) - allowed
    if extra:
        raise ProfileError(f"{where}: unknown key(s) {sorted(extra)}")


def load_profile(path, cifp_override=None):
    with open(path) as f:
        raw = yaml.safe_load(f)
    _only(raw, _TOP, "profile")
    for k in ("name", "cifp", "start", "route"):
        if k not in raw:
            raise ProfileError(f"profile: missing {k!r}")

    cifp = raw["cifp"]
    _only(cifp, {"cycle", "file"}, "cifp")
    cycle = str(cifp.get("cycle", ""))
    if not (len(cycle) == 4 and cycle.isdigit()):
        raise ProfileError("cifp.cycle: a 4-digit AIRAC cycle is required (pinned, e.g. \"2611\")")
    cifp_file = cifp_override or cifp.get("file")
    if not cifp_file:
        raise ProfileError("cifp.file: no CIFP file named, and none given on the command line")
    if not os.path.isabs(cifp_file) and not cifp_override:
        cifp_file = os.path.join(os.path.dirname(os.path.abspath(path)), cifp_file)

    aircraft = raw.get("aircraft", {}) or {}
    _only(aircraft, _AIRCRAFT, "aircraft")
    env = raw.get("environment", {}) or {}
    _only(env, _ENV, "environment")
    if "magvar_deg" not in env:
        raise ProfileError("environment.magvar_deg: required (FIX MAGVAR, positive west)")
    wind = env.get("wind", {}) or {}
    _only(wind, {"from_deg", "kt"}, "environment.wind")

    start = raw["start"]
    _only(start, _START, "start")
    if "fix" not in start or "alt_ft" not in start:
        raise ProfileError("start: fix and alt_ft are required")
    if start.get("heading", "on_course") != "on_course":
        raise ProfileError("start.heading: only on_course is supported")

    route = raw["route"]
    if not isinstance(route, list) or len(route) != 1 or "procedure" not in route[0]:
        raise ProfileError("route: exactly one `- procedure:` item is supported so far")
    _only(route[0], {"procedure"}, "route[0]")
    proc = route[0]["procedure"]
    _only(proc, _PROC, "route[0].procedure")
    for k in ("airport", "approach", "transition"):
        if k not in proc:
            raise ProfileError(f"route[0].procedure: missing {k!r}")

    pilot = raw.get("pilot", {}) or {}
    _only(pilot, _PILOT, "pilot")

    events = []
    ids = set()
    for n, e in enumerate(raw.get("events", []) or []):
        _only(e, {"id", "at", "do"}, f"events[{n}]")
        eid = e.get("id") or f"event{n + 1}"
        if eid in ids:
            raise ProfileError(f"events[{n}]: duplicate id {eid!r}")
        ids.add(eid)
        at, do = e.get("at") or {}, e.get("do") or {}
        _only(at, {"leg", "after_s", "after", "hold_at"}, f"events[{n}].at")
        _only(do, set(_DO) | {"hold_s"}, f"events[{n}].do")
        if sum(k in at for k in ("leg", "after", "hold_at")) != 1:
            raise ProfileError(f"events[{n}].at: exactly one of leg / after / hold_at")
        if at.get("after") and at["after"] not in ids:
            raise ProfileError(f"events[{n}].at.after: {at['after']!r} is not an earlier event")
        if sum(k in do for k in _DO) != 1:
            raise ProfileError(f"events[{n}].do: exactly one of {' / '.join(_DO)}")
        if "hold_s" in do and "offset_nm" not in do:
            raise ProfileError(f"events[{n}].do: hold_s goes with offset_nm")
        events.append(Event(eid, at, do))

    end = raw.get("end", {}) or {}
    _only(end, _END, "end")
    if end.get("after_event") and end["after_event"] not in ids:
        raise ProfileError(f"end.after_event: no event {end['after_event']!r}")

    checks = raw.get("checks", []) or []
    for n, c in enumerate(checks):
        _only(c, _CHECK_KEYS, f"checks[{n}]")
        if len(_CHECK_KINDS & set(c)) != 1:
            raise ProfileError(f"checks[{n}]: exactly one of key_pair / sequence / flagged")
        if "key_pair" in c and (not isinstance(c["key_pair"], list) or len(c["key_pair"]) != 2):
            raise ProfileError(f"checks[{n}].key_pair: [ENGINE_KEY, REFERENCE_KEY]")
        if "flagged" in c:
            if not isinstance(c["flagged"], list) or not c["flagged"]:
                raise ProfileError(f"checks[{n}].flagged: a list of engine keys")
            silent = c.get("while_silent")
            if (c.get("while_no_fix") is True) == (silent is not None):
                raise ProfileError(f"checks[{n}].flagged: exactly one condition, while_no_fix: true "
                                   "or while_silent: [KEY, ...]")
            if silent is not None and (not isinstance(silent, list) or not silent):
                raise ProfileError(f"checks[{n}].while_silent: a list of input keys")
        if "while_silent" in c and "flagged" not in c:
            raise ProfileError(f"checks[{n}].while_silent goes with flagged")
        sws = c.get("skip_when_silent")
        if sws is not None and (not isinstance(sws, dict) or not sws
                                or not all(isinstance(v, (int, float)) for v in sws.values())):
            raise ProfileError(f"checks[{n}].skip_when_silent: {{KEY: seconds, ...}}")

    return Profile(
        name=raw["name"], path=os.path.abspath(path), description=raw.get("description", ""),
        cifp_cycle=cycle, cifp_file=cifp_file, aircraft=aircraft,
        magvar_deg=float(env["magvar_deg"]), wind_from_deg=float(wind.get("from_deg", 0.0)),
        wind_kt=float(wind.get("kt", 0.0)), start=start, procedure=proc, pilot=pilot,
        events=events, end=end, checks=checks,
    )
