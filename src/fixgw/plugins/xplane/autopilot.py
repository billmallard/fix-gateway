#  Copyright (c) 2026 Bill Mallard
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 2 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.

"""X-Plane as the autopilot source (AFC2, AER-5806).

The head asks, the autopilot decides (doc/autopilot_keys.md). This adapter
turns each pilot request (``APCMD``, acted on when ``APCMDSEQ`` changes) into
the matching X-Plane autopilot command, and writes the state X-Plane reports
back into the autopilot keys: engaged flags, active and armed modes, the
flight director, the supported requests and a message for the pilot. It also
keeps the pilot's targets and settings (``TARGETS``: the autopilot dials, the
heading bug, both OBS courses and the altimeter setting) in step with the sim,
both ways. The heading bug is moved with the knob's own commands, a step at
a time, never written (XPB-018).

Every dataref and command name below is in X-Plane 12's own ``DataRefs.txt``
and ``Commands.txt`` (version stamp 1210). The adapter also logs, once, any of
its datarefs the running sim has not answered, so a name the sim does not
know shows up on the first session rather than as a key that never updates.

Three rules keep the annunciation honest:

- **State comes only from the sim.** A request is pressed into X-Plane and
  nothing else: the mode keys change when X-Plane says its mode changed. A
  request the adapter refuses (no nav signal, a mode this autopilot lacks,
  no data from the sim) sends nothing and says why on ``APMSG``.
- **Publish only from fresh data.** State is written only when every
  dataref it is built from has been heard within ``FRESH_S``. When X-Plane
  stops sending, nothing is written and the keys go old by their own tol.
- **Never replay a request.** ``APCMDSEQ`` is read as a baseline at start;
  only a later change is a request.
"""
import json
import queue
import struct
import time

# First RREF request index for the adapter's datarefs. The plugin's own
# ``datarefs:`` block counts up from 1000, so the two ranges never meet.
AP_RREF_BASE = 2000

CMND_HEADER = b"CMND\x00"

# (name used here, X-Plane dataref). All are read-only to the adapter except
# the ones in TARGETS, which it also writes (DREF) when the pilot sets them.
DATAREFS = (
    ("servos_on", "sim/cockpit2/autopilot/servos_on"),
    ("fd_mode", "sim/cockpit2/autopilot/flight_director_mode"),
    ("yd_on", "sim/cockpit2/switches/yaw_damper_on"),
    # Lateral. Each *_status is 0=off, 1=armed (where the mode arms), 2=captured.
    ("heading_status", "sim/cockpit2/autopilot/heading_status"),
    ("heading_hold_status", "sim/cockpit2/autopilot/heading_hold_status"),
    ("track_status", "sim/cockpit2/autopilot/track_status"),
    ("nav_status", "sim/cockpit2/autopilot/nav_status"),
    ("gpss_status", "sim/cockpit2/autopilot/gpss_status"),
    ("backcourse_status", "sim/cockpit2/autopilot/backcourse_status"),
    ("roll_status", "sim/cockpit2/autopilot/roll_status"),
    ("rate_status", "sim/cockpit2/autopilot/rate_status"),
    ("attitude_status", "sim/cockpit2/autopilot/attitude_status"),
    ("approach_status", "sim/cockpit2/autopilot/approach_status"),
    # Vertical.
    ("pitch_status", "sim/cockpit2/autopilot/pitch_status"),
    ("vvi_status", "sim/cockpit2/autopilot/vvi_status"),
    ("speed_status", "sim/cockpit2/autopilot/speed_status"),
    ("altitude_hold_status", "sim/cockpit2/autopilot/altitude_hold_status"),
    ("glideslope_status", "sim/cockpit2/autopilot/glideslope_status"),
    ("vnav_status", "sim/cockpit2/autopilot/vnav_status"),
    # Flight director commanded attitude, pos up / pos right.
    ("fd_pitch", "sim/cockpit2/autopilot/flight_director_pitch_deg"),
    ("fd_roll", "sim/cockpit2/autopilot/flight_director_roll_deg"),
    # The altitude ALT hold holds (read only), which is not the dial: the
    # pilot may preselect the next altitude while holding this one.
    ("alt_hold", "sim/cockpit2/autopilot/altitude_hold_ft"),
    # Target dials.
    ("alt_dial", "sim/cockpit2/autopilot/altitude_dial_ft"),
    ("vvi_dial", "sim/cockpit2/autopilot/vvi_dial_fpm"),
    ("ias_dial", "sim/cockpit2/autopilot/airspeed_dial_kts"),
    # The pilot-side settings beside them: heading bug, OBS courses, altimeter.
    ("hdg_dial", "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot"),
    ("nav1_obs", "sim/cockpit2/radios/actuators/nav1_obs_deg_mag_pilot"),
    ("nav2_obs", "sim/cockpit2/radios/actuators/nav2_obs_deg_mag_pilot"),
    ("baro", "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot"),
    # What the HSI shows (0=NAV1, 1=NAV2, 2=GPS1, 3=GPS2), whether it has a
    # lateral signal, and what each nav radio has tuned (3=VOR, 4=ILS
    # localizer, 5=standalone localizer).
    ("hsi_source", "sim/cockpit2/radios/actuators/HSI_source_select_pilot"),
    ("hsi_signal", "sim/cockpit2/radios/indicators/hsi_display_horizontal_pilot"),
    ("nav1_type", "sim/cockpit/radios/nav_type[0]"),
    ("nav2_type", "sim/cockpit/radios/nav_type[1]"),
    # What this aircraft's autopilot is, for APCAPS.
    ("ap_type", "sim/aircraft/autopilot/preconfigured_ap_type"),
    ("single_axis", "sim/aircraft/autopilot/single_axis_autopilot"),
    ("has_yd", "sim/aircraft/overflow/has_yawdamp_but"),
)

# Each pilot target and the sim dial it is kept in step with, the smallest
# difference that counts as a change, and whether it is an angle (compared
# around the circle, so 359 to 0 is a 1 degree change, and written in 0-360).
# The angles' 0.1 covers the database's 359.9 maximum: a dial at 359.95 is
# stored as 359.9 and must not count as a change on every reply.
TARGETS = (
    ("ALTSEL", "alt_dial", "sim/cockpit2/autopilot/altitude_dial_ft", 1.0,
     False),
    ("VSSEL", "vvi_dial", "sim/cockpit2/autopilot/vvi_dial_fpm", 1.0, False),
    ("IASSEL", "ias_dial", "sim/cockpit2/autopilot/airspeed_dial_kts", 0.05,
     False),
    ("HEADBUG", "hdg_dial", "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot",
     0.1, True),
    ("NAV1CRS", "nav1_obs", "sim/cockpit2/radios/actuators/nav1_obs_deg_mag_pilot",
     0.1, True),
    ("NAV2CRS", "nav2_obs", "sim/cockpit2/radios/actuators/nav2_obs_deg_mag_pilot",
     0.1, True),
    ("BARO", "baro", "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot",
     0.005, False),
)
TARGET_KEYS = tuple(t[0] for t in TARGETS)

# The heading bug is walked to the pilot's value with the cockpit's own knob
# commands, never written: a direct write can make X-Plane's HDG mode turn the
# long way round (XPB-018; AER-6268 saw +40 flown as 320 left), while a step
# turns the way it moves. This is what the xplane-bench driver does. One step
# goes out at a time, the next once a reply shows the dial took it: X-Plane
# applies a burst of the same CMND as one step (session 2786fb6ef13a).
WALK_KEY = "HEADBUG"
WALK_UP = "sim/autopilot/heading_up"        # one degree each
WALK_DOWN = "sim/autopilot/heading_down"
WALK_DONE = 0.5     # within half a step the knob can get no closer
WALK_WAIT_S = 0.5   # a step not shown on the dial by now is sent again, from
                    # where the dial is

REQUESTS = ("AP", "FD", "YD", "LVL", "HDG", "TRK", "NAV", "APR", "IAS",
            "VNAV", "VS", "ALT", "DISC")

# The X-Plane command each request presses. AP, FD, NAV, APR and DISC are
# decided in Autopilot._command, from the sim's state.
COMMANDS = {
    "YD": "sim/systems/yaw_damper_toggle",
    "LVL": "sim/autopilot/return_to_level",
    "HDG": "sim/autopilot/heading",
    "TRK": "sim/autopilot/track",
    "IAS": "sim/autopilot/level_change",
    "VNAV": "sim/autopilot/vnav",
    "VS": "sim/autopilot/alt_vs",
    "ALT": "sim/autopilot/altitude_hold",
}
CMD_AP_ON = "sim/autopilot/servos_on"
CMD_AP_OFF = "sim/autopilot/servos_off_any"
CMD_FD_ON = "sim/autopilot/fdir_on"
CMD_FD_OFF = "sim/autopilot/servos_fdir_off"
CMD_NAV = "sim/autopilot/NAV"
CMD_APR = "sim/autopilot/approach"

# The requests each X-Plane preconfigured autopilot type offers
# (sim/aircraft/autopilot/preconfigured_ap_type). Two-axis requests are
# removed for a single-axis autopilot, and YD is added when the aircraft has
# a yaw damper switch. An unlisted type gets X-Plane's generic set.
_TWO_AXIS = {"ALT", "VS", "IAS", "VNAV"}
_GENERIC = ("AP", "FD", "LVL", "HDG", "TRK", "NAV", "APR", "IAS", "VNAV",
            "VS", "ALT", "DISC")
TYPE_CAPS = {
    0: _GENERIC,                                     # X-Plane custom
    1: _GENERIC,                                     # airliner
    2: ("AP", "FD", "LVL", "HDG", "NAV", "APR", "IAS", "VNAV", "VS", "ALT",
        "DISC"),                                     # GFC-700
    3: ("AP", "HDG", "NAV", "APR", "VS", "ALT", "DISC"),   # S-Tec 55
    4: ("AP", "HDG", "NAV", "APR", "VS", "ALT", "DISC"),   # S-Tec 55, preselect
    5: ("AP", "HDG", "NAV", "APR", "DISC"),          # KAP-140 single axis
    6: ("AP", "HDG", "NAV", "APR", "VS", "ALT", "DISC"),   # KAP-140 dual axis
    7: ("AP", "HDG", "NAV", "APR", "VS", "ALT", "DISC"),   # KAP-140, preselect
    8: ("AP", "HDG", "NAV", "DISC"),                 # Piper Autocontrol
    9: ("AP", "FD", "HDG", "NAV", "APR", "IAS", "VS", "ALT", "DISC"),  # FCS-65
}

# The keys this adapter writes. All must be in the database, or it stays off.
STATE_KEYS = ("APENG", "FDENG", "YDENG", "APLAT", "APLATARM", "APVERT",
              "APVERTARM", "APALTCAP", "APALTREF", "FDPITCH", "FDROLL",
              "APCAPS",
              "APMSG", "APDISC")
READ_KEYS = ("APCMD", "APCMDSEQ")

FRESH_S = 1.0       # every dataref heard within this, or nothing is published
REFRESH_S = 0.5     # an unchanged key is rewritten this often (tol is 1-2 s)
MSG_HOLD_S = 10.0   # how long a message stays on APMSG
TARGET_SETTLE_S = 2.0   # a dial that has not followed a pilot target by now
                        # has refused it: the sim's value wins
TARGET_DELAY_S = 0.5    # a target written right after a mode press waits this
                        # long, so the mode's own sync does not overwrite it
UNANSWERED_S = 10.0     # datarefs still silent this long after the first
                        # reply are logged once by name
MSG_MAX = 64


def cmnd_packet(command):
    """X-Plane's UDP command packet: "CMND", a null, the command path."""
    return CMND_HEADER + command.encode("ascii")


def target_diff(a, b, angle):
    """How far apart two target values are; the short way round for an angle."""
    d = abs(a - b)
    if angle:
        d %= 360.0
        d = min(d, 360.0 - d)
    return d


def _on(raw, name, level=2):
    return int(round(raw.get(name, 0))) == level


def nav_mode_name(raw):
    """What NAV is following, in APLAT terms: GPS, LOC or VOR."""
    src = int(round(raw.get("hsi_source", 0)))
    if src >= 2:
        return "GPS"
    navtype = int(round(raw.get("nav1_type" if src == 0 else "nav2_type", 0)))
    return "LOC" if navtype in (4, 5) else "VOR"


def source_name(raw):
    return {0: "NAV1", 1: "NAV2", 2: "GPS", 3: "GPS2"}.get(
        int(round(raw.get("hsi_source", 0))), "the HSI source")


def lateral(raw):
    """(APLAT, APLATARM) from the sim's lateral mode datarefs."""
    if not (_on(raw, "servos_on", 1) or int(round(raw.get("fd_mode", 0))) >= 1):
        return "", ""
    nav = nav_mode_name(raw)
    if _on(raw, "backcourse_status"):
        active = "BC"
    elif _on(raw, "nav_status"):
        active = nav
    elif _on(raw, "gpss_status"):
        active = "GPS"
    elif _on(raw, "heading_status") or _on(raw, "heading_hold_status"):
        active = "HDG"
    elif _on(raw, "track_status"):
        active = "TRK"
    elif (_on(raw, "roll_status") or _on(raw, "rate_status")
          or _on(raw, "attitude_status")):
        active = "ROL"
    else:
        active = ""
    armed = []
    for status, name in (("backcourse_status", "BC"), ("nav_status", nav),
                         ("gpss_status", "GPS")):
        if _on(raw, status, 1) and name != active and name not in armed:
            armed.append(name)
    return active, ",".join(armed)


def vertical(raw):
    """(APVERT, APVERTARM) from the sim's vertical mode datarefs."""
    if not (_on(raw, "servos_on", 1) or int(round(raw.get("fd_mode", 0))) >= 1):
        return "", ""
    gs = "GP" if nav_mode_name(raw) == "GPS" else "GS"
    if _on(raw, "glideslope_status"):
        active = gs
    elif _on(raw, "vnav_status"):
        active = "VNAV"
    elif _on(raw, "altitude_hold_status"):
        active = "ALT"
    elif _on(raw, "speed_status"):
        active = "IAS"
    elif _on(raw, "vvi_status"):
        active = "VS"
    elif _on(raw, "pitch_status") or _on(raw, "attitude_status"):
        active = "PIT"
    else:
        active = ""
    armed = [name for status, name in (("altitude_hold_status", "ALT"),
                                       ("vnav_status", "VNAV"),
                                       ("glideslope_status", gs))
             if _on(raw, status, 1) and name != active]
    return active, ",".join(armed)


def capabilities(raw):
    """The requests this aircraft's autopilot offers, in REQUESTS order."""
    caps = set(TYPE_CAPS.get(int(round(raw.get("ap_type", 0))), _GENERIC))
    if _on(raw, "single_axis", 1):
        caps -= _TWO_AXIS
    if _on(raw, "has_yd", 1):
        caps.add("YD")
    return [r for r in REQUESTS if r in caps]


class Autopilot:
    """X-Plane as the source of the autopilot keys.

    ``parent`` is the plugin (db_read / db_write / db_callback_add / log),
    ``send(packet)`` puts one UDP packet on the wire to X-Plane, and
    ``clock`` is a monotonic clock in seconds, replaceable for tests."""

    def __init__(self, parent, send, config=None, clock=time.monotonic):
        config = config or {}
        self.parent = parent
        self.log = parent.log
        self.send = send
        self.clock = clock
        self.refs = {AP_RREF_BASE + i: (name, dref)
                     for i, (name, dref) in enumerate(DATAREFS)}
        self.raw = {}
        self.heard = {}             # name -> clock time of the last reply
        self.first_reply = None
        self.unanswered_logged = False
        self.last_written = {}      # key -> (value, clock time)
        self.prev_apeng = None
        self.disc_latched = False
        self.msg, self.msg_until = "", 0.0
        self.requests = queue.Queue()
        self.deferred = []          # (due, dref, value)
        self.targets = {key: {"fix": None, "pending": None, "pilot": False}
                        for key in TARGET_KEYS}
        self.walk = None            # the heading bug's walk, while one runs
        self.raw_log = None
        self.raw_logged = {}
        self.enabled = self._keys_present()
        if not self.enabled:
            return
        self.seq = self._read("APCMDSEQ")
        parent.db_callback_add("APCMDSEQ", self._on_seq)
        path = config.get("raw_log")
        if path:
            self.raw_log = open(path, "a", encoding="utf-8")

    # ------------------------------------------------------------------
    # Setup and database helpers
    # ------------------------------------------------------------------
    def _keys_present(self):
        missing = []
        for key in STATE_KEYS + READ_KEYS + TARGET_KEYS:
            try:
                self.parent.db_get_item(key)
            except KeyError:
                missing.append(key)
        if missing:
            self.log.error(
                "xplane: autopilot adapter off: the database has no %s "
                "(add database/autopilot.yaml); %s are not kept in step with "
                "the sim either", ", ".join(missing), ", ".join(TARGET_KEYS))
            return False
        return True

    def _read(self, key):
        v = self.parent.db_read(key)
        return v[0] if isinstance(v, tuple) else v

    def _write(self, key, value, now):
        last = self.last_written.get(key)
        if last is not None and last[0] == value and now - last[1] < REFRESH_S:
            return
        self.parent.db_write(key, value)
        self.last_written[key] = (value, now)

    def _say(self, text, now):
        self.msg, self.msg_until = text[:MSG_MAX], now + MSG_HOLD_S
        self.log.info("xplane: autopilot: %s", text)

    # ------------------------------------------------------------------
    # X-Plane -> FIX
    # ------------------------------------------------------------------
    def feed(self, index, value):
        """Take one RREF (index, value). Returns False for another index."""
        entry = self.refs.get(index)
        if entry is None:
            return False
        name = entry[0]
        now = self.clock()
        self.raw[name] = float(value)
        self.heard[name] = now
        if self.first_reply is None:
            self.first_reply = now
        return True

    def fresh(self, now=None):
        now = self.clock() if now is None else now
        return (len(self.heard) == len(DATAREFS)
                and all(now - t <= FRESH_S for t in self.heard.values()))

    def publish(self):
        """Write the state built from the latest replies, if they are fresh."""
        if not self.enabled:
            return
        now = self.clock()
        self._log_raw(now)
        if not self.fresh(now):
            self._check_unanswered(now)
            return
        raw = self.raw
        apeng = _on(raw, "servos_on", 1)
        if self.prev_apeng and not apeng:
            self.disc_latched = True
            self.log.info("xplane: autopilot disconnected")
        self.prev_apeng = apeng
        lat, latarm = lateral(raw)
        vert, vertarm = vertical(raw)
        if self.msg and now >= self.msg_until:
            self.msg = ""
        self._write("APENG", apeng, now)
        self._write("FDENG", int(round(raw["fd_mode"])) >= 1, now)
        self._write("YDENG", _on(raw, "yd_on", 1), now)
        self._write("APLAT", lat, now)
        self._write("APLATARM", latarm, now)
        self._write("APVERT", vert, now)
        self._write("APVERTARM", vertarm, now)
        # X-Plane reports no capture phase: altitude_hold_status goes from
        # armed to captured in one step. Never claim a capture it did not
        # report.
        self._write("APALTCAP", False, now)
        self._write("APALTREF", int(round(raw["alt_hold"])), now)
        self._write("FDPITCH", raw["fd_pitch"], now)
        self._write("FDROLL", raw["fd_roll"], now)
        self._write("APCAPS", ",".join(capabilities(raw)), now)
        self._write("APMSG", self.msg, now)
        self._write("APDISC", self.disc_latched, now)
        # A pilot change not yet sent wins over a dial that moved at the same
        # time: send it first, so it is pending before the dial is read.
        self._sync_to_sim(now)
        self._walk_bug(now)
        self._sync_from_sim(now)

    def _check_unanswered(self, now):
        if (self.unanswered_logged or self.first_reply is None
                or now - self.first_reply < UNANSWERED_S):
            return
        silent = [dref for name, dref in DATAREFS if name not in self.heard]
        if silent:
            self.unanswered_logged = True
            self.log.error(
                "xplane: autopilot adapter publishing nothing: X-Plane has "
                "not answered %s", ", ".join(silent))

    def _log_raw(self, now):
        """One JSON line per change of any dataref: the playback format."""
        if self.raw_log is None:
            return
        changed = {k: v for k, v in self.raw.items()
                   if self.raw_logged.get(k) != v}
        if not changed:
            return
        self.raw_logged.update(changed)
        self.raw_log.write(json.dumps(
            {"t": round(time.time(), 3), "refs": changed}) + "\n")
        self.raw_log.flush()

    # ------------------------------------------------------------------
    # Targets, both ways
    # ------------------------------------------------------------------
    def _sync_from_sim(self, now):
        """A dial the pilot did not just set is copied into its FIX key."""
        for key, name, _dref, eps, angle in TARGETS:
            t = self.targets[key]
            if key == WALK_KEY and t["pilot"]:
                # A walk ends up to half a step off the pilot's bug, so that
                # is the same bug. Copying the fraction back overwrote the
                # glass's next click in flight (session 2a79cb152f26); a knob
                # step in the cockpit is always at least half a step.
                eps = WALK_DONE
            sim = self.raw[name] % 360.0 if angle else self.raw[name]
            if t["pending"] is not None:
                value, sent = t["pending"]
                if target_diff(sim, value, angle) < eps:
                    t["pending"] = None
                elif now - sent < TARGET_SETTLE_S:
                    continue            # the dial has not followed yet
                else:
                    t["pending"] = None
                    self.log.info("xplane: autopilot: %s %r not taken, the sim"
                                  " holds %r", key, value, sim)
            if t["fix"] is not None and target_diff(sim, t["fix"], angle) < eps:
                continue
            self.parent.db_write(key, sim)
            t["fix"], t["pilot"] = self._read(key), False

    def _sync_to_sim(self, now):
        """A target the pilot changed on the bus is written to its dial."""
        for key, _name, dref, eps, angle in TARGETS:
            t = self.targets[key]
            value = self._read(key)
            if t["fix"] is None:
                continue                # the sim's dial has not been read yet
            if target_diff(value, t["fix"], angle) < eps:
                continue
            t["fix"], t["pilot"] = value, True
            t["pending"] = (value, now)
            if key == WALK_KEY:
                self._start_walk(value % 360.0)
            else:
                self._dref(dref, value)

    def _start_walk(self, goal):
        """Walk the heading bug to ``goal``. A walk already running keeps its
        last step, so a new goal does not send one the dial has not shown."""
        last = self.walk["step"] if self.walk else None
        self.walk = {"goal": goal, "best": None, "moved": None, "step": last}

    def _walk_bug(self, now):
        """One reply's worth of the heading bug's walk: one knob step the
        short way, once the dial shows the last. Ends within half a step of
        the goal, where the bus keeps the pilot's value, or when the dial has
        not come closer for TARGET_SETTLE_S."""
        w = self.walk
        if w is None:
            return
        t = self.targets[WALK_KEY]
        dial = self.raw["hdg_dial"] % 360.0
        diff = (w["goal"] - dial + 540.0) % 360.0 - 180.0
        if abs(diff) < WALK_DONE:
            self.walk, t["pending"] = None, None
            return
        if w["best"] is None or abs(diff) <= w["best"] - WALK_DONE:
            w["best"], w["moved"] = abs(diff), now
        elif now - w["moved"] >= TARGET_SETTLE_S:
            self.walk, t["pending"] = None, None
            self.log.info("xplane: autopilot: %s %r not reached, the sim holds"
                          " %r", WALK_KEY, w["goal"], dial)
            return
        if w["step"] is not None:
            expect, sent = w["step"]
            if (target_diff(dial, expect, True) >= WALK_DONE
                    and now - sent < WALK_WAIT_S):
                return              # the dial has not shown the last step
        self.send(cmnd_packet(WALK_UP if diff > 0 else WALK_DOWN))
        w["step"] = ((dial + (1.0 if diff > 0 else -1.0)) % 360.0, now)
        t["pending"] = (w["goal"], now)

    def _dref(self, dref, value):
        self.send(struct.pack("<4sxf500s", b"DREF", float(value),
                              dref.encode("ascii")))

    # ------------------------------------------------------------------
    # FIX -> X-Plane: requests
    # ------------------------------------------------------------------
    def _on_seq(self, key, value, udata):
        """APCMDSEQ callback, on the writer's thread: queue the request."""
        seq = value[0] if isinstance(value, tuple) else value
        if seq == self.seq:
            return
        self.seq = seq
        self.requests.put(str(self._read("APCMD")).strip().upper())

    def poll(self):
        """Run from the plugin loop: requests, deferred writes, targets."""
        if not self.enabled:
            return
        now = self.clock()
        handled = False
        while True:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                break
            self._command(request, now)
            handled = True
        if handled and self.fresh(now):
            # A refusal or an acknowledgement shows now, not on the next reply.
            self._write("APMSG", self.msg, now)
            self._write("APDISC", self.disc_latched, now)
        due = [d for d in self.deferred if d[0] <= now]
        self.deferred = [d for d in self.deferred if d[0] > now]
        for _due, dref, value in due:
            self._dref(dref, value)
        if self.fresh(now):
            self._sync_to_sim(now)

    def _press(self, command):
        self.send(cmnd_packet(command))
        self.log.info("xplane: autopilot: pressed %s", command)

    def _command(self, request, now):
        raw = self.raw
        if request not in REQUESTS:
            self._say("Unknown request {}".format(request or '""'), now)
            return
        if not self.fresh(now):
            self._say("{} refused: no autopilot data from X-Plane".format(
                request), now)
            return
        if request not in capabilities(raw):
            self._say("{} is not available on this autopilot".format(request),
                      now)
            return
        apeng = _on(raw, "servos_on", 1)
        if request == "DISC":
            if self.disc_latched:
                self.disc_latched = False
                self.log.info("xplane: autopilot disconnect acknowledged")
            elif apeng:
                self._press(CMD_AP_OFF)
            return
        if request == "AP":
            self._press(CMD_AP_OFF if apeng else CMD_AP_ON)
            return
        if request == "FD":
            if int(round(raw["fd_mode"])) < 1:
                self._press(CMD_FD_ON)
            elif apeng:
                self._say("FD stays on while the autopilot is engaged", now)
            else:
                self._press(CMD_FD_OFF)
            return
        if request in ("NAV", "APR"):
            status = "nav_status" if request == "NAV" else "approach_status"
            engaged = (int(round(raw[status])) > 0
                       or (request == "NAV" and int(round(raw["gpss_status"])) > 0))
            if not engaged and not _on(raw, "hsi_signal", 1):
                self._say("{} refused: no signal on {}".format(
                    request, source_name(raw)), now)
                return
            self._press(CMD_NAV if request == "NAV" else CMD_APR)
            return
        self._press(COMMANDS[request])
        # Command the mode, then the target: alt_vs and level_change engage
        # at the current rate or speed, so a target written with the press
        # would be overwritten by the mode's own sync.
        if request == "VS":
            self._defer_target("VSSEL", self._read("VSSEL"), now)
        elif request == "IAS":
            ias = self._read("IASSEL")
            if ias > 0:
                self._defer_target("IASSEL", ias, now)

    def _defer_target(self, key, value, now):
        """Write a target to its dial TARGET_DELAY_S from now. Until the dial
        follows, its value is not copied back, so the mode's own sync does
        not flicker the pilot's target on the bus."""
        dref = next(t[2] for t in TARGETS if t[0] == key)
        self.deferred.append((now + TARGET_DELAY_S, dref, value))
        self.targets[key]["pending"] = (value, now + TARGET_DELAY_S)

    def subscriptions(self):
        """(index, dataref) pairs to subscribe over RREF."""
        if not self.enabled:
            return []
        return [(i, dref) for i, (_n, dref) in self.refs.items()]

    def close(self):
        if self.enabled:
            try:
                self.parent.db_callback_del("APCMDSEQ", self._on_seq)
            except Exception:
                pass
        if self.raw_log is not None:
            self.raw_log.close()
            self.raw_log = None
