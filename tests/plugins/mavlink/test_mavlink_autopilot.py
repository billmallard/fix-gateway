"""ArduPlane as the autopilot source (AFC7, AER-5822), on the real database."""
import importlib
import logging
import os
from types import SimpleNamespace

import pytest
from pymavlink import mavutil

import fixgw.database as fixdatabase
from fixgw.plugins.mavlink import autopilot as ap

mav_module = importlib.import_module("fixgw.plugins.mavlink.Mav")

# Recorded from ArduPlane 4.7.2 SITL through the real plugin in the real
# server: the autopilot's own heartbeats, as a tlog, with an 8 s link loss.
# tools/mavlink_sitl/README.md has the session and the commands.
TLOG = os.path.join(os.path.dirname(__file__), "data", "arduplane_4.7.2_modes.tlog")
# The same, from tools/mavlink_sitl/reengage.py (AFC7a, AER-5897): three
# times the plugin engages GUIDED and the transmitter takes MANUAL, then the
# plugin's own GUIDED with no switch.
REENGAGE_TLOG = os.path.join(os.path.dirname(__file__), "data",
                             "arduplane_4.7.2_reengage.tlog")

STATE = ("APENG", "FDENG", "YDENG", "APLAT", "APLATARM", "APVERT",
         "APVERTARM", "APALTCAP", "APCAPS", "APMSG", "APDISC")
# Every GA request with no ArduPlane equivalent.
# The latest a state key may go old after the last heartbeat: 1.5 s of
# holding (one missed 1 Hz heartbeat), then the keys' own 2 s tol. Literals,
# so a longer hold in the adapter fails here rather than moving the bound.
HOLD = 1.5
OLD_BY = HOLD + 2.0
NOT_ARDUPLANE = ("FD", "YD", "LVL", "HDG", "TRK", "APR", "IAS", "VNAV", "VS",
                 "ALT")


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class Parent:
    """The plugin's database API on the real fixgw database."""

    def __init__(self, leader=True):
        self.log = logging.getLogger("test.mavlink")
        self.quorum = SimpleNamespace(leader=leader)
        self.writes = []

    def db_read(self, key):
        return fixdatabase.read(key)

    def db_write(self, key, value):
        self.writes.append((key, value))
        fixdatabase.write(key, value)

    def db_get_item(self, key):
        return fixdatabase.get_raw_item(key)

    def db_callback_add(self, key, function, udata=None):
        fixdatabase.callback_add("test", key, function, udata)

    def db_callback_del(self, key, function, udata=None):
        fixdatabase.callback_del("test", key, function, udata)

    def written(self, key):
        return [v for k, v in self.writes if k == key]


class Conn:
    def __init__(self):
        self.sent = []
        self.target_system, self.target_component = 1, 1
        self.mav = SimpleNamespace(
            command_long_encode=lambda *a: ("long", a),
            command_int_encode=lambda *a: ("int", a),
            send=self.sent.append,
        )


def make(database, leader=True, apstat="ARMED"):
    """An adapter and a Mav sharing one parent, as the plugin thread has."""
    clock = Clock()
    parent = Parent(leader)
    adapter = ap.Autopilot(parent, clock=clock)
    mav = mav_module.Mav.__new__(mav_module.Mav)
    mav.parent = parent
    mav.autopilot = adapter
    mav.conn = Conn()
    mav._apreq = mav._apmode = "TRIM"
    mav._apstat = apstat
    mav._apwpv = True
    mav._waypoint = ""
    mav._apmodes = {"TRIM": 0, "CRUISE": 7, "AUTOTUNE": 8, "AUTO": 10,
                    "GUIDED": 15}
    database.write("WPLAT", 34.44)
    database.write("WPLON", -119.92)
    database.write("WPNAME", "WPT1")
    # The MSL altitude a reposition holds (AER-6351).
    database.write("TALT", 1500.0)
    return adapter, mav, parent, clock


def read(key):
    v = fixdatabase.read(key)
    return v[0], v[2]       # value, old


def press(database, request):
    database.write("APCMD", request)
    database.write("APCMDSEQ", database.read("APCMDSEQ")[0] + 1)


def sent_modes(mav):
    """The custom modes Mav sent as DO_SET_MODE."""
    return [m[1][5] for m in mav.conn.sent
            if m[0] == "long" and m[1][2] == mavutil.mavlink.MAV_CMD_DO_SET_MODE]


# --------------------------------------------------------------------------
# The mode table
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mode,expected", [
    (0, (False, "", "")),               # MANUAL: the pilot's trims
    (16, (False, "", "")),              # INITIALISING
    (7, (True, "TRK", "ALT")),          # CRUISE
    (15, (True, "GPS", "ALT")),         # GUIDED
    (8, (True, "ROL", "PIT")),          # AUTOTUNE
    (5, (True, "", "")),                # FBWA: flying, no GA name
    (10, (True, "", "")),               # AUTO: its own mission, not ours
    (11, (True, "", "")),               # RTL
])
def test_state_for_each_arduplane_mode(mode, expected):
    assert ap.state(mode) == expected


def test_heartbeat_publishes_every_state_key_fresh(database):
    adapter, _mav, parent, _c = make(database)
    adapter.heartbeat(7)
    assert {k: read(k) for k in STATE} == {
        "APENG": (True, False), "FDENG": (False, False),
        "YDENG": (False, False), "APLAT": ("TRK", False),
        "APLATARM": ("", False), "APVERT": ("ALT", False),
        "APVERTARM": ("", False), "APALTCAP": (False, False),
        "APCAPS": ("AP,NAV,DISC", False), "APMSG": ("", False),
        "APDISC": (False, False),
    }
    # No flight director: the bars' keys are never written, so go old.
    assert not parent.written("FDPITCH") and not parent.written("FDROLL")


def test_a_mode_with_no_ga_name_is_named_on_apmsg(database):
    adapter, _m, _p, _c = make(database)
    adapter.heartbeat(5)
    assert read("APENG") == (True, False)
    assert read("APLAT") == ("", False) and read("APVERT") == ("", False)
    assert read("APMSG") == ("ArduPlane mode FBWA", False)


def test_a_mode_change_clears_an_earlier_refusal(database):
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(7)
    mav._apmode = "CRUISE"
    press(database, "VS")
    mav.checkMode()
    adapter.heartbeat(7)
    assert read("APMSG")[0] == "VS is not available on this autopilot"
    adapter.heartbeat(5)             # the transmitter's mode switch
    assert read("APMSG")[0] == "ArduPlane mode FBWA"


# --------------------------------------------------------------------------
# APCAPS: only what ArduPlane can fly
# --------------------------------------------------------------------------
def test_apcaps_is_ap_nav_disc_and_no_ga_only_mode(database):
    adapter, _m, _p, _c = make(database)
    adapter.heartbeat(0)
    caps = read("APCAPS")[0].split(",")
    assert caps == ["AP", "NAV", "DISC"]
    for request in NOT_ARDUPLANE:
        assert request not in caps


@pytest.mark.parametrize("request_", NOT_ARDUPLANE)
def test_a_ga_mode_arduplane_lacks_is_refused_and_sends_nothing(database, request_):
    adapter, mav, parent, _c = make(database)
    adapter.heartbeat(7)
    mav._apmode = "CRUISE"
    parent.writes.clear()
    press(database, request_)
    mav.checkMode()
    assert read("APMSG")[0] == "{} is not available on this autopilot".format(request_)
    assert not [k for k, _v in parent.writes if k.startswith("MAVREQ")]
    assert sent_modes(mav) == []
    assert read("APLAT")[0] == "TRK"


def test_unknown_request_is_refused(database):
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(0)
    press(database, "ROLL")
    mav.checkMode()
    assert read("APMSG")[0] == "Unknown request ROLL"
    assert sent_modes(mav) == []


# --------------------------------------------------------------------------
# Requests to MAVREQ* and on to the flight controller
# --------------------------------------------------------------------------
def test_ap_from_manual_engages_cruise_through_mavreq(database):
    adapter, mav, parent, _c = make(database)
    adapter.heartbeat(0)
    press(database, "AP")
    mav.checkMode()
    assert parent.written("MAVREQCRUISE") == [True]
    assert sent_modes(mav) == [7]
    # The request changed no state key: that waits for the heartbeat.
    assert read("APENG")[0] is False
    adapter.heartbeat(7)
    assert read("APENG")[0] is True and read("APLAT")[0] == "TRK"


def test_ap_while_engaged_and_disc_go_to_manual(database):
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(7)
    mav._apmode = "CRUISE"
    press(database, "AP")
    mav.checkMode()
    assert sent_modes(mav) == [0]

    adapter.heartbeat(7)          # the change has not landed yet
    mav._apmode = "CRUISE"
    mav.conn.sent.clear()
    press(database, "DISC")
    mav.checkMode()
    assert sent_modes(mav) == [0]


def test_nav_engaged_with_a_waypoint_flies_guided_and_again_cruise(database):
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(7)
    mav._apmode = "CRUISE"
    press(database, "NAV")
    mav.checkMode()
    sent = mav.conn.sent
    assert sent[-1][0] == "int"
    assert sent[-1][1][3] == mavutil.mavlink.MAV_CMD_DO_REPOSITION
    lat, lon = sent[-1][1][10:12]
    assert abs(lat - 344400000) <= 1 and abs(lon + 1199200000) <= 1

    adapter.heartbeat(15)
    assert (read("APLAT")[0], read("APVERT")[0]) == ("GPS", "ALT")
    press(database, "NAV")
    mav.checkMode()
    assert sent_modes(mav) == [7]


def test_nav_with_the_autopilot_off_is_refused(database):
    # No FD to arm, so NAV would couple the servos by itself.
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(0)
    press(database, "NAV")
    mav.checkMode()
    assert read("APMSG")[0] == "NAV refused: engage AP first"
    assert mav.conn.sent == []


def test_nav_with_no_waypoint_is_refused(database):
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(7)
    mav._apmode = "CRUISE"
    database.write("WPLAT", 0.0)      # checkWaypoint: no valid waypoint
    press(database, "NAV")
    mav.checkMode()
    assert read("APMSG")[0] == "NAV refused: no active waypoint"
    assert mav.conn.sent == []


def test_not_armed_is_said_not_dropped(database):
    adapter, mav, _p, _c = make(database, apstat="ERROR")
    adapter.heartbeat(0)
    press(database, "AP")
    mav.checkMode()
    assert read("APMSG")[0] == "AP refused: autopilot status ERROR"
    assert mav.conn.sent == []


def test_a_transmitter_mode_change_does_not_hide_the_next_request(database):
    # Mav remembers the mode it last set (CRUISE). The pilot's transmitter
    # then went MANUAL. AP must still send CRUISE, which checkMode skips
    # when it believes CRUISE is current.
    adapter, mav, _p, _c = make(database)
    mav._apmode = "CRUISE"
    database.write("MAVREQCRUISE", True)
    adapter.heartbeat(0)
    press(database, "AP")
    mav.checkMode()
    assert sent_modes(mav) == [7]


def test_only_the_quorum_leader_turns_requests_into_mavreq(database):
    adapter, mav, parent, _c = make(database, leader=False)
    adapter.heartbeat(0)
    press(database, "AP")
    mav.checkMode()
    assert not [k for k, _v in parent.writes if k.startswith("MAVREQ")]


def test_the_counter_at_start_is_a_baseline_not_a_request(database):
    database.write("APCMD", "AP")
    database.write("APCMDSEQ", 41)
    adapter, mav, parent, _c = make(database)
    adapter.heartbeat(0)
    mav.checkMode()
    database.write("APCMDSEQ", 41)       # a client re-sending the same count
    mav.checkMode()
    assert mav.conn.sent == []
    database.write("APCMDSEQ", 0)        # the gateway restarted: a new request
    mav.checkMode()
    assert sent_modes(mav) == [7]


def test_request_with_no_heartbeat_is_refused(database):
    adapter, mav, _p, clock = make(database)
    adapter.heartbeat(0)
    clock.t += ap.HOLD_S + 0.1
    press(database, "AP")
    mav.checkMode()
    assert adapter.msg == "AP refused: no heartbeat from the autopilot"
    assert mav.conn.sent == []


# --------------------------------------------------------------------------
# The disconnect alert
# --------------------------------------------------------------------------
def test_any_disconnect_latches_apdisc_until_disc(database):
    adapter, mav, _p, _c = make(database)
    adapter.heartbeat(7)
    adapter.heartbeat(0)             # the transmitter, a failsafe, anything
    assert read("APDISC")[0] is True
    adapter.heartbeat(0)
    assert read("APDISC")[0] is True
    press(database, "DISC")
    mav.checkMode()
    assert read("APDISC")[0] is False
    assert mav.conn.sent == []       # an acknowledgement, not a command


# --------------------------------------------------------------------------
# Stale is removed, not frozen
# --------------------------------------------------------------------------
def test_state_is_refreshed_only_while_the_heartbeat_is_fresh(database):
    adapter, _m, parent, clock = make(database)
    adapter.heartbeat(7)
    parent.writes.clear()
    clock.t += ap.REFRESH_S
    adapter.refresh()
    assert len(parent.writes) == len(STATE)
    parent.writes.clear()
    clock.t += ap.HOLD_S - ap.REFRESH_S + 0.01
    adapter.refresh()
    assert parent.writes == []


def test_link_loss_takes_the_state_old_within_tol(database, monkeypatch):
    # One clock for the adapter and the database's item ages.
    now = [5000.0]
    monkeypatch.setattr(fixdatabase.time, "time", lambda: now[0])
    adapter, _m, _p, clock = make(database)
    clock.t = now[0]

    def advance(dt):
        now[0] += dt
        clock.t = now[0]
        adapter.refresh()

    adapter.heartbeat(7)
    # Heartbeats stop. The plugin loop keeps calling refresh().
    last_hb = now[0]
    while now[0] - last_hb < ap.HOLD_S:
        advance(0.1)
    last_write = adapter.last_write
    assert last_write - last_hb <= HOLD
    for key in STATE:
        item = fixdatabase.get_raw_item(key)
        assert item.tol == 2000
        assert read(key)[1] is False
    advance(item.tol / 1000.0 - (now[0] - last_write) + 0.01)
    for key in STATE:
        assert read(key)[1] is True, key
    # Old within tol of the last write, which is at most HOLD_S after the
    # last heartbeat.
    assert now[0] - last_hb <= OLD_BY + 0.11


def test_without_the_autopilot_keys_the_adapter_stays_off(caplog):
    parent = SimpleNamespace(log=logging.getLogger("test.mavlink"))

    def get(key):
        raise KeyError(key)

    parent.db_get_item = get
    parent.db_callback_add = lambda *a: pytest.fail("subscribed")
    with caplog.at_level(logging.ERROR):
        adapter = ap.Autopilot(parent)
    assert adapter.enabled is False
    assert "autopilot adapter off" in caplog.text
    adapter.heartbeat(7)
    adapter.refresh()
    assert adapter.poll(None) is False


# --------------------------------------------------------------------------
# Recorded ArduPlane traffic
# --------------------------------------------------------------------------
# What each recorded mode must produce: (MAVMODE, APENG, APLAT, APVERT).
RECORDED = {
    "INITIALISING": ("UNKNOWN", False, "", ""),
    "MANUAL": ("TRIM", False, "", ""),
    "CRUISE": ("CRUISE", True, "TRK", "ALT"),
    "GUIDED": ("GUIDED", True, "GPS", "ALT"),
    "AUTOTUNE": ("AUTOTUNE", True, "ROL", "PIT"),
    "TAKEOFF": ("UNKNOWN", True, "", ""),
    "FBWA": ("UNKNOWN", True, "", ""),
}


class ReplayConn(Conn):
    """The recorded heartbeats, through pymavlink's own tlog reader, on the
    recording's clock. Between two records it returns None every 0.1 s, as
    the plugin's 0.1 s recv timeout does on a quiet link."""

    def __init__(self, clocks, path=TLOG):
        super().__init__()
        self.clocks = clocks
        log = mavutil.mavlink_connection(path)
        self.records = []
        while True:
            m = log.recv_match()
            if m is None:
                break
            self.records.append(m)
        log.close()
        self.i = 0

    def _set(self, t):
        for c in self.clocks:
            c[0] = t

    def recv_match(self, **kwargs):
        if kwargs.get("type") == "COMMAND_ACK" or self.i >= len(self.records):
            return None
        m = self.records[self.i]
        now = self.clocks[0][0]
        if m._timestamp <= now + 0.1:
            self.i += 1
            self._set(max(now, m._timestamp))
            return m
        self._set(now + 0.1)
        return None


def replay(database, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(fixdatabase.time, "time", lambda: now[0])
    monkeypatch.setattr(mav_module.time, "monotonic", lambda: now[0])
    adapter, mav, parent, _clock = make(database)
    adapter.clock = lambda: now[0]
    mav.conn = ReplayConn([now])
    now[0] = mav.conn.records[0]._timestamp - 0.05
    mav._link_ok = False
    mav._last_heartbeat = now[0]
    mav.ids, mav.no_msg_count, mav._interval = [], 0, {}
    mav._ahrs = mav._gps = mav._airspeed = mav._groundspeed = False
    mav._accel = mav._pressure = False
    ticks = []
    while mav.conn.i < len(mav.conn.records):
        before = mav.conn.i
        mav.process()
        hb = mav.conn.records[before] if mav.conn.i > before else None
        keys = STATE + ("MAVMODE", "MAVMSG")
        ticks.append((now[0], hb, {k: read(k) for k in keys}))
    return ticks


def test_recorded_traffic_maps_every_mode(database, monkeypatch):
    ticks = replay(database, monkeypatch)
    seen = set()
    for _t, hb, keys in ticks:
        if hb is None:
            continue
        name = mavutil.mode_mapping_apm[hb.custom_mode]
        mavmode, apeng, lat, vert = RECORDED[name]
        assert keys["MAVMODE"][0] == mavmode, name       # the buttons' key
        assert keys["APENG"] == (apeng, False), name
        assert keys["APLAT"] == (lat, False), name
        assert keys["APVERT"] == (vert, False), name
        assert keys["APCAPS"] == ("AP,NAV,DISC", False)
        if lat == "" and apeng:
            assert keys["APMSG"][0] == "ArduPlane mode " + name
        seen.add(name)
    assert seen == set(RECORDED)


def test_recorded_disconnects_latch_apdisc(database, monkeypatch):
    ticks = replay(database, monkeypatch)
    prev = None
    disconnects = 0
    for _t, hb, keys in ticks:
        if hb is None:
            continue
        eng = keys["APENG"][0]
        if prev and not eng:
            disconnects += 1
            assert keys["APDISC"][0] is True
        prev = eng
    assert disconnects >= 2     # the head's AP, and the transmitter's switch


def test_recorded_link_loss_takes_the_state_old_within_tol(database, monkeypatch):
    ticks = replay(database, monkeypatch)
    beats = [t for t, hb, _k in ticks if hb is not None]
    gaps = [(a, b) for a, b in zip(beats, beats[1:]) if b - a > 4.0]
    assert gaps, "the recording has a link loss"
    last, back = gaps[0]
    during = [(t, k) for t, hb, k in ticks if last < t < back]
    for key in STATE:
        olds = [t for t, k in during if k[key][1]]
        assert olds, key
        assert olds[0] - last <= OLD_BY + 0.15, key
        # and stays old: never frozen fresh again until the link is back
        assert all(k[key][1] for t, k in during if t >= olds[0]), key
    # CERT2d: checkLink runs before each receive, so in replay MAVMSG goes
    # old up to two 0.1 s ticks after the timeout.
    olds = [t for t, k in during if k["MAVMSG"][1]]
    assert olds and olds[0] - last <= mav_module.LINK_TIMEOUT + 0.25
    after = [k for t, hb, k in ticks if hb is not None and t >= back][0]
    assert after["APENG"][1] is False and after["MAVMSG"][1] is False


# --------------------------------------------------------------------------
# AFC7a: a mode change the plugin did not make is never undone (AER-5897)
# --------------------------------------------------------------------------
# Mav's name for each ArduPlane mode in the recording; MAVMODE shows it.
MAVMODE_OF = {"MANUAL": "TRIM", "CRUISE": "CRUISE", "GUIDED": "GUIDED"}
NEXT_WP = 34.45


def repositions(sent):
    """(change_mode flag, lat) of each DO_REPOSITION in ``sent``."""
    return [(m[1][7], m[1][10]) for m in sent
            if m[0] == "int" and m[1][3] == mavutil.mavlink.MAV_CMD_DO_REPOSITION]


def test_recorded_transmitter_takeover_is_never_undone(database, monkeypatch):
    """ArduPlane's own heartbeats from reengage.py, through the plugin loop
    (process, then checkMode, every tick). The head presses AP and NAV where
    the recording shows the plugin's own mode changes. After each GUIDED to
    MANUAL, which the transmitter made, the flight plan moves to the next
    waypoint and then is cleared: the plugin must send nothing. On dev it
    sent DO_REPOSITION with the mode-change flag (GUIDED again) and then
    DO_SET_MODE CRUISE."""
    now = [0.0]
    monkeypatch.setattr(fixdatabase.time, "time", lambda: now[0])
    monkeypatch.setattr(mav_module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mav_module.time, "sleep", lambda _s: None)
    adapter, mav, parent, _clock = make(database)
    adapter.clock = lambda: now[0]
    conn = ReplayConn([now], REENGAGE_TLOG)
    mav.conn = conn
    names = [mavutil.mode_mapping_apm[m.custom_mode] for m in conn.records]
    assert "GUIDED" in names and "MANUAL" in names
    now[0] = conn.records[0]._timestamp - 0.05
    mav._link_ok = False
    mav._last_heartbeat = now[0]
    mav.ids, mav.no_msg_count, mav._interval = [], 0, {}
    mav._ahrs = mav._gps = mav._airspeed = mav._groundspeed = False
    mav._accel = mav._pressure = False
    mav._apreq = mav._apmode = "INIT"

    def plan(lat):
        database.write("WPLAT", lat)
        database.write("WPLON", -119.92)
        database.write("WPNAME", "WPT1")
        database.write("TALT", 1500.0)  # fresh on the replay clock

    reported = None         # the last replayed heartbeat's mode
    pressed_for = -1        # the record index a press was made for
    takeovers = 0
    takeover = None         # (record index, sent count) of a MANUAL takeover
    guided_since = None
    retargets = []
    presses = []
    moves = 0
    while conn.i < len(conn.records):
        before = conn.i
        mav.process()
        if conn.i > before:
            prev, reported = reported, names[before]
            if reported == "MANUAL" and prev == "GUIDED":
                takeovers += 1
                takeover = (before, len(conn.sent), now[0])
            guided_since = now[0] if reported == "GUIDED" and prev != "GUIDED" \
                else guided_since
        nxt = conn.i
        nxt_name = names[nxt] if nxt < len(names) else None
        # The head's own presses, just before the recorded change they made.
        if (nxt_name not in (None, reported) and pressed_for != nxt
                and conn.records[nxt]._timestamp - now[0] < 0.15
                and (reported, nxt_name) in (("MANUAL", "CRUISE"),
                                             ("CRUISE", "GUIDED"))):
            pressed_for = nxt
            takeover = None
            plan(34.44)
            presses.append("AP" if nxt_name == "CRUISE" else "NAV")
            press(database, presses[-1])
        if takeover is not None:
            # The pilot has it: the flight plan moves on, then is cleared.
            t = now[0] - takeover[2]
            if 0.5 <= t < 0.6:
                plan(NEXT_WP)
                moves += 1
            elif 2.0 <= t < 2.1:
                plan(0.0)
                moves += 1
            assert conn.sent[takeover[1]:] == [], (
                "the plugin re-commanded ArduPlane after the transmitter "
                "took MANUAL", conn.sent[takeover[1]:])
        if (reported == "GUIDED" and guided_since is not None
                and now[0] - guided_since > 1.0 and not retargets):
            # The plugin's own GUIDED, no switch: it follows the plan.
            n = len(conn.sent)
            plan(NEXT_WP)
            mav.checkMode()
            retargets = repositions(conn.sent[n:])
        mav.checkMode()
        if reported is not None:
            # MAVMODE is what ArduPlane reports, never a request in flight.
            assert read("MAVMODE")[0] == MAVMODE_OF.get(reported, "UNKNOWN"), (
                now[0], reported)
    # NAV for A; AP and NAV for B, C and D. Each takeover (A, B, C and E)
    # saw the plan move and then clear.
    assert presses == ["NAV"] + ["AP", "NAV"] * 3
    assert takeovers == 4 and moves == 2 * takeovers
    # A new waypoint moves the plugin's own GUIDED, without the mode flag.
    assert retargets == [(0, int(NEXT_WP * 10**7))]
    # NAV itself still asks for GUIDED.
    assert (1, int(34.44 * 10**7)) in repositions(conn.sent)
