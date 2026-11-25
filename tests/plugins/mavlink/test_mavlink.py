from types import SimpleNamespace
import importlib
import math

import pytest

import fixgw.plugin as plugin_base
import fixgw.plugins.mavlink as mav_plugin

mav_module = importlib.import_module("fixgw.plugins.mavlink.Mav")


class FakeLog:
    def __init__(self):
        self.messages = []

    def debug(self, message):
        self.messages.append(message)


class FakeParent:
    def __init__(self):
        self.log = FakeLog()
        self.quorum = SimpleNamespace(leader=True)
        self.writes = []
        self.values = {
            "MAVREQADJ": (False, False, False, False, False, False),
            "MAVADJ": (False, False, False, False, False, False),
            "TRIMP": (1.0, False, False, False, False, False),
            "TRIMR": (2.0, False, False, False, False, False),
            "TRIMY": (3.0, False, False, False, False, False),
            "MAVREQTRIM": (False, False, False, False, False, False),
            "MAVREQCRUISE": (False, False, False, False, False, False),
            "MAVREQAUTOTUNE": (False, False, False, False, False, False),
            "MAVREQAUTO": (False, False, False, False, False, False),
            "MAVREQGUIDED": (False, False, False, False, False, False),
            "WPLAT": (40.0, False, False, False, False, False),
            "WPLON": (-88.0, False, False, False, False, False),
            "WPNAME": ("HOME", False, False, False, False, False),
            "ALT": (1000.0, False, False, False, False, False),
            "TALT": (1200.0, False, False, False, False, False),
        }

    def db_write(self, key, value):
        self.writes.append((key, value))
        self.values[key] = (value, False, False, False, False, False)

    def db_read(self, key):
        return self.values.get(key, (0, False, False, False, False, False))

    def db_get_item(self, key):
        return FakeItem(self, key)

    def db_callback_add(self, key, function, udata=None):
        self.callbacks = getattr(self, "callbacks", [])
        self.callbacks.append((key, function))

    def db_callback_del(self, key, function, udata=None):
        self.callbacks.remove((key, function))


class FakeItem:
    def __init__(self, parent, key):
        self.parent = parent
        self.key = key

    min = None
    max = None

    @property
    def written(self):
        return self.key in self.parent.values

    @property
    def value(self):
        return self.parent.db_read(self.key)

    def set(self, value, old=None, bad=None):
        self.parent.db_write(self.key, value)


class FakeMavSender:
    def __init__(self):
        self.sent = []
        self.manual_controls = []
        self.long_commands = []
        self.int_commands = []

    def command_long_encode(self, *args):
        self.long_commands.append(args)
        return ("long", args)

    def command_int_encode(self, *args):
        self.int_commands.append(args)
        return ("int", args)

    def send(self, message):
        self.sent.append(message)

    def manual_control_send(self, *args):
        self.manual_controls.append(args)


class FakeConn:
    def __init__(self, messages=None, ack=None):
        self.messages = list(messages or [])
        self.ack = ack
        self.mav = FakeMavSender()
        self.target_system = 1
        self.target_component = 2
        self.closed = False
        self.heartbeat_waited = False

    def recv_match(self, **kwargs):
        if kwargs.get("type") == "COMMAND_ACK":
            return self.ack
        if self.messages:
            value = self.messages.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        return None

    def wait_heartbeat(self):
        self.heartbeat_waited = True

    def close(self):
        self.closed = True


class Msg(SimpleNamespace):
    def __init__(self, msg_type, **kwargs):
        super().__init__(**kwargs)
        self._msg_type = msg_type

    def get_type(self):
        return self._msg_type


def make_mav(messages=None, parent=None, options=None):
    mav = mav_module.Mav.__new__(mav_module.Mav)
    mav.parent = parent or FakeParent()
    mav.autopilot = None
    mav._apreq = "INIT"
    mav._apstat = "TRIM"
    mav._apmode = "INIT"
    mav._apwpv = False
    mav._apmodes = {
        "TRIM": 0,
        "CRUISE": 7,
        "AUTOTUNE": 8,
        "AUTO": 10,
        "GUIDED": 15,
    }
    options = options or {
        "airspeed": True,
        "groundspeed": True,
        "gps": True,
        "ahrs": True,
        "accel": True,
        "pressure": True,
    }
    mav._airspeed = options.get("airspeed", False)
    mav._groundspeed = options.get("groundspeed", False)
    mav._gps = options.get("gps", False)
    mav._ahrs = options.get("ahrs", False)
    mav._accel = options.get("accel", False)
    mav._pressure = options.get("pressure", False)
    mav._pascal_offset = options.get("pascal_offset", 0)
    mav._min_airspeed = options.get("min_airspeed", 10)
    mav._outputPitch = 0
    mav._outputRoll = 0
    mav._outputYaw = 0
    mav._apAdjust = False
    mav._trimsSaved = False
    mav._trimsSavedRoll = 0
    mav._trimsSavedPitch = 0
    mav._trimsSavedYaw = 0
    mav._waypoint = str
    mav._link_ok = False
    mav._last_heartbeat = 0.0
    mav._interval = mav_module.defaultdict(lambda: 200000)
    mav._data = mav_module.defaultdict(list)
    mav._max_average = 15
    mav.ids = [101, 202]
    mav.conn = FakeConn(messages)
    mav.no_msg_count = 0
    return mav


def assert_write(parent, key, value):
    assert (key, value) in parent.writes


def test_constructor_requests_configured_ids_and_initializes(monkeypatch):
    ack = SimpleNamespace(
        command=mav_module.mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL,
        result=mav_module.mavutil.mavlink.MAV_RESULT_ACCEPTED,
    )
    conn = FakeConn(ack=ack)
    monkeypatch.setattr(mav_module, "stat", lambda _port: True)
    monkeypatch.setattr(mav_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(mav_module.mavutil, "mavlink_connection", lambda *_a, **_k: conn)
    parent = FakeParent()

    mav = mav_module.Mav(
        parent,
        port="/dev/fake",
        baud=115200,
        options={
            "airspeed": True,
            "groundspeed": True,
            "gps": True,
            "ahrs": True,
            "accel": True,
            "pressure": True,
            "pascal_offset": 12,
            "min_airspeed": 20,
        },
    )

    assert mav.conn is conn
    assert conn.mav.sent
    # No data key is written before a message carries it (SRC3, AER-6542).
    assert not {key for key, _value in parent.writes} & set(INIT_KEYS)


def test_constructor_raises_when_port_missing(monkeypatch):
    parent = FakeParent()
    monkeypatch.setattr(mav_module, "stat", lambda _port: False)

    with pytest.raises(Exception, match="serial port /dev/missing is not found"):
        mav_module.Mav(parent, port="/dev/missing")

    assert_write(parent, "MAVSTATE", "ERROR")


def test_constructor_raises_when_second_port_check_fails(monkeypatch):
    parent = FakeParent()
    checks = [True, False]
    monkeypatch.setattr(mav_module, "stat", lambda _port: checks.pop(0))
    monkeypatch.setattr(mav_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(Exception, match="serial port /dev/flaky is not found"):
        mav_module.Mav(parent, port="/dev/flaky")

    assert_write(parent, "MAVSTATE", "ERROR")


def test_request_ids_records_failed_ack_and_helpers_close_heartbeat(caplog):
    mav = make_mav()
    mav.conn.ack = SimpleNamespace(command=999, result=999)

    mav.request_ids()
    mav.wait_heartbeat()
    mav.close()

    assert len(mav.conn.mav.sent) == 2
    assert mav.conn.heartbeat_waited is True
    assert mav.conn.closed is True


def test_process_no_message_re_requests_ids_after_threshold(monkeypatch):
    mav = make_mav(messages=[None])
    calls = []
    mav.no_msg_count = 15
    mav.request_ids = lambda: calls.append("request")

    mav.process()

    assert calls == ["request"]
    assert mav.no_msg_count == 0


def test_process_vfr_hud_writes_speed_and_vertical_speed_paths():
    parent = FakeParent()
    mav = make_mav(
        parent=parent,
        messages=[
            Msg("VFR_HUD", airspeed=20.0, groundspeed=4.0, climb=1.25),
            Msg("VFR_HUD", airspeed=1.0, groundspeed=1.0, climb=0.0),
        ],
    )

    mav.process()
    mav.process()

    assert_write(parent, "IAS", 38.88)
    assert_write(parent, "GS", 7.78)
    assert_write(parent, "TAS", 38.88)
    assert_write(parent, "VS", 246)

    low_parent = FakeParent()
    low_mav = make_mav(
        parent=low_parent,
        messages=[Msg("VFR_HUD", airspeed=1.0, groundspeed=1.0, climb=0.0)],
    )
    low_mav.process()

    assert_write(low_parent, "IAS", 0)
    assert_write(low_parent, "GS", 0)
    assert_write(low_parent, "TAS", 0)


def test_process_sensor_attitude_gps_position_pressure_and_servo_messages(monkeypatch):
    parent = FakeParent()
    parent.values["BARO"] = (29.92126, False, False, False, False, False)
    monkeypatch.setattr(mav_module, "rot", lambda speed, roll: ("rot", speed, roll))
    mav = make_mav(
        parent=parent,
        messages=[
            Msg("VFR_HUD", airspeed=20.0, groundspeed=5.0, climb=0.0),
            Msg("SCALED_IMU", xacc=1111, yacc=-2222, zacc=3333),
            Msg("ATTITUDE", roll=0.5, pitch=-0.25, yaw=1.0),
            Msg(
                "GPS_RAW_INT",
                cog=12345,
                fix_type=3,
                alt_ellipsoid=304800,
                satellites_visible=8,
                vel_acc=2000,
                h_acc=3048,
                v_acc=6096,
            ),
            Msg("GLOBAL_POSITION_INT", hdg=9876, relative_alt=15240, alt=304800, lat=400000000, lon=-880000000),
            Msg("SCALED_PRESSURE", press_abs=1013.25, press_diff=1.234),
            Msg("SERVO_OUTPUT_RAW", servo1_raw=1600, servo2_raw=1400, servo4_raw=1504),
        ],
    )

    for _ in range(7):
        mav.process()

    assert_write(parent, "ALAT", -2.222)
    assert_write(parent, "ALONG", 1.111)
    assert_write(parent, "ANORM", 3.333)
    assert_write(parent, "ROLL", 28.65)
    assert_write(parent, "ROT", ("rot", 20.0, 28.65))
    assert_write(parent, "PITCH", -14.32)
    assert_write(parent, "YAW", 57.3)
    assert_write(parent, "TRACK", 123.45)
    assert_write(parent, "GPS_FIX_TYPE", 3)
    assert_write(parent, "GPS_ELLIPSOID_ALT", 1000.0)
    assert_write(parent, "GPS_SATS_VISIBLE", 8)
    assert_write(parent, "GPS_ACCURACY_SPEED", 3.89)
    assert_write(parent, "GPS_ACCURACY_HORIZ", 10.0)
    assert_write(parent, "GPS_ACCURACY_VERTICAL", 20.0)
    assert_write(parent, "HEAD", 98.76)
    assert_write(parent, "AGL", 50.0)
    # ALT is from the pressure, sea-level standard at the standard setting;
    # TALT is the GPS MSL height (AER-6351).
    assert_write(parent, "ALT", 0.0)
    assert ("ALT", 1000.0) not in parent.writes
    assert_write(parent, "TALT", 1000.0)
    assert_write(parent, "LAT", 40.0)
    assert_write(parent, "LONG", -88.0)
    assert_write(parent, "AIRPRESS", 101325.0)
    assert_write(parent, "DIFFAIRPRESS", 123.4)
    assert mav._outputRoll == 250
    assert mav._outputPitch == -250
    assert mav._outputYaw == 10


def test_rot_is_the_turn_rate_for_the_bank_in_degrees():
    # pymavlink 2.4.42 rate_of_turn(speed, bank) takes m/s and degrees. Fed
    # the roll in radians it read about 57x low (SRC1, AER-6405). At 30 deg
    # and 100 kt the coordinated rate is g tan(bank) / V, about 6.3 deg/s.
    parent = FakeParent()
    tas_ms = 100 / 1.9438445
    bank = math.radians(30)
    mav = make_mav(
        parent=parent,
        messages=[Msg("VFR_HUD", airspeed=tas_ms, groundspeed=tas_ms, climb=0.0)]
        + [Msg("ATTITUDE", roll=bank, pitch=0.0, yaw=0.0)] * 5,
    )

    for _ in range(6):
        mav.process()

    expected = math.degrees(9.80665 * math.tan(bank) / tas_ms)
    rots = [v for k, v in parent.writes if k == "ROT"]
    assert len(rots) == 5
    for value in rots:
        assert value == pytest.approx(expected, rel=0.01)


def test_head_averages_across_north_not_to_the_reciprocal():
    # HEAD is a running mean of up to 15 samples. An arithmetic mean of 359
    # and 1 is 180; on the circle it is 0 (SRC1, AER-6405).
    parent = FakeParent()
    mav = make_mav(
        parent=parent,
        messages=[
            Msg("GLOBAL_POSITION_INT", hdg=hdg, relative_alt=0, alt=0, lat=0, lon=0)
            for hdg in [35900, 100] * 10
        ],
    )

    for _ in range(20):
        mav.process()

    heads = [v for k, v in parent.writes if k == "HEAD"]
    assert len(heads) == 20
    for value in heads:
        assert 0.0 <= value < 360.0
        assert min(value, 360.0 - value) <= 1.0


def test_head_mean_stays_inside_the_key_range():
    # A mean a hair under 360 rounds to 360.0, which is north again: 0.0.
    parent = FakeParent()
    mav = make_mav(
        parent=parent,
        messages=[
            Msg("GLOBAL_POSITION_INT", hdg=hdg, relative_alt=0, alt=0, lat=0, lon=0)
            for hdg in (35999, 0, 0)
        ],
    )

    for _ in range(3):
        mav.process()

    heads = [v for k, v in parent.writes if k == "HEAD"]
    assert heads[0] == 359.99
    assert heads[-1] == 0.0


def _gps_raw(cog):
    return Msg("GPS_RAW_INT", cog=cog, fix_type=0, alt_ellipsoid=0, satellites_visible=0, vel_acc=0, h_acc=0, v_acc=0)


def test_process_gps_cog_is_track_not_course():
    # cog is ground track; COURSE is compute's NAVSRC select output (AER-4475).
    parent = FakeParent()
    mav = make_mav(parent=parent, messages=[_gps_raw(9000)])
    mav._data["GS"].append(60.0)

    mav.process()

    assert_write(parent, "TRACK", 90.0)
    assert "COURSE" not in [k for k, _ in parent.writes]


@pytest.mark.parametrize("gs,cog", [(0.0, 9000), (60.0, 65535)])
def test_process_gps_no_track_when_stationary_or_cog_unknown(gs, cog):
    # No ground track below 3 kt or with cog = UINT16_MAX. The old code put the
    # magnetic heading into COURSE here; nothing substitutes for TRACK now.
    parent = FakeParent()
    mav = make_mav(parent=parent, messages=[_gps_raw(cog)])
    mav._data["GS"].append(gs)
    mav._data["HEAD"].append(271.0)

    mav.process()

    written = [k for k, _ in parent.writes]
    assert "TRACK" not in written
    assert "COURSE" not in written


@pytest.mark.parametrize(
    "mode,name,message",
    [
        (0, "TRIM", "Trim Mode"),
        (7, "CRUISE", "Heading Hold"),
        (8, "AUTOTUNE", "Auto Tune"),
        (15, "GUIDED", "Nav to: HOME"),
    ],
)
def test_process_heartbeat_sets_known_modes(mode, name, message):
    parent = FakeParent()
    flags = (
        mav_module.mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_MANUAL_INPUT_ENABLED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_HIL_ENABLED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_STABILIZE_ENABLED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_GUIDED_ENABLED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_AUTO_ENABLED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_TEST_ENABLED
        | mav_module.mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
    )
    mav = make_mav(parent=parent, messages=[Msg("HEARTBEAT", type=1, custom_mode=mode, base_mode=flags)])

    mav.process()

    assert_write(parent, "MAVSTATE", "ARMED")
    assert_write(parent, "MAVMODE", name)
    assert_write(parent, "MAVMSG", message)


def test_process_heartbeat_unknown_mode_sets_error_and_ignores_other_vehicle_type():
    parent = FakeParent()
    mav = make_mav(
        parent=parent,
        messages=[
            Msg("HEARTBEAT", type=1, custom_mode=999, base_mode=0),
            Msg("HEARTBEAT", type=27, custom_mode=0, base_mode=0),
        ],
    )

    mav.process()
    mav.process()

    assert_write(parent, "MAVMODE", "UNKNOWN")
    assert_write(parent, "MAVSTATE", "ERROR")


class DbParent(FakeParent):
    """A parent on the real fixgw database, for the flags that live on items."""

    def __init__(self, database):
        super().__init__()
        self.database = database

    def db_write(self, key, value):
        self.writes.append((key, value))
        self.database.write(key, value)

    def db_read(self, key):
        return self.database.read(key)

    def db_get_item(self, key):
        return self.database.get_raw_item(key)


# Three missed 1 Hz heartbeats.  A literal, so the test fails on the flag
# rather than on a missing constant where the plugin has no timeout.
LINK_TIMEOUT = 3.0


def heartbeat(mode):
    return Msg("HEARTBEAT", type=1, custom_mode=mode, base_mode=0)


def link_loss_mav(database, monkeypatch, messages):
    clock = [100.0]
    monkeypatch.setattr(mav_module.time, "monotonic", lambda: clock[0])
    parent = DbParent(database)
    mav = make_mav(parent=parent, messages=messages)
    mav._last_heartbeat = clock[0]
    seen = []
    item = database.get_raw_item("MAVMSG")
    database.callback_add("test", "MAVMSG", lambda k, v, u: seen.append(v[:3]), None)
    return mav, item, clock, seen


def test_link_timeout_marks_mavmsg_old_and_next_heartbeat_clears_it(
    database, monkeypatch
):
    # AER-5320: MAVMSG has tol 0, so a link loss left "Heading Hold" on the
    # PFD as if current.
    mav, item, clock, seen = link_loss_mav(
        database, monkeypatch, [heartbeat(7), None, None, heartbeat(0)]
    )

    mav.process()
    assert item.value[0] == "Heading Hold"
    assert item.old is False

    clock[0] += LINK_TIMEOUT
    mav.process()
    assert item.old is False  # not past the timeout yet

    clock[0] += 0.1
    mav.process()
    assert item.value[0] == "Heading Hold"
    assert item.old is True

    seen.clear()
    clock[0] += 10
    mav.process()
    assert item.value[0] == "Trim Mode"
    assert item.old is False
    # One update: the new mode is never shown old, nor the old one fresh.
    assert seen == [("Trim Mode", False, False)]
    assert mav_module.LINK_TIMEOUT == LINK_TIMEOUT


def test_link_timeout_flag_survives_plugin_writes_until_a_heartbeat(
    database, monkeypatch
):
    # The plugin's own messages during the outage ("Drop to Heading Hold",
    # "Invalid Request") are not news from the autopilot.
    mav, item, clock, _ = link_loss_mav(database, monkeypatch, [heartbeat(7), None])
    mav.process()
    clock[0] += LINK_TIMEOUT + 1
    mav.process()
    assert item.old is True

    mav.parent.db_write("MAVMSG", "Drop to Heading Hold")
    assert item.value[0] == "Drop to Heading Hold"
    assert item.old is True

    # Nor is a heartbeat from another kind of system on the link.
    mav.conn.messages = [Msg("HEARTBEAT", type=27, custom_mode=0, base_mode=0)]
    mav.process()
    assert item.old is True


# The keys Mav.init() used to zero on connect; the ahrs option is what
# reads each.
INIT_KEYS = ("ROLL", "PITCH", "HEAD", "AGL", "ALT", "VS")
DATA_OFF = {
    "airspeed": False,
    "groundspeed": False,
    "gps": False,
    "ahrs": False,
    "accel": False,
    "pressure": False,
}


def connect_mav(database, monkeypatch, options):
    """The real constructor, as on every connect, against a fake link."""
    monkeypatch.setattr(mav_module, "stat", lambda _port: True)
    monkeypatch.setattr(mav_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        mav_module.mavutil, "mavlink_connection", lambda *_a, **_k: FakeConn()
    )
    return mav_module.Mav(DbParent(database), port="/dev/fake", options=options)


def test_connect_with_data_options_off_leaves_attitude_and_altitude_alone(
    database, monkeypatch
):
    # AER-6068: as the autopilot source only (AFC7), the plugin is not the
    # attitude or altitude source, and a connect must not blank another
    # source's values or become a second writer of its keys.
    for n, key in enumerate(INIT_KEYS, start=1):
        database.write(key, 10 * n + 0.5)
    before = {key: database.read(key) for key in INIT_KEYS}

    connect_mav(database, monkeypatch, DATA_OFF)

    assert {key: database.read(key) for key in INIT_KEYS} == before
    writers = database.writers()
    assert {key: writers.get(key, []) for key in INIT_KEYS} == {
        key: [] for key in INIT_KEYS
    }


def test_connect_with_ahrs_on_writes_nothing_until_a_real_message(
    database, monkeypatch
):
    # SRC3 (AER-6542): a zero written on connect is a wings-level, 0 ft
    # attitude that reads valid until the first real message. The values
    # from before the connect stay as they were and age old on their tol.
    for key in INIT_KEYS:
        database.write(key, 12.5)
    before = {key: database.read(key) for key in INIT_KEYS}

    mav = connect_mav(database, monkeypatch, dict(DATA_OFF, ahrs=True))

    assert {key: database.read(key) for key in INIT_KEYS} == before
    writers = database.writers()
    assert {key: writers.get(key, []) for key in INIT_KEYS} == {
        key: [] for key in INIT_KEYS
    }

    # The first real messages are what write them.
    run(
        mav,
        Msg("ATTITUDE", roll=math.radians(10.0), pitch=math.radians(-5.0), yaw=0.0),
        Msg("GLOBAL_POSITION_INT", hdg=9000, relative_alt=304800, alt=609600,
            lat=400000000, lon=-880000000),
        Msg("VFR_HUD", airspeed=0.0, groundspeed=0.0, climb=2.54),
    )
    assert database.read("ROLL")[0] == pytest.approx(10.0)
    assert database.read("PITCH")[0] == pytest.approx(-5.0)
    assert database.read("HEAD")[0] == pytest.approx(90.0)
    assert database.read("AGL")[0] == pytest.approx(1000.0)
    assert database.read("VS")[0] == 500
    writers = database.writers()
    assert {key: writers.get(key) for key in INIT_KEYS if key != "ALT"} == {
        key: ["mavlink"] for key in INIT_KEYS if key != "ALT"
    }
    # AER-6351: ALT is indicated altitude, from SCALED_PRESSURE only.
    assert writers.get("ALT", []) == []


# --------------------------------------------------------------------------
# MAVALT1: ALT is indicated altitude from the static pressure and BARO; TALT
# is the autopilot's GPS/EKF height above MSL (AER-6351)
# --------------------------------------------------------------------------
# Goldens from the ICAO standard atmosphere table, not from the plugin's
# formula: 843.07 hPa is 5000 ft pressure altitude, 696.82 hPa is 10000 ft.
ISA_5000_HPA = 843.07
ISA_10000_HPA = 696.82
MSL_FT = 6000.0  # GLOBAL_POSITION_INT.alt, well away from both


def position(alt_ft):
    return Msg("GLOBAL_POSITION_INT", hdg=0, relative_alt=0,
               alt=round(alt_ft * 304.8), lat=400000000, lon=-880000000)


def pressure(hpa):
    return Msg("SCALED_PRESSURE", press_abs=hpa, press_diff=0.0)


def airdata_mav(database, options=None, messages=()):
    return make_mav(parent=DbParent(database), messages=list(messages),
                    options=options or dict(DATA_OFF, ahrs=True, pressure=True))


def run(mav, *messages):
    mav.conn.messages.extend(messages)
    for _ in messages:
        mav.process()


def test_alt_is_indicated_from_pressure_and_baro_and_talt_is_msl(database):
    mav = airdata_mav(database)
    # The calibration offset applies to ALT as it does to AIRPRESS.
    mav._pascal_offset = 100
    run(mav, position(MSL_FT), pressure(ISA_5000_HPA - 1.0))

    alt = database.read("ALT")
    assert database.read("TALT")[0] == MSL_FT
    assert database.read("AIRPRESS")[0] == pytest.approx(84307.0)
    # BARO untouched reads its initial 29.92, about 1 ft off standard.
    assert alt[0] == pytest.approx(5000.0, abs=3.0)
    assert alt[2:5] == (False, False, False)
    # compute's PALT, ALT read back to standard, is the pressure altitude.
    assert alt[0] + mav_module.airdata.baro_offset_ft(29.92) == pytest.approx(
        5000.0, abs=1.0)

    # The pilot turns the knob 1 inHg up: the tape rises about 915 ft at the
    # same pressure (near sea level 1 inHg is about 915 ft), TALT does not.
    database.write("BARO", 30.92)
    run(mav, position(MSL_FT), pressure(ISA_5000_HPA - 1.0))
    assert 905.0 < database.read("ALT")[0] - alt[0] < 925.0
    assert database.read("TALT")[0] == MSL_FT

    # A climb moves ALT with the pressure, TALT with the GPS.
    database.write("BARO", 29.92126)
    run(mav, position(MSL_FT + 100.0), pressure(ISA_10000_HPA - 1.0))
    assert database.read("ALT")[0] == pytest.approx(10000.0, abs=3.0)
    assert database.read("TALT")[0] == MSL_FT + 100.0
    assert database.writers()["ALT"] == ["mavlink"]


def test_no_pressure_leaves_alt_unwritten(database):
    # The pressure option off, or on with no SCALED_PRESSURE yet: ALT is
    # left for its tol to age, never filled in with the GPS height.
    for options in (dict(DATA_OFF, ahrs=True),
                    dict(DATA_OFF, ahrs=True, pressure=True)):
        mav = airdata_mav(database, options)
        run(mav, position(MSL_FT))
        assert database.read("TALT")[0] == MSL_FT
        assert database.read("ALT")[0] == 0.0
        assert database.writers().get("ALT", []) == []
    # Pressure off: a SCALED_PRESSURE does not reach ALT either.
    mav = airdata_mav(database, dict(DATA_OFF, ahrs=True))
    run(mav, pressure(ISA_5000_HPA))
    assert database.writers().get("ALT", []) == []


def test_alt_without_ahrs_is_another_sources(database):
    # As the autopilot source only (AFC7, AER-6068) the plugin does not own
    # ALT, even with pressure on.
    mav = airdata_mav(database, dict(DATA_OFF, pressure=True))
    run(mav, pressure(ISA_5000_HPA))
    assert database.read("AIRPRESS")[0] == pytest.approx(84307.0)
    assert database.writers().get("ALT", []) == []


def test_alt_is_bad_while_baro_is_unusable_or_alt_out_of_range(database):
    mav = airdata_mav(database)
    database.write("BARO", 20.0)  # in BARO's range, but no real setting
    run(mav, pressure(ISA_5000_HPA))
    assert database.read("ALT")[3] is True
    database.write("BARO", 29.92)
    run(mav, pressure(ISA_5000_HPA))
    assert database.read("ALT")[3] is False
    database.get_raw_item("BARO").fail = True
    run(mav, pressure(ISA_5000_HPA))
    assert database.read("ALT")[3] is True
    database.get_raw_item("BARO").fail = False
    # A failed sensor's 0 hPa is 145,000 ft: clamped to 60,000 and bad.
    run(mav, pressure(0.0))
    assert database.read("ALT")[0] == 60000.0
    assert database.read("ALT")[3] is True


def guided_mav(database):
    mav = airdata_mav(database)
    mav._apstat = "ARMED"
    mav._apwpv = True
    database.write("WPLAT", 40.0)
    database.write("WPLON", -88.0)
    database.write("WPNAME", "HOME")
    return mav


def test_reposition_holds_the_msl_altitude_not_the_indicated(database):
    mav = guided_mav(database)
    database.write("BARO", 30.92)
    run(mav, position(MSL_FT), pressure(ISA_5000_HPA))
    assert abs(database.read("ALT")[0] - MSL_FT) > 50.0

    for change_mode in (True, False):
        mav.setMode("GUIDED", change_mode=change_mode)
        kind, args = mav.conn.mav.sent[-1]
        assert kind == "int"
        assert args[3] == mav_module.mavutil.mavlink.MAV_CMD_DO_REPOSITION
        assert args[2] == mav_module.mavutil.mavlink.MAV_FRAME_GLOBAL
        assert args[12] == pytest.approx(MSL_FT * 0.3048)


def test_reposition_without_a_usable_msl_altitude_sends_nothing(database):
    mav = guided_mav(database)
    run(mav, pressure(ISA_5000_HPA))  # ALT good, TALT never written
    mav.setMode("GUIDED")
    assert mav.conn.mav.sent == []
    assert database.read("MAVMSG")[0] == "No MSL Altitude"
    assert mav._apreq == "INIT"
    for flag in ("old", "bad", "fail"):
        run(mav, position(MSL_FT))
        setattr(database.get_raw_item("TALT"), flag, True)
        mav.setMode("GUIDED")
        assert mav.conn.mav.sent == [], flag
        setattr(database.get_raw_item("TALT"), flag, False)
    run(mav, position(MSL_FT))
    mav.setMode("GUIDED")
    assert mav.conn.mav.sent[-1][1][12] == pytest.approx(MSL_FT * 0.3048)


def test_reconnected_link_clears_old_left_by_previous_connection(
    database, monkeypatch
):
    # A new Mav after a reconnect starts with no link; the item is still old
    # from the last one, and the first heartbeat must clear it.
    database.get_raw_item("MAVMSG").old = True
    mav, item, clock, _ = link_loss_mav(database, monkeypatch, [None, heartbeat(7)])

    clock[0] += LINK_TIMEOUT + 1
    mav.process()
    assert item.old is True

    mav.process()
    assert item.value[0] == "Heading Hold"
    assert item.old is False


def test_process_sys_status_sends_arm_command_when_prearm_check_passes():
    parent = FakeParent()
    mav = make_mav(
        parent=parent,
        messages=[
            Msg(
                "SYS_STATUS",
                onboard_control_sensors_health=mav_module.mavutil.mavlink.MAV_SYS_STATUS_PREARM_CHECK,
            ),
            Msg("SYS_STATUS", onboard_control_sensors_health=0),
        ],
    )

    mav.process()
    mav.process()

    assert mav.conn.mav.sent[0][0] == "long"


def test_send_trims_adjusts_and_restores_saved_trims():
    parent = FakeParent()
    mav = make_mav(parent=parent)
    parent.values["MAVREQADJ"] = (True, False, False, False, False, False)
    mav._apmode = "CRUISE"

    mav.sendTrims()

    assert_write(parent, "MAVADJ", True)
    assert_write(parent, "TRIMR", 0)
    assert mav.conn.mav.manual_controls

    parent.values["MAVREQADJ"] = (False, False, False, False, False, False)
    mav.sendTrims()
    assert_write(parent, "MAVADJ", False)

    mav._apmode = "CRUISE"
    mav._outputPitch = 120
    mav._outputRoll = -80
    mav._outputYaw = 30
    mav.sendTrims()
    assert mav._trimsSaved is True
    assert_write(parent, "TRIMP", 12.0)
    assert_write(parent, "TRIMR", -8.0)
    assert_write(parent, "TRIMY", 3.0)

    mav._apmode = "TRIM"
    mav.sendTrims()
    assert_write(parent, "TRIMR", 0.0)
    assert_write(parent, "TRIMP", 0.0)
    assert_write(parent, "TRIMY", 0.0)


def test_send_trims_does_not_send_when_not_leader():
    parent = FakeParent()
    parent.quorum.leader = False
    mav = make_mav(parent=parent)
    mav._apmode = "TRIM"

    mav.sendTrims()

    assert mav.conn.mav.manual_controls == []


def test_check_mode_returns_when_not_leader_and_changes_requested_mode(monkeypatch):
    parent = FakeParent()
    parent.quorum.leader = False
    mav = make_mav(parent=parent)
    calls = []
    mav.checkWaypoint = lambda: calls.append("waypoint")

    mav.checkMode()
    assert calls == ["waypoint"]

    parent.quorum.leader = True
    parent.values["MAVREQTRIM"] = (True, False, False, False, False, False)
    parent.values["MAVREQCRUISE"] = (True, False, False, False, False, False)
    mav._apmode = "CRUISE"
    mav.setMode = lambda mode: calls.append(mode)

    mav.checkMode()

    assert calls[-1] == "TRIM"
    assert_write(parent, "MAVREQCRUISE", False)


def test_check_waypoint_invalid_guided_drops_to_cruise_and_valid_updates_mode():
    parent = FakeParent()
    mav = make_mav(parent=parent)
    calls = []
    mav.setMode = lambda mode, change_mode=True: calls.append((mode, change_mode))
    mav._apreq = "GUIDED"
    mav._apmode = "GUIDED"
    parent.values["WPLAT"] = (0.0, False, False, False, False, False)

    mav.checkWaypoint()

    assert calls == [("CRUISE", True)]
    assert_write(parent, "MAVREQGUIDED", False)
    assert_write(parent, "MAVREQCRUISE", True)
    assert_write(parent, "MAVWPVALID", False)

    parent.values["WPLAT"] = (40.0, False, False, False, False, False)
    parent.values["WPLON"] = (-88.0, False, False, False, False, False)
    parent.values["WPNAME"] = ("NEW", False, False, False, False, False)
    mav._waypoint = "old"
    calls.clear()
    mav.checkWaypoint()

    assert mav._apwpv is True
    # A new waypoint moves the GUIDED target and never asks for GUIDED.
    assert calls == [("GUIDED", False)]
    assert_write(parent, "MAVWPVALID", True)


def test_a_guided_the_plugin_did_not_ask_for_is_left_alone():
    # AFC7a: a ground station put ArduPlane in GUIDED. The flight plan's
    # waypoint must not retarget it, nor a cleared plan drop it to CRUISE.
    parent = FakeParent()
    mav = make_mav(parent=parent, messages=[heartbeat(7), heartbeat(15)])
    mav._apstat = "ARMED"
    mav.process()                       # CRUISE: the request baseline
    mav.process()                       # GUIDED, from somewhere else
    assert (mav._apreq, mav._apmode) == ("CRUISE", "GUIDED")
    mav._waypoint = "old"
    mav.checkMode()
    parent.values["WPLAT"] = (0.0, False, False, False, False, False)
    mav.checkMode()
    assert mav.conn.mav.sent == []


def test_set_mode_valid_invalid_guided_and_non_leader(monkeypatch):
    parent = FakeParent()
    mav = make_mav(parent=parent)
    monkeypatch.setattr(mav_module.time, "sleep", lambda _seconds: None)

    mav._apstat = "TRIM"
    mav._apwpv = False
    mav.setMode("CRUISE")
    assert_write(parent, "MAVMSG", "Invalid Request")

    parent.quorum.leader = False
    mav._apstat = "ARMED"
    mav.setMode("CRUISE")
    assert mav.conn.mav.sent == []

    parent.quorum.leader = True
    mav.setMode("CRUISE")
    assert mav.conn.mav.sent[-1][0] == "long"
    # A request is not a report: MAVMODE and _apmode wait for the heartbeat
    # (AFC7a).
    assert mav._apreq == "CRUISE"
    assert mav._apmode == "INIT"
    assert "MAVMODE" not in [k for k, _v in parent.writes]

    mav._apwpv = True
    mav.setMode("GUIDED")
    assert mav.conn.mav.sent[-1][0] == "int"
    assert mav.conn.mav.sent[-1][1][7] == 1     # param2: change mode
    assert mav._waypoint == "-88.040.0HOME"
    mav.setMode("GUIDED", change_mode=False)
    assert mav.conn.mav.sent[-1][1][7] == 0     # only moves an active GUIDED

    mav._apstat = "ERROR"
    mav.setMode("TRIM")


def test_avg_and_get_avg_limit_samples():
    mav = make_mav()
    mav._max_average = 2

    assert mav.avg("X", 1, 1) == 1
    assert mav.avg("X", 3, 1) == 2
    assert mav.avg("X", 5, 1) == 4
    assert mav._data["X"] == [3, 5]
    assert mav.get_avg("X", 1) == 4
    assert mav.get_avg("EMPTY", 1) == 0


class FakeMainMav:
    instances = []

    def __init__(self, parent, port, baud, options, autopilot=None):
        self.parent = parent
        self.autopilot = autopilot
        self.port = port
        self.baud = baud
        self.options = options
        self.closed = False
        self.process_calls = 0
        FakeMainMav.instances.append(self)
        if options.get("raise_init"):
            raise RuntimeError("init failed")

    def wait_heartbeat(self):
        if self.options.get("raise_heartbeat"):
            raise RuntimeError("heartbeat failed")

    def process(self):
        self.process_calls += 1
        if self.options.get("raise_process"):
            raise RuntimeError("process failed")
        if self.process_calls > 42:
            self.parent.thread.getout = True

    def sendTrims(self):
        pass

    def checkMode(self):
        self.checked_mode = True

    def close(self):
        self.closed = True


def test_main_thread_run_success_and_error_paths(monkeypatch):
    monkeypatch.setattr(mav_plugin, "Mav", FakeMainMav)
    monkeypatch.setattr(mav_plugin.time, "sleep", lambda _seconds: None)
    parent = FakeParent()
    parent.config = {"type": "serial", "baud": "38400", "port": "/dev/fake", "options": {}}
    thread = mav_plugin.MainThread(parent)
    parent.thread = thread

    thread.run()

    assert FakeMainMav.instances[-1].baud == 38400
    assert getattr(FakeMainMav.instances[-1], "checked_mode", False) is True

    parent.config = {"type": "serial", "options": {"raise_heartbeat": True}}
    thread = mav_plugin.MainThread(parent)
    parent.thread = thread
    sleeps = 0

    def stop_after_retry(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            thread.getout = True

    monkeypatch.setattr(mav_plugin.time, "sleep", stop_after_retry)
    thread.run()
    assert FakeMainMav.instances[-1].closed is True

    parent.config = {"type": "serial", "options": {"raise_process": True}}
    thread = mav_plugin.MainThread(parent)
    parent.thread = thread
    sleeps = 0
    monkeypatch.setattr(mav_plugin.time, "sleep", stop_after_retry)
    thread.run()
    assert FakeMainMav.instances[-1].closed is True


def test_main_thread_keeps_one_autopilot_adapter_across_reconnects(monkeypatch):
    # The request baseline and the disconnect latch must survive a
    # reconnect, and the APCMDSEQ callback must be removed when it stops.
    monkeypatch.setattr(mav_plugin, "Mav", FakeMainMav)
    parent = FakeParent()
    parent.config = {"type": "serial", "options": {"raise_heartbeat": True}}
    thread = mav_plugin.MainThread(parent)
    parent.thread = thread
    sleeps = 0

    def stop_after_three(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 5:
            thread.getout = True

    monkeypatch.setattr(mav_plugin.time, "sleep", stop_after_three)
    FakeMainMav.instances.clear()
    thread.run()
    adapters = {id(m.autopilot) for m in FakeMainMav.instances}
    assert len(FakeMainMav.instances) >= 2 and len(adapters) == 1
    assert FakeMainMav.instances[0].autopilot is thread.autopilot
    assert parent.callbacks == []


def test_main_thread_runs_check_mode_at_once_for_a_head_request(monkeypatch):
    # checkMode otherwise runs every 40 loops: up to 4 s on a quiet link.
    class PendingOnce:
        def __init__(self, parent):
            self.left = 1

        def pending(self):
            self.left -= 1
            return self.left >= 0

        def close(self):
            pass

    class Recording(FakeMainMav):
        def checkMode(self):
            self.first_check = getattr(self, "first_check", self.process_calls)

    monkeypatch.setattr(mav_plugin, "Mav", Recording)
    monkeypatch.setattr(mav_plugin, "Autopilot", PendingOnce)
    parent = FakeParent()
    parent.config = {"type": "serial", "options": {}}
    thread = mav_plugin.MainThread(parent)
    parent.thread = thread
    thread.run()
    assert FakeMainMav.instances[-1].first_check == 1


def test_main_thread_autopilot_option_off(monkeypatch):
    monkeypatch.setattr(mav_plugin, "Mav", FakeMainMav)
    parent = FakeParent()
    parent.config = {"type": "serial", "options": {"autopilot": False}}
    thread = mav_plugin.MainThread(parent)
    parent.thread = thread
    FakeMainMav.instances.clear()
    thread.run()
    assert thread.autopilot is None
    assert FakeMainMav.instances[-1].autopilot is None
    assert getattr(parent, "callbacks", []) == []


def test_main_thread_run_init_failure_logs_and_retries(monkeypatch):
    monkeypatch.setattr(mav_plugin, "Mav", FakeMainMav)
    parent = FakeParent()
    parent.config = {"type": "serial", "options": {"raise_init": True}}
    thread = mav_plugin.MainThread(parent)
    sleeps = 0

    def stop_after_retry(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            thread.getout = True

    monkeypatch.setattr(mav_plugin.time, "sleep", stop_after_retry)

    thread.run()

    assert any("failed to connect" in str(message) for message in parent.log.messages)


def test_plugin_lifecycle_and_invalid_type(monkeypatch):
    class DummyThread:
        def __init__(self, parent):
            self.parent = parent
            self.started = False
            self.stopped = False
            self.alive = False

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.joined = timeout

    monkeypatch.setattr(mav_plugin, "MainThread", DummyThread)

    plugin = mav_plugin.Plugin("mavlink", {"type": "serial"}, {})
    plugin.run()
    plugin.stop()

    assert plugin.thread.started is True
    assert plugin.thread.stopped is True
    assert plugin.get_status() == mav_plugin.OrderedDict()

    with pytest.raises(ValueError, match="Only serial"):
        mav_plugin.Plugin("mavlink", {"type": "udp"}, {})

    failing = mav_plugin.Plugin("mavlink", {"type": "serial"}, {})
    failing.thread.alive = True
    with pytest.raises(plugin_base.PluginFail):
        failing.stop()


def test_main_thread_stop_sets_getout():
    parent = FakeParent()
    parent.config = {"type": "serial"}
    thread = mav_plugin.MainThread(parent)

    thread.stop()

    assert thread.getout is True


def test_plugin_stop_ignores_join_exception_then_raises(monkeypatch):
    class BadJoinThread:
        def __init__(self, parent):
            self.parent = parent

        def start(self):
            pass

        def stop(self):
            pass

        def is_alive(self):
            return True

        def join(self, timeout):
            raise RuntimeError("join failed")

    monkeypatch.setattr(mav_plugin, "MainThread", BadJoinThread)
    plugin = mav_plugin.Plugin("mavlink", {"type": "serial"}, {})

    with pytest.raises(plugin_base.PluginFail):
        plugin.stop()
