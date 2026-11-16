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

"""ArduPlane as an autopilot source (AFC7, AER-5822).

The head asks, the autopilot decides (doc/autopilot_keys.md). This adapter
maps what an ArduPlane flight controller reports in its HEARTBEAT, the same
message that sets ``MAVMODE``, into the autopilot state keys, and turns a
pilot request (``APCMD``, acted on when ``APCMDSEQ`` changes) into the
existing ``MAVREQ*`` keys, which ``Mav.checkMode`` then sends as a mode change.

ArduPlane's modes are drone modes, not GA modes. Only three requests have an
honest ArduPlane answer, so ``APCAPS`` is ``AP,NAV,DISC`` and the head greys
every other button:

- ``AP`` engages CRUISE, which holds the ground track and the altitude it has
  when engaged (``APLAT`` ``TRK``, ``APVERT`` ``ALT``). ``AP`` while engaged,
  and ``DISC``, go back to MANUAL, where the pilot's trims drive the servos.
- ``NAV``, with the autopilot engaged and a valid waypoint, flies GUIDED to
  ``WPLAT``/``WPLON`` at the current altitude (``GPS``, ``ALT``). It flies
  direct to the waypoint, not along the leg. ``NAV`` while in GUIDED goes back
  to CRUISE.

HDG has no equivalent: CRUISE holds the track it has, not ``HEADBUG``. There
is no LVL, VS, IAS, ALT preselect, flight director or yaw damper switch.

Three rules keep the annunciation honest:

- **State comes only from the heartbeat.** A request writes a ``MAVREQ*`` key
  and nothing else; the mode keys change when ArduPlane reports the new mode.
  A mode with no GA name (FBWA, RTL, LOITER, AUTO's own mission, ...) is shown
  engaged with no lateral or vertical mode, and named on ``APMSG``.
- **Publish only from a fresh heartbeat.** The state is rewritten while the
  last fixed-wing heartbeat is at most ``HOLD_S`` old. When the link stops,
  nothing is written and the keys go old by their own tol.
- **Never replay a request.** ``APCMDSEQ`` is read as a baseline at start;
  only a later change is a request.
"""
import queue
import time

from pymavlink import mavutil

# ArduPlane custom modes (mavutil.mode_mapping_apm).
MANUAL = 0
CRUISE = 7
AUTOTUNE = 8
GUIDED = 15
INITIALISING = 16

# Each ArduPlane mode with a GA name: (APLAT, APVERT). CRUISE locks the
# ground course and the altitude when the sticks are released. GUIDED flies
# to one point at one altitude. AUTOTUNE flies like FBWA, holding the
# attitude the sticks give it, while it tunes.
GA_MODES = {
    CRUISE: ("TRK", "ALT"),
    GUIDED: ("GPS", "ALT"),
    AUTOTUNE: ("ROL", "PIT"),
}

# The modes in which ArduPlane is not flying the airplane.
NOT_ENGAGED = (MANUAL, INITIALISING)

REQUESTS = ("AP", "FD", "YD", "LVL", "HDG", "TRK", "NAV", "APR", "IAS",
            "VNAV", "VS", "ALT", "DISC")
CAPS = ("AP", "NAV", "DISC")

# The MAVREQ* key, and Mav's own mode name, for each mode a request can set.
MODE_NAMES = {MANUAL: "TRIM", CRUISE: "CRUISE", GUIDED: "GUIDED"}

STATE_KEYS = ("APENG", "FDENG", "YDENG", "APLAT", "APLATARM", "APVERT",
              "APVERTARM", "APALTCAP", "APCAPS", "APMSG", "APDISC")
READ_KEYS = ("APCMD", "APCMDSEQ")

HOLD_S = 1.5        # ArduPlane heartbeats at 1 Hz: one may be missed
REFRESH_S = 0.5     # an unchanged key is rewritten this often (tol is 2 s)
MSG_HOLD_S = 10.0   # how long a request's message stays on APMSG
MSG_MAX = 64


def mode_name(custom_mode):
    return mavutil.mode_mapping_apm.get(custom_mode, str(custom_mode))


def engaged(custom_mode):
    return custom_mode not in NOT_ENGAGED


def state(custom_mode):
    """(APENG, APLAT, APVERT) for an ArduPlane custom mode."""
    if not engaged(custom_mode):
        return False, "", ""
    lat, vert = GA_MODES.get(custom_mode, ("", ""))
    return True, lat, vert


class Autopilot:
    """ArduPlane, through the MAVLink plugin, as the autopilot source.

    ``parent`` is the plugin (db_read / db_write / db_get_item /
    db_callback_add / log). ``clock`` is a monotonic clock in seconds,
    replaceable for tests. One instance lives for the plugin's lifetime, so
    a reconnect keeps the request baseline and the disconnect latch."""

    def __init__(self, parent, clock=time.monotonic):
        self.parent = parent
        self.log = parent.log
        self.clock = clock
        self.mode = None            # custom_mode of the last heartbeat
        self.heard = None           # clock time of the last heartbeat
        self.last_write = None
        self.prev_engaged = None
        self.disc_latched = False
        self.msg, self.msg_until = "", 0.0
        self.requests = queue.Queue()
        self.enabled = self._keys_present()
        if not self.enabled:
            return
        self.seq = self._read("APCMDSEQ")
        parent.db_callback_add("APCMDSEQ", self._on_seq)

    def _keys_present(self):
        missing = []
        for key in STATE_KEYS + READ_KEYS:
            try:
                self.parent.db_get_item(key)
            except KeyError:
                missing.append(key)
        if missing:
            self.log.error(
                "mavlink: autopilot adapter off: the database has no %s "
                "(add database/autopilot.yaml)", ", ".join(missing))
            return False
        return True

    def _read(self, key):
        v = self.parent.db_read(key)
        return v[0] if isinstance(v, tuple) else v

    def _say(self, text, now):
        self.msg, self.msg_until = text[:MSG_MAX], now + MSG_HOLD_S
        self.log.info("mavlink: autopilot: %s", text)

    def fresh(self, now):
        return self.heard is not None and now - self.heard <= HOLD_S

    # ------------------------------------------------------------------
    # ArduPlane -> FIX
    # ------------------------------------------------------------------
    def heartbeat(self, custom_mode):
        """Take the custom_mode of one fixed-wing HEARTBEAT and publish."""
        if not self.enabled:
            return
        now = self.clock()
        if self.mode is not None and custom_mode != self.mode:
            # A new mode is newer news than an earlier refusal: without
            # this, "VS is not available" hid "ArduPlane mode FBWA" for up
            # to MSG_HOLD_S after a mode switch on the transmitter.
            self.msg = ""
        self.mode = custom_mode
        self.heard = now
        eng = engaged(custom_mode)
        if self.prev_engaged and not eng:
            self.disc_latched = True
            self.log.info("mavlink: autopilot disconnected (%s)",
                          mode_name(custom_mode))
        self.prev_engaged = eng
        self._publish(now)

    def refresh(self):
        """Rewrite the state before its tol runs out, while it is fresh."""
        if not self.enabled:
            return
        now = self.clock()
        if not self.fresh(now):
            return
        if self.last_write is not None and now - self.last_write < REFRESH_S:
            return
        self._publish(now)

    def _publish(self, now):
        apeng, lat, vert = state(self.mode)
        if self.msg and now >= self.msg_until:
            self.msg = ""
        msg = self.msg
        if not msg and apeng and self.mode not in GA_MODES:
            msg = "ArduPlane mode {}".format(mode_name(self.mode))
        w = self.parent.db_write
        w("APENG", apeng)
        # ArduPlane has no flight director and no separate yaw damper, and
        # reports no armed modes or capture phase. FDPITCH and FDROLL are
        # never written, so they stay old and no display draws bars.
        w("FDENG", False)
        w("YDENG", False)
        w("APLAT", lat)
        w("APLATARM", "")
        w("APVERT", vert)
        w("APVERTARM", "")
        w("APALTCAP", False)
        w("APCAPS", ",".join(CAPS))
        w("APMSG", msg)
        w("APDISC", self.disc_latched)
        self.last_write = now

    # ------------------------------------------------------------------
    # FIX -> ArduPlane: requests
    # ------------------------------------------------------------------
    def _on_seq(self, key, value, udata):
        """APCMDSEQ callback, on the writer's thread: queue the request."""
        seq = value[0] if isinstance(value, tuple) else value
        if seq == self.seq:
            return
        self.seq = seq
        self.requests.put(str(self._read("APCMD")).strip().upper())

    def pending(self):
        return self.enabled and not self.requests.empty()

    def poll(self, mav):
        """Run from Mav.checkMode: turn queued requests into MAVREQ* keys.

        Returns True when a request was handled."""
        if not self.enabled:
            return False
        now = self.clock()
        handled = False
        while True:
            try:
                request = self.requests.get_nowait()
            except queue.Empty:
                break
            handled = True
            # Only the quorum leader commands the flight controller, as in
            # Mav.checkMode; another gateway's leader answers the request.
            if mav.parent.quorum.leader:
                self._command(request, mav, now)
        if handled and self.fresh(now):
            # A refusal or an acknowledgement shows now, not on the next
            # heartbeat.
            self._publish(now)
        return handled

    def _command(self, request, mav, now):
        if request not in REQUESTS:
            self._say("Unknown request {}".format(request or '""'), now)
            return
        if not self.fresh(now):
            self._say("{} refused: no heartbeat from the autopilot".format(
                request), now)
            return
        if request not in CAPS:
            self._say("{} is not available on this autopilot".format(request),
                      now)
            return
        eng = engaged(self.mode)
        if request == "DISC":
            if self.disc_latched:
                self.disc_latched = False
                self.log.info("mavlink: autopilot disconnect acknowledged")
            elif eng:
                self._request(MANUAL, request, mav, now)
            return
        if request == "AP":
            self._request(MANUAL if eng else CRUISE, request, mav, now)
            return
        # NAV
        if self.mode == GUIDED:
            self._request(CRUISE, request, mav, now)
        elif not eng:
            self._say("NAV refused: engage AP first", now)
        elif not mav._apwpv:
            self._say("NAV refused: no active waypoint", now)
        else:
            self._request(GUIDED, request, mav, now)

    def _request(self, target, request, mav, now):
        """Write the MAVREQ* key for ``target``, for Mav.checkMode to send."""
        # Mav.setMode's own conditions, checked first so a refusal is said
        # rather than dropped (setMode only writes "Invalid Request").
        if mav._apstat not in ("TRIM", "ARMED"):
            self._say("{} refused: autopilot status {}".format(
                request, mav._apstat), now)
            return
        if target != MANUAL and mav._apstat != "ARMED":
            self._say("{} refused: autopilot not armed".format(request), now)
            return
        name = MODE_NAMES[target]
        # Mav keeps the mode it last set, not the one the autopilot reports:
        # after a mode change from the transmitter they differ, and checkMode
        # would skip a request for the mode it thinks is current. The pilot
        # is asking now, so start from what the autopilot reports.
        reported = MODE_NAMES.get(self.mode, mode_name(self.mode))
        if reported == name:
            return
        mav._apmode = reported
        for other in mav._apmodes:
            if other != name and self._read("MAVREQ" + other):
                self.parent.db_write("MAVREQ" + other, False)
        self.parent.db_write("MAVREQ" + name, True)
        self.log.info("mavlink: autopilot: %s requests %s", request, name)

    def close(self):
        if self.enabled:
            try:
                self.parent.db_callback_del("APCMDSEQ", self._on_seq)
            except Exception:
                pass
