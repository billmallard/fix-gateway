"""X-Plane as the autopilot source (AFC2, AER-5806).

The adapter presses X-Plane's autopilot commands for the pilot's requests and
writes what X-Plane reports into the autopilot keys (doc/autopilot_keys.md).
These tests run it on the real database the default configuration loads, so
every key is read back with its real type, range and tol. The sim is a dict
of dataref values fed through the same RREF indices the plugin subscribes.
"""

import struct
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

import fixgw.plugin as plugin_base
from fixgw.plugins.xplane import MainThread
from fixgw.plugins.xplane import autopilot as ap

# A Laminar C172 G1000 (GFC-700) in level cruise: AP on, HDG, ALT hold at
# 3500 with 4500 preselected, the HSI on GPS with a signal.
CRUISE = {
    "servos_on": 1, "fd_mode": 2, "yd_on": 0,
    "heading_status": 2, "heading_hold_status": 0, "track_status": 0,
    "nav_status": 0, "gpss_status": 0, "backcourse_status": 0,
    "roll_status": 0, "rate_status": 0, "attitude_status": 0,
    "approach_status": 0,
    "pitch_status": 0, "vvi_status": 0, "speed_status": 0,
    "altitude_hold_status": 2, "glideslope_status": 0, "vnav_status": 0,
    "fd_pitch": 2.5, "fd_roll": -4.0, "alt_hold": 3500.0,
    "alt_dial": 4500.0, "vvi_dial": 0.0, "ias_dial": 100.0,
    "hdg_dial": 90.0, "nav1_obs": 180.0, "nav2_obs": 270.0, "baro": 29.92,
    "hsi_source": 2, "hsi_signal": 1, "nav1_type": 0, "nav2_type": 0,
    "ap_type": 2, "single_axis": 0, "has_yd": 0,
}
INDEX = {name: ap.AP_RREF_BASE + i for i, (name, _d) in enumerate(ap.DATAREFS)}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Sim:
    """The adapter, its clock, the packets it sent, and the sim's datarefs."""

    def __init__(self, database, state=None, config=None):
        self.db = database
        self.clock = Clock()
        self.sent = []
        self.knob = None            # replies of lag, once the knob is turned on
        self.knob_seen = 0
        self.knob_queue = []
        self.sent_at = []           # the clock time of each packet in sent
        self.parent = plugin_base.PluginBase("xplane", {}, {})
        self.ap = ap.Autopilot(self.parent, self._send, config,
                               clock=self.clock)
        self.state = dict(CRUISE if state is None else state)

    def _send(self, packet):
        self.sent.append(packet)
        self.sent_at.append(self.clock.t)

    def step(self, dt=0.05, **changes):
        """Advance the clock, apply changes to the sim, deliver one reply."""
        self.reply(dt, **changes)
        self.ap.poll()

    def reply(self, dt=0.05, **changes):
        """One reply only: what the plugin does when a packet lands before
        its next loop pass."""
        self.clock.t += dt
        self.state.update(changes)
        self._turn_knob()
        for name, value in self.state.items():
            assert self.ap.feed(INDEX[name], value)
        self.ap.publish()

    def turn_knob(self, lag=0):
        """From now on, X-Plane's heading_up / heading_down turn the dial one
        degree each, ``lag`` replies after they were sent."""
        self.knob = lag
        self.knob_seen = len(self.sent)

    def _turn_knob(self):
        if self.knob is None:
            return
        new = [p[5:].decode() for p in self.sent[self.knob_seen:]
               if p[:5] == b"CMND\x00"]
        self.knob_seen = len(self.sent)
        self.knob_queue.append(new)
        if len(self.knob_queue) <= self.knob:
            return
        for command in self.knob_queue.pop(0):
            step = {ap.WALK_UP: 1.0, ap.WALK_DOWN: -1.0}.get(command)
            if step is not None:
                self.state["hdg_dial"] = (self.state["hdg_dial"] + step) % 360.0

    def read(self, key):
        return self.db.read(key)[0]

    def request(self, verb):
        """What the control head does: APCMD, then bump APCMDSEQ."""
        self.db.write("APCMD", verb)
        self.db.write("APCMDSEQ", self.read("APCMDSEQ") + 1)

    def commands(self):
        return [p[5:].decode() for p in self.sent if p[:5] == b"CMND\x00"]

    def drefs(self):
        out = []
        for p in self.sent:
            if p[:4] == b"DREF":
                _h, value, path = struct.unpack("<4sxf500s", p)
                out.append((path.rstrip(b"\x00").decode(), value))
        return out


@pytest.fixture
def sim(database):
    s = Sim(database)
    s.step()
    return s


# ---------------------------------------------------------------------------
# Every name is X-Plane's
# ---------------------------------------------------------------------------

def test_every_dataref_is_subscribed_once_on_its_own_index():
    names = [n for n, _d in ap.DATAREFS]
    drefs = [d for _n, d in ap.DATAREFS]
    assert len(set(names)) == len(names) and len(set(drefs)) == len(drefs)
    assert set(names) == set(CRUISE)
    assert all(d.startswith("sim/") for d in drefs)


def test_cmnd_packet_is_header_null_and_path():
    assert ap.cmnd_packet("sim/autopilot/NAV") == b"CMND\x00sim/autopilot/NAV"


# ---------------------------------------------------------------------------
# State: exactly what the sim reports
# ---------------------------------------------------------------------------

def test_cruise_state(sim):
    assert sim.read("APENG") is True
    assert sim.read("FDENG") is True
    assert sim.read("YDENG") is False
    assert (sim.read("APLAT"), sim.read("APLATARM")) == ("HDG", "")
    assert (sim.read("APVERT"), sim.read("APVERTARM")) == ("ALT", "")
    assert sim.read("APALTCAP") is False
    assert sim.read("APALTREF") == 3500
    assert (sim.read("FDPITCH"), sim.read("FDROLL")) == (2.5, -4.0)
    assert sim.read("APCAPS") == "AP,FD,LVL,HDG,NAV,APR,IAS,VNAV,VS,ALT,DISC"
    assert sim.read("APMSG") == ""
    assert sim.read("APDISC") is False


def test_held_altitude_is_the_sims_hold_not_the_preselect(sim):
    """APALTREF follows altitude_hold_ft; turning the dial (ALTSEL) while
    holding moves only ALTSEL (Bill, AER-5808)."""
    sim.step(alt_dial=6500.0)
    assert sim.read("ALTSEL") == 6500
    assert sim.read("APALTREF") == 3500
    sim.step(alt_hold=6499.6)
    assert sim.read("APALTREF") == 6500


def test_held_altitude_goes_old_with_the_rest_when_the_sim_stops(sim, monkeypatch):
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    sim.step()
    assert sim.db.read("APALTREF")[2] is False
    now[0] += 2.1
    sim.clock.t += 2.1
    sim.ap.publish()
    assert sim.db.read("APALTREF")[2] is True


def test_hdg_to_nav_capture_on_gps(sim):
    sim.step(nav_status=1)
    assert (sim.read("APLAT"), sim.read("APLATARM")) == ("HDG", "GPS")
    sim.step(nav_status=2, heading_status=0)
    assert (sim.read("APLAT"), sim.read("APLATARM")) == ("GPS", "")


@pytest.mark.parametrize("src,navtype,name", [
    (0, 3, "VOR"), (0, 4, "LOC"), (0, 5, "LOC"), (1, 4, "LOC"), (1, 3, "VOR"),
    (0, 0, "VOR"), (2, 4, "GPS"), (3, 0, "GPS")])
def test_nav_mode_is_named_by_what_the_hsi_follows(sim, src, navtype, name):
    sim.step(hsi_source=src, nav1_type=navtype, nav2_type=navtype,
             nav_status=2, heading_status=0)
    assert sim.read("APLAT") == name


def test_vs_to_alt_capture(sim):
    sim.step(altitude_hold_status=1, vvi_status=2)
    assert (sim.read("APVERT"), sim.read("APVERTARM")) == ("VS", "ALT")
    sim.step(altitude_hold_status=2, vvi_status=0)
    assert (sim.read("APVERT"), sim.read("APVERTARM")) == ("ALT", "")


def test_ias_climb_with_alt_armed(sim):
    sim.step(altitude_hold_status=1, speed_status=2)
    assert (sim.read("APVERT"), sim.read("APVERTARM")) == ("IAS", "ALT")


def test_approach_armed_then_captured_ils(sim):
    sim.step(hsi_source=0, nav1_type=4, nav_status=1, glideslope_status=1)
    assert (sim.read("APLAT"), sim.read("APLATARM")) == ("HDG", "LOC")
    assert sim.read("APVERTARM") == "GS"
    sim.step(nav_status=2, heading_status=0, glideslope_status=2,
             altitude_hold_status=0)
    assert (sim.read("APLAT"), sim.read("APVERT")) == ("LOC", "GS")


def test_gps_glidepath_is_gp(sim):
    sim.step(nav_status=2, heading_status=0, glideslope_status=1)
    assert sim.read("APVERTARM") == "GP"


def test_roll_and_pitch_hold(sim):
    sim.step(heading_status=0, roll_status=2, altitude_hold_status=0,
             pitch_status=2)
    assert (sim.read("APLAT"), sim.read("APVERT")) == ("ROL", "PIT")


def test_neither_ap_nor_fd_on_means_no_modes(sim):
    sim.step(servos_on=0, fd_mode=0)
    assert (sim.read("APLAT"), sim.read("APVERT")) == ("", "")
    assert (sim.read("APLATARM"), sim.read("APVERTARM")) == ("", "")
    assert sim.read("APENG") is False and sim.read("FDENG") is False


def test_fd_only_shows_its_modes(sim):
    sim.step(servos_on=0, fd_mode=1)
    assert sim.read("APENG") is False and sim.read("FDENG") is True
    assert (sim.read("APLAT"), sim.read("APVERT")) == ("HDG", "ALT")


@pytest.mark.parametrize("state,caps", [
    ({"ap_type": 2}, "AP,FD,LVL,HDG,NAV,APR,IAS,VNAV,VS,ALT,DISC"),
    ({"ap_type": 3}, "AP,HDG,NAV,APR,VS,ALT,DISC"),
    ({"ap_type": 5}, "AP,HDG,NAV,APR,DISC"),
    ({"ap_type": 6, "single_axis": 1}, "AP,HDG,NAV,APR,DISC"),
    ({"ap_type": 2, "has_yd": 1}, "AP,FD,YD,LVL,HDG,NAV,APR,IAS,VNAV,VS,ALT,DISC"),
    ({"ap_type": 0}, "AP,FD,LVL,HDG,TRK,NAV,APR,IAS,VNAV,VS,ALT,DISC"),
    ({"ap_type": 42}, "AP,FD,LVL,HDG,TRK,NAV,APR,IAS,VNAV,VS,ALT,DISC"),
])
def test_caps_follow_the_aircrafts_autopilot(sim, state, caps):
    sim.step(**state)
    assert sim.read("APCAPS") == caps


# ---------------------------------------------------------------------------
# Requests: pressed into the sim, the state stays the sim's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb,command", [
    ("HDG", "sim/autopilot/heading"),
    ("NAV", "sim/autopilot/NAV"),
    ("APR", "sim/autopilot/approach"),
    ("ALT", "sim/autopilot/altitude_hold"),
    ("VS", "sim/autopilot/alt_vs"),
    ("IAS", "sim/autopilot/level_change"),
    ("VNAV", "sim/autopilot/vnav"),
    ("LVL", "sim/autopilot/return_to_level"),
    ("AP", "sim/autopilot/servos_off_any"),
    ("DISC", "sim/autopilot/servos_off_any"),
])
def test_request_presses_the_sim_command(sim, verb, command):
    sim.request(verb)
    sim.step()
    assert sim.commands() == [command]


def test_request_changes_no_state_by_itself(sim):
    sim.request("NAV")
    sim.step()
    sim.step()
    assert sim.commands() == ["sim/autopilot/NAV"]
    assert (sim.read("APLAT"), sim.read("APLATARM")) == ("HDG", "")


def test_ap_engages_when_off(sim):
    sim.step(servos_on=0, fd_mode=1)
    sim.request("AP")
    sim.step()
    assert sim.commands() == ["sim/autopilot/servos_on"]


def test_fd_on_off_and_refused_with_ap_engaged(sim):
    sim.request("FD")
    sim.step()
    assert sim.commands() == []
    assert sim.read("APMSG") == "FD stays on while the autopilot is engaged"
    sim.step(servos_on=0, fd_mode=1)
    sim.request("FD")
    sim.step()
    sim.step(fd_mode=0)
    sim.request("FD")
    sim.step()
    assert sim.commands() == ["sim/autopilot/servos_fdir_off",
                              "sim/autopilot/fdir_on"]


def test_same_verb_twice_is_two_presses(sim):
    sim.request("HDG")
    sim.request("HDG")
    sim.step()
    assert sim.commands() == ["sim/autopilot/heading"] * 2


def test_baseline_sequence_is_never_replayed(database):
    database.write("APCMD", "AP")
    database.write("APCMDSEQ", 41)
    s = Sim(database, dict(CRUISE, servos_on=0))
    s.step()
    # A client that reconnects re-sends the counter it last wrote.
    database.write("APCMDSEQ", 41)
    s.step()
    assert s.commands() == []
    s.request("AP")
    s.step()
    assert s.commands() == ["sim/autopilot/servos_on"]


def test_a_counter_that_went_back_to_zero_is_a_request(sim):
    sim.db.write("APCMDSEQ", 7)
    sim.step()
    sim.db.write("APCMD", "HDG")
    sim.db.write("APCMDSEQ", 0)
    sim.step()
    assert sim.commands()[-1] == "sim/autopilot/heading"


def test_rewriting_the_same_counter_is_not_a_request(sim):
    sim.db.write("APCMD", "HDG")
    sim.db.write("APCMDSEQ", sim.read("APCMDSEQ"))
    sim.step()
    assert sim.commands() == []


def test_unknown_request_is_refused_on_apmsg(sim):
    sim.request("ROLL")
    sim.step()
    assert sim.commands() == []
    assert sim.read("APMSG") == "Unknown request ROLL"


def test_unsupported_request_is_refused_on_apmsg(sim):
    sim.request("TRK")                      # a GFC-700 has no TRK
    sim.step()
    assert sim.commands() == []
    assert sim.read("APMSG") == "TRK is not available on this autopilot"


def test_message_clears_after_its_hold(sim):
    sim.request("ROLL")
    sim.step()
    sim.step(dt=ap.MSG_HOLD_S - 0.2)
    assert sim.read("APMSG") == "Unknown request ROLL"
    sim.step(dt=0.3)
    assert sim.read("APMSG") == ""


# The negative test (AFC2 done-when 2; brief section 8): a request for NAV
# with no nav source never shows NAV, and says why.
@pytest.mark.parametrize("src,name", [(0, "NAV1"), (1, "NAV2"), (2, "GPS")])
def test_nav_with_no_signal_is_refused_and_aplat_unchanged(sim, src, name):
    sim.step(hsi_source=src, hsi_signal=0)
    sim.request("NAV")
    for _ in range(40):                     # 2 s of replies
        sim.step()
        assert (sim.read("APLAT"), sim.read("APLATARM")) == ("HDG", "")
    assert sim.commands() == []
    assert sim.read("APMSG") == "NAV refused: no signal on {}".format(name)


def test_apr_with_no_signal_is_refused(sim):
    sim.step(hsi_source=0, hsi_signal=0)
    sim.request("APR")
    sim.step()
    assert sim.commands() == []
    assert sim.read("APMSG") == "APR refused: no signal on NAV1"


def test_nav_cancel_is_pressed_even_after_the_signal_is_lost(sim):
    sim.step(nav_status=2, heading_status=0, hsi_signal=0)
    sim.request("NAV")
    sim.step()
    assert sim.commands() == ["sim/autopilot/NAV"]


def test_request_with_no_data_from_the_sim_is_refused(sim):
    sim.clock.t += ap.FRESH_S + 0.5
    sim.request("HDG")
    sim.ap.poll()
    assert sim.commands() == []
    assert sim.ap.msg == "HDG refused: no autopilot data from X-Plane"


# ---------------------------------------------------------------------------
# Disconnect alert: latched by the source until DISC acknowledges it
# ---------------------------------------------------------------------------

def test_disconnect_latches_until_acknowledged(sim):
    sim.step(servos_on=0)
    assert sim.read("APENG") is False and sim.read("APDISC") is True
    sim.step(dt=5.0)
    assert sim.read("APDISC") is True
    sim.request("AP")                       # re-engaging does not acknowledge
    sim.step()
    sim.step(servos_on=1)
    assert sim.read("APDISC") is True
    sim.request("DISC")
    sim.step()
    assert sim.read("APDISC") is False
    assert sim.commands() == ["sim/autopilot/servos_on"]   # DISC sent nothing


def test_no_alert_when_the_autopilot_was_never_on(database):
    s = Sim(database, dict(CRUISE, servos_on=0))
    s.step()
    s.step()
    assert s.read("APDISC") is False


# ---------------------------------------------------------------------------
# Stale is removed: nothing is written from old or partial data
# ---------------------------------------------------------------------------

def test_keys_go_old_within_their_tol_when_the_sim_stops(sim):
    """Real time: the database ages keys on the wall clock."""
    sim.step()
    t0 = time.time()
    keys = ap.STATE_KEYS
    assert not any(sim.db.read(k)[2] for k in keys)
    while time.time() - t0 < 2.3:
        sim.clock.t += 0.1
        sim.ap.poll()                       # the loop keeps running, no data
        time.sleep(0.1)
    for key in keys:
        item = sim.db.get_raw_item(key)
        assert sim.db.read(key)[2] is True, key
        assert item.tol in (1000, 2000)


def test_fd_goes_old_first(sim):
    sim.step()
    time.sleep(1.2)
    assert sim.db.read("FDPITCH")[2] is True
    assert sim.db.read("FDROLL")[2] is True
    assert sim.db.read("APLAT")[2] is False


def test_unchanged_keys_are_rewritten_well_inside_tol(sim):
    written = []
    sim.db.callback_add("t", "APLAT", lambda k, v, u: written.append(sim.clock.t), None)
    for _ in range(40):
        sim.step(dt=0.05)
    assert written
    gaps = [b - a for a, b in zip(written, written[1:])]
    assert max(gaps) <= ap.REFRESH_S + 0.05 and ap.REFRESH_S < 1.0


def test_a_silent_dataref_stops_publishing(sim):
    """One dataref the sim stopped answering: no state from the rest."""
    sim.step()
    t_silent = sim.clock.t                  # nav_status's last reply
    writes = []
    sim.db.callback_add("t", "FDPITCH",
                        lambda k, v, u: writes.append(sim.clock.t), None)
    for i in range(40):
        sim.clock.t += 0.05
        sim.state["fd_pitch"] = 2.5 + 0.1 * i   # a new value every reply
        for name, value in sim.state.items():
            if name != "nav_status":
                sim.ap.feed(INDEX[name], value)
        sim.ap.publish()
    assert writes and max(writes) <= t_silent + ap.FRESH_S
    assert sim.clock.t - t_silent > 1.5 * ap.FRESH_S


def test_never_answered_dataref_is_logged_by_name(database):
    s = Sim(database)
    s.parent.log = MagicMock()
    s.ap.log = s.parent.log
    for _ in range(int(ap.UNANSWERED_S / 0.5) + 2):
        s.clock.t += 0.5
        for name, value in s.state.items():
            if name != "has_yd":
                s.ap.feed(INDEX[name], value)
        s.ap.publish()
    errors = [c for c in s.ap.log.error.call_args_list
              if "has not answered" in c.args[0]]
    assert len(errors) == 1
    assert "sim/aircraft/overflow/has_yawdamp_but" in errors[0].args[1]
    assert s.db.get_raw_item("APLAT").written is False


# ---------------------------------------------------------------------------
# Targets, both ways
# ---------------------------------------------------------------------------

def test_sim_dials_fill_the_targets(sim):
    assert (sim.read("ALTSEL"), sim.read("VSSEL"), sim.read("IASSEL")) == (
        4500, 0, 100.0)
    sim.step(alt_dial=6000.0, vvi_dial=-500.0, ias_dial=90.0)
    assert (sim.read("ALTSEL"), sim.read("VSSEL"), sim.read("IASSEL")) == (
        6000, -500, 90.0)


def test_pilot_target_is_written_to_the_dial_and_not_overwritten(sim):
    sim.db.write("ALTSEL", 8500)
    sim.step()
    assert sim.drefs() == [("sim/cockpit2/autopilot/altitude_dial_ft", 8500.0)]
    for _ in range(5):                      # the dial has not followed yet
        sim.step()
        assert sim.read("ALTSEL") == 8500
    sim.step(alt_dial=8500.0)
    assert sim.read("ALTSEL") == 8500
    assert len(sim.drefs()) == 1            # no echo back to the sim


def test_a_target_the_sim_refuses_settles_to_the_sim(sim):
    sim.db.write("IASSEL", 400.0)
    sim.step()
    sim.step(dt=ap.TARGET_SETTLE_S + 0.1, ias_dial=100.0)
    assert sim.read("IASSEL") == 100.0
    sim.step()
    assert len(sim.drefs()) == 1


def test_vs_press_then_the_pilots_rate(sim):
    sim.db.write("VSSEL", 700)
    sim.step(vvi_dial=700.0)                # dial follows the pilot's 700
    sent = len(sim.drefs())
    sim.request("VS")
    sim.step()
    # X-Plane's alt_vs syncs the dial to the current rate first ...
    sim.step(vvi_dial=0.0, vvi_status=2, altitude_hold_status=1)
    assert sim.read("VSSEL") == 700         # ... and the bus does not flicker
    sim.step(dt=ap.TARGET_DELAY_S)
    assert sim.drefs()[sent:] == [("sim/cockpit2/autopilot/vvi_dial_fpm", 700.0)]
    sim.step(vvi_dial=700.0)
    assert sim.read("VSSEL") == 700


def test_ias_press_with_no_target_takes_the_sims_speed(sim):
    sim.db.write("IASSEL", 0.0)
    sim.step(ias_dial=0.0)
    sim.request("IAS")
    sim.step()
    sim.step(dt=1.0, ias_dial=97.0, speed_status=2)
    assert sim.read("IASSEL") == 97.0
    assert all(d[0] != "sim/cockpit2/autopilot/airspeed_dial_kts" or d[1] != 0.0
               for d in sim.drefs()[1:])


# ---------------------------------------------------------------------------
# The heading bug, the OBS courses and the altimeter (AFC2b, AER-5856)
# ---------------------------------------------------------------------------

# (key, the sim's dataref name here, its DREF path, a pilot step).
SETTINGS = [
    ("HEADBUG", "hdg_dial",
     "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot", 1.0),
    ("NAV1CRS", "nav1_obs",
     "sim/cockpit2/radios/actuators/nav1_obs_deg_mag_pilot", 1.0),
    ("NAV2CRS", "nav2_obs",
     "sim/cockpit2/radios/actuators/nav2_obs_deg_mag_pilot", 1.0),
    ("BARO", "baro",
     "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot", 0.01),
]
ANGLES = [s for s in SETTINGS if s[0] != "BARO"]
# The settings written to their dial. The heading bug is walked instead.
WRITTEN = [s for s in SETTINGS if s[0] != "HEADBUG"]
WRITTEN_ANGLES = [s for s in ANGLES if s[0] != "HEADBUG"]


def test_every_setting_is_a_target_on_one_sync():
    by_key = {t[0]: t for t in ap.TARGETS}
    for key, name, dref, _step in SETTINGS:
        assert by_key[key][1:3] == (name, dref)
        assert (name, dref) in ap.DATAREFS


@pytest.mark.parametrize("key, name, _dref, step", SETTINGS)
def test_a_change_in_the_sims_cockpit_shows_on_the_bus(sim, key, name, _dref,
                                                       step):
    start = sim.read(key)
    assert start == pytest.approx(CRUISE[name])
    sim.step(**{name: CRUISE[name] + 5 * step})
    assert sim.read(key) == pytest.approx(CRUISE[name] + 5 * step)
    assert sim.drefs() == []                # nothing echoed back to the sim


@pytest.mark.parametrize("key, name, dref, step", WRITTEN)
def test_a_pilot_change_moves_the_dial_and_stays(sim, key, name, dref, step):
    want = CRUISE[name] + 3 * step
    sim.db.write(key, want)
    sim.step()
    assert sim.drefs() == [(dref, pytest.approx(want))]
    for _ in range(5):                      # the dial has not followed yet
        sim.step()
        assert sim.read(key) == pytest.approx(want)
    sim.step(**{name: want})
    for _ in range(3):
        sim.step(dt=ap.TARGET_SETTLE_S)
        assert sim.read(key) == pytest.approx(want)
    assert len(sim.drefs()) == 1


@pytest.mark.parametrize("key, name, dref, step", WRITTEN)
def test_a_reply_in_flight_does_not_revert_a_pilot_change(sim, key, name,
                                                          dref, step):
    """The pilot's change lands, then a reply sent before X-Plane saw it
    arrives ahead of the plugin's next loop pass, with the dial a step off
    from where the bus last had it."""
    want = CRUISE[name] + 10 * step
    sim.db.write(key, want)
    sim.reply(**{name: CRUISE[name] + step})
    assert sim.read(key) == pytest.approx(want)
    sim.ap.poll()
    sim.step(**{name: CRUISE[name] + step})
    assert sim.read(key) == pytest.approx(want)
    assert sim.drefs() == [(dref, pytest.approx(want))]


@pytest.mark.parametrize("key, name, dref, step", WRITTEN)
def test_a_fast_knob_spin_never_steps_back(sim, key, name, dref, step):
    """Twelve clicks, one per reply, with the sim's dial two replies behind:
    the bus only ever moves forward and ends on the last click."""
    start = CRUISE[name]
    clicks = [start + (i + 1) * step for i in range(12)]
    seen = []
    for i, value in enumerate(clicks):
        sim.db.write(key, value)
        lagged = clicks[i - 2] if i >= 2 else start
        sim.step(**{name: lagged})
        seen.append(sim.read(key))
    for _ in range(3):
        sim.step(**{name: clicks[-1]})
        seen.append(sim.read(key))
    assert seen == sorted(seen)
    assert seen[-1] == pytest.approx(clicks[-1])
    assert sim.drefs()[-1] == (dref, pytest.approx(clicks[-1]))


def test_a_setting_the_sim_will_not_take_settles_to_the_sims(sim):
    sim.db.write("BARO", 31.50)
    sim.step()
    sim.step(dt=ap.TARGET_SETTLE_S + 0.1)   # the dial stays at 29.92
    assert sim.read("BARO") == pytest.approx(29.92)
    sim.step()
    assert len(sim.drefs()) == 1


@pytest.mark.parametrize("key, name, dref, _step", WRITTEN_ANGLES)
def test_a_pilot_wrap_from_359_to_0_is_one_degree(sim, key, name, dref, _step):
    sim.step(**{name: 359.0})
    assert sim.read(key) == 359.0
    sent = len(sim.drefs())
    sim.db.write(key, 0.0)
    sim.step()
    assert sim.drefs()[sent:] == [(dref, 0.0)]
    # X-Plane may report the dial a hair under 360, or at 360, rather than
    # at 0: the dial followed, so the pilot's 0 stays.
    for value in (359.98, 360.0):
        for _ in range(3):
            sim.step(dt=ap.TARGET_SETTLE_S, **{name: value})
            assert sim.read(key) == 0.0
    assert len(sim.drefs()) == sent + 1


@pytest.mark.parametrize("key, name, _dref, _step", ANGLES)
def test_a_dial_turned_to_360_in_the_sim_reads_0(sim, key, name, _dref, _step):
    sim.step(**{name: 360.0})
    assert sim.read(key) == 0.0


@pytest.mark.parametrize("key, name, _dref, _step", ANGLES)
def test_a_sim_wrap_from_359_to_0_is_one_degree(sim, key, name, _dref, _step):
    sim.step(**{name: 359.5})
    sim.step(**{name: 0.4})
    assert sim.read(key) == pytest.approx(0.4)
    sim.step(**{name: 359.95})              # the database holds 359.9 at most
    assert sim.read(key) == pytest.approx(359.9)
    writes = []
    with patch.object(sim.parent, "db_write",
                      side_effect=lambda k, v: writes.append(k)):
        for _ in range(3):
            sim.step(dt=0.2)
    assert key not in writes                # not rewritten on every reply
    assert sim.drefs() == []


# ---------------------------------------------------------------------------
# The heading bug is walked, never written (XPB-018, AER-6268)
# ---------------------------------------------------------------------------

HDG_DREF = "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot"


def _walk(sim, replies=60, dt=0.05):
    """Replies until the walk is over; the dial after each one."""
    dials = []
    for _ in range(replies):
        sim.step(dt=dt)
        dials.append(sim.state["hdg_dial"])
        if sim.ap.walk is None:
            break
    return dials


def _batches(sim, since):
    """The heading commands sent from ``since`` on, one list per batch."""
    out = {}
    for p, t in zip(sim.sent[since:], sim.sent_at[since:]):
        c = p[5:].decode() if p[:5] == b"CMND\x00" else None
        if c in (ap.WALK_UP, ap.WALK_DOWN):
            out.setdefault(t, []).append(c)
    return list(out.values())


@pytest.mark.parametrize("start, want, command, steps", [
    (73.0, 113.2, ap.WALK_UP, 40),          # AER-6268, the bench's change
    (73.0, 33.0, ap.WALK_DOWN, 40),
    (350.0, 30.0, ap.WALK_UP, 40),          # across north, still the short way
    (10.0, 330.0, ap.WALK_DOWN, 40),
    (90.0, 269.0, ap.WALK_UP, 179),
    (90.0, 271.0, ap.WALK_DOWN, 179),
])
def test_a_large_bug_change_is_walked_the_short_way(sim, start, want,
                                                    command, steps):
    """A large heading bug change goes to X-Plane as batches of one-degree
    knob steps, every one the short way, and never as a write to the dial:
    a write turned the C172 320 deg the long way on the bench."""
    sim.step(hdg_dial=start)
    sim.turn_knob()
    sent = len(sim.sent)
    sim.db.write("HEADBUG", want)
    _walk(sim)
    assert sim.ap.walk is None
    assert sim.drefs() == []
    batches = _batches(sim, sent)
    assert len(batches) > 1
    assert all(len(b) <= ap.WALK_STEPS for b in batches)
    assert sum(batches, []) == [command] * steps
    assert sim.state["hdg_dial"] == pytest.approx(round(want) % 360)


def test_a_walk_waits_for_the_dial_and_never_overshoots(sim):
    """Replies two behind the knob: a batch the dial has not shown yet is not
    sent again, so the dial goes 73 to 113 one way and stops there."""
    sim.step(hdg_dial=73.0)
    sim.turn_knob(lag=2)
    sim.db.write("HEADBUG", 113.0)
    dials = _walk(sim, replies=200)
    for _ in range(5):
        sim.step()
        dials.append(sim.state["hdg_dial"])
    assert dials == sorted(dials)
    assert dials[-1] == 113.0
    assert sim.commands().count(ap.WALK_UP) == 40
    assert ap.WALK_DOWN not in sim.commands()


def test_the_bus_keeps_the_pilots_bug_through_the_walk(sim):
    """The bug on the bus does not flicker back through the dial's steps,
    however long the walk takes, then takes the dial's value."""
    sim.step(hdg_dial=90.0)
    sim.turn_knob(lag=1)
    sim.db.write("HEADBUG", 270.4)
    t0 = sim.clock.t
    seen = []
    for _ in range(400):
        sim.step(dt=0.1)
        seen.append(sim.read("HEADBUG"))
        if sim.ap.walk is None:
            break
    assert sim.ap.walk is None
    assert sim.clock.t - t0 > ap.TARGET_SETTLE_S        # longer than a write
    assert seen[:-1] == [pytest.approx(270.4)] * (len(seen) - 1)
    sim.step()
    assert sim.read("HEADBUG") == pytest.approx(270.0)
    assert sim.drefs() == []


def test_a_bug_moved_again_mid_walk_walks_to_the_new_value(sim):
    sim.step(hdg_dial=73.0)
    sim.turn_knob(lag=1)
    sim.db.write("HEADBUG", 113.0)
    for _ in range(3):
        sim.step()
    assert 73.0 < sim.state["hdg_dial"] < 113.0
    sim.db.write("HEADBUG", 150.0)
    _walk(sim, replies=200)
    assert sim.state["hdg_dial"] == 150.0
    assert sim.read("HEADBUG") == 150.0
    assert ap.WALK_DOWN not in sim.commands()


def test_a_fast_knob_spin_on_the_glass_never_steps_the_bug_back(sim):
    """Twelve clicks, one per reply, with X-Plane two replies behind."""
    sim.step(hdg_dial=90.0)
    sim.turn_knob(lag=2)
    seen = []
    for i in range(12):
        sim.db.write("HEADBUG", 91.0 + i)
        sim.step()
        seen.append(sim.read("HEADBUG"))
    for _ in range(10):
        sim.step()
        seen.append(sim.read("HEADBUG"))
    assert seen == sorted(seen)
    assert seen[-1] == 102.0 and sim.state["hdg_dial"] == 102.0
    assert ap.WALK_DOWN not in sim.commands()


@pytest.mark.parametrize("start, want, command", [
    (90.0, 91.0, ap.WALK_UP), (359.0, 0.0, ap.WALK_UP),
    (0.0, 359.0, ap.WALK_DOWN),
])
def test_one_click_is_one_step(sim, start, want, command):
    sim.step(hdg_dial=start)
    sim.turn_knob()
    sent = len(sim.sent)
    sim.db.write("HEADBUG", want)
    _walk(sim)
    assert _batches(sim, sent) == [[command]]
    assert sim.read("HEADBUG") == want


def test_a_bug_the_knob_cannot_move_settles_to_the_sim(sim):
    """No knob (the sim ignores the commands): the walk gives up, the bus
    takes the dial's value, and no more steps go out."""
    sim.step(hdg_dial=73.0)
    sim.db.write("HEADBUG", 113.0)
    for _ in range(int(ap.TARGET_SETTLE_S / 0.05) + 5):
        sim.step()
    assert sim.ap.walk is None
    assert sim.read("HEADBUG") == 73.0
    sent = len(sim.sent)
    for _ in range(20):
        sim.step()
    assert sim.sent[sent:] == []
    assert sim.drefs() == []


@pytest.mark.parametrize("a, b, angle, d", [
    (359.0, 0.0, True, 1.0), (0.0, 359.0, True, 1.0), (10.0, 350.0, True, 20.0),
    (360.0, 0.0, True, 0.0), (180.0, 0.0, True, 180.0), (359.0, 0.0, False, 359.0),
])
def test_target_diff(a, b, angle, d):
    assert ap.target_diff(a, b, angle) == pytest.approx(d)


# ---------------------------------------------------------------------------
# Wiring into the plugin
# ---------------------------------------------------------------------------

def _thread(database, autopilot=True):
    parent = plugin_base.PluginBase("xplane", {}, {})
    parent.config = {"udp_in": 49001, "udp_out": 49000,
                     "datarefs": {"NAV1CDI": "sim/cockpit/radios/nav1_hdef_dot"}}
    if autopilot:
        parent.config["autopilot"] = {"enabled": True}
    with patch("socket.socket"):
        return MainThread(parent)


def test_plugin_subscribes_the_adapters_datarefs(database):
    th = _thread(database)
    th.subscribe_datarefs(10)
    sent = [struct.unpack("<4sxii400s", c.args[0])
            for c in th.sock.sendto.call_args_list]
    by_index = {i: d.rstrip(b"\x00").decode() for _h, _f, i, d in sent}
    assert by_index[1000] == "sim/cockpit/radios/nav1_hdef_dot"
    for name, dref in ap.DATAREFS:
        assert by_index[INDEX[name]] == dref


def test_plugin_routes_rref_replies_and_publishes(database):
    th = _thread(database)
    pairs = [(INDEX[n], float(v)) for n, v in CRUISE.items()] + [(1000, 0.5)]
    data = b"RREF," + b"".join(struct.pack("<If", i, v) for i, v in pairs)
    th._ingest_rref(data)
    assert database.read("APLAT")[0] == "HDG"
    assert database.read("NAV1CDI")[0] == 0.5
    assert database.get_raw_item("APLAT").writers == ["xplane"]


def test_plugin_without_the_block_has_no_adapter(database):
    th = _thread(database, autopilot=False)
    assert th.autopilot is None
    th.subscribe_datarefs(10)
    assert len(th.sock.sendto.call_args_list) == 1


def test_adapter_stays_off_when_the_database_lacks_its_keys():
    parent = MagicMock()
    parent.db_get_item.side_effect = lambda k: (_ for _ in ()).throw(KeyError(k)) \
        if k.startswith("AP") else None
    a = ap.Autopilot(parent, MagicMock())
    assert a.enabled is False
    assert a.subscriptions() == []
    parent.db_callback_add.assert_not_called()
    assert "add database/autopilot.yaml" in parent.log.error.call_args.args[0]


def test_shipped_xplane_yaml_turns_the_adapter_on():
    cfg = yaml.safe_load(
        Path("src/fixgw/config/connections/xplane.yaml").read_text())
    assert cfg["xplane"]["autopilot"]["enabled"] is True


def test_plugin_ignores_a_plain_row_for_a_target(database):
    """An older xplane.yaml with HEADBUG still under datarefs: the plain read
    would copy a reply in flight over the pilot's change, so it is dropped."""
    parent = plugin_base.PluginBase("xplane", {}, {})
    parent.config = {"udp_in": 49001, "udp_out": 49000,
                     "datarefs": {"HEADBUG": "sim/cockpit/autopilot/heading_mag",
                                  "NAV1CDI": "sim/cockpit/radios/nav1_hdef_dot"},
                     "dataref_writes": {"BARO": "sim/x"},
                     "autopilot": {"enabled": True}}
    parent.log = MagicMock()
    with patch("socket.socket"):
        th = MainThread(parent)
    assert [k for k, _d, _s in th.dataref_map.values()] == ["NAV1CDI"]
    assert th.dref_writes == {}
    warned = " ".join(c.args[1] for c in parent.log.warning.call_args_list)
    assert "HEADBUG" in warned and "BARO" in warned


def test_plugin_keeps_the_plain_row_with_the_adapter_off(database):
    parent = plugin_base.PluginBase("xplane", {}, {})
    parent.config = {"udp_in": 49001, "udp_out": 49000,
                     "datarefs": {"HEADBUG": "sim/cockpit/autopilot/heading_mag"}}
    with patch("socket.socket"):
        th = MainThread(parent)
    assert [k for k, _d, _s in th.dataref_map.values()] == ["HEADBUG"]


def test_shipped_xplane_yaml_gives_each_target_one_path():
    cfg = yaml.safe_load(
        Path("src/fixgw/config/connections/xplane.yaml").read_text())["xplane"]
    for key in ap.TARGET_KEYS:
        assert key not in cfg["datarefs"]
        assert key not in cfg.get("dataref_writes", {})
