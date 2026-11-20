"""OnSpeed plugin optional outputs: PITCH, ROLL, IAS and ALT (CERT13h, AER-6082).

The database entries are the shipped ones (database/ahrs.yaml), so the
ranges and tols under test are the real ones.  Frames are the spec's V1, V2
and V5 vectors, built from the firmware's sprintf independently of the
decoder, and variants of V1 re-encoded with the firmware's format string.
"""

import io
import os

import pytest
import yaml

import fixgw.cfg as cfg
import fixgw.database as database
import fixgw.plugins.compute as compute
import fixgw.plugins.onspeed as onspeed
from fixgw import airdata
from fixgw.plugins.onspeed.sentence import FIELDS, encode, parse

from .test_sentence import V1, V1_RAW, V2, V4, V5

CONFIG = os.path.join(os.path.dirname(onspeed.__file__), "..", "..", "config")
KEYS = ("AOA", "PITCH", "ROLL", "IAS", "ALT", "BARO", "PALT")
CRLF = b"\r\n"
ALL = {"attitude": True, "airspeed": True, "altitude": True}


def _db_config(keys=KEYS):
    with open(os.path.join(CONFIG, "database", "ahrs.yaml")) as f:
        entries = [e for e in yaml.safe_load(f)["items"] if e["key"] in keys]
    assert {e["key"] for e in entries} == set(keys)
    entries.append({"key": "ZZLOADER", "description": "DB load sentinel", "type": "str", "initial": "Loaded"})
    return yaml.safe_dump({"variables": {}, "entries": entries})


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, key, value, udata):
        self.calls.append((key, value))

    def of(self, key):
        return [v for k, v in self.calls if k == key]


@pytest.fixture
def db():
    database.init(io.StringIO(_db_config()))
    rec = Recorder()
    for key in KEYS:
        database.callback_add("test", key, rec, None)
    return rec


def _frame(**raw):
    values = list(V1_RAW)
    for name, value in raw.items():
        values[[f[0] for f in FIELDS].index(name)] = value
    return encode(values).encode()


def _plugin(**config):
    c = {"port": "/dev/null"}
    c.update(config)
    return onspeed.Plugin("onspeed", c, {})


def _fed(plugin, *lines):
    plugin.thread.feed(b"partial" + CRLF + b"".join(line + CRLF for line in lines))
    return plugin


def _read(key):
    value, _ann, old, bad, fail, _sec = database.read(key)
    return value, old, bad, fail


def _writes(plugin, monkeypatch, *lines):
    keys = []
    real = plugin.db_write
    monkeypatch.setattr(plugin, "db_write", lambda k, v: (keys.append(k), real(k, v)))
    _fed(plugin, *lines)
    return keys


# -- off by default --------------------------------------------------------


def test_default_config_writes_only_aoa(db, monkeypatch):
    p = _plugin()
    assert set(_writes(p, monkeypatch, V1, V2, V5)) == {"AOA", "AOA.Warn"}
    assert database.writers() == {"AOA": ["onspeed"]}
    assert p.get_status()["Outputs"] == ["AOA", "AOA.Warn"]


def test_shipped_config_turns_no_output_on():
    conf = cfg.from_yaml(os.path.join(CONFIG, "connections", "onspeed.yaml"))
    section = conf["onspeed"]
    for option, _keys in onspeed.OUTPUT_GROUPS:
        assert section[option] is False  # present, so a builder sees it
    assert onspeed.output_keys(section) == []
    prefs = cfg.from_yaml(os.path.join(CONFIG, "preferences.yaml"))
    assert prefs["enabled"]["ONSPEED"] is False


@pytest.mark.parametrize("value", [False, "false", "no", "off", "0", None, ""])
def test_a_false_like_value_does_not_turn_an_output_on(value):
    assert onspeed.output_keys({"attitude": value}) == []


@pytest.mark.parametrize("value", [True, "true", "yes", "on", "1"])
def test_a_true_like_value_turns_an_output_on(value):
    assert onspeed.output_keys({"airspeed": value}) == ["IAS"]


@pytest.mark.parametrize(
    "option,keys",
    [("attitude", {"PITCH", "ROLL"}), ("airspeed", {"IAS"}), ("altitude", {"ALT"})],
)
def test_each_option_turns_on_only_its_keys(db, monkeypatch, option, keys):
    p = _plugin(**{option: True})
    assert set(_writes(p, monkeypatch, V1)) == {"AOA", "AOA.Warn"} | keys
    assert set(database.writers()) == {"AOA"} | keys
    assert all(w == ["onspeed"] for w in database.writers().values())


def test_new_options_are_not_reported_unknown(db, caplog):
    caplog.set_level("WARNING")
    _plugin(**ALL)
    assert "Unknown configuration option" not in caplog.text


# -- values ------------------------------------------------------------------


def test_v1_writes_attitude_and_airspeed(db):
    p = _fed(_plugin(**ALL), V1)
    assert _read("PITCH") == (5.2, False, False, False)
    assert _read("ROLL") == (-12.3, False, False, False)
    assert _read("IAS") == (85.4, False, False, False)
    assert p.get_status()["Outputs"] == ["AOA", "AOA.Warn", "PITCH", "ROLL", "IAS", "ALT"]


def test_value_and_flags_arrive_in_one_update(db):
    _fed(_plugin(**ALL), V1)
    for key in ("PITCH", "ROLL", "IAS", "ALT"):
        assert len(db.of(key)) == 1
        assert db.of(key)[0][2:5] == (False, False, False)


def test_alt_at_standard_baro_is_pressure_altitude(db):
    # BARO untouched reads its schema initial, 29.92: 1.2 ft off 29.92126.
    _fed(_plugin(altitude=True), V1)
    value, old, bad, fail = _read("ALT")
    assert value == pytest.approx(4500 - airdata.baro_offset_ft(29.92))
    assert abs(value - 4500) < 2
    assert (old, bad, fail) == (False, False, False)


@pytest.mark.parametrize("baro", [28.50, 29.92126, 30.12, 31.00])
def test_alt_round_trips_through_compute_palt(db, baro):
    # compute's PALT, fed this ALT and BARO, gives back OnSpeed's pressure
    # altitude: the two plugins share one formula, run in opposite directions.
    database.write("BARO", baro)
    _fed(_plugin(altitude=True), V1)

    class Parent:
        db_get_item = staticmethod(database.get_raw_item)
        db_read = staticmethod(database.read)

    func = compute.altPressure(["BARO", "ALT"], "PALT", False)
    func("BARO", database.read("BARO"), Parent())
    func("ALT", database.read("ALT"), Parent())
    assert database.read("PALT")[0] == pytest.approx(parse(V1).palt, abs=1e-6)


def test_alt_offset_against_the_rule_of_thumb(db):
    # Independent of the formula: near sea level an inch of mercury is about
    # 900 to 950 ft, and a higher setting reads higher.
    database.write("BARO", 30.92)
    _fed(_plugin(altitude=True), _frame(palt=0))
    assert 900 < _read("ALT")[0] < 950


def test_a_baro_change_shows_on_the_next_frame(db):
    p = _fed(_plugin(altitude=True), V1)
    first = _read("ALT")[0]
    database.write("BARO", 30.42)
    p.thread.feed(V1 + CRLF)
    assert _read("ALT")[0] == pytest.approx(4500 - airdata.baro_offset_ft(30.42))
    assert _read("ALT")[0] > first + 400


@pytest.mark.parametrize(
    "baro",
    [(29.92, False, True, False), (29.92, False, False, True), (24.0, False, False, False), (34.0, False, False, False)],
    ids=["baro-bad", "baro-failed", "baro-low", "baro-high"],
)
def test_unusable_baro_flags_alt_bad(db, baro):
    database.write("BARO", baro)
    _fed(_plugin(altitude=True), V1)
    value, old, bad, fail = _read("ALT")
    assert (old, bad, fail) == (False, True, False)
    assert value == pytest.approx(4500 - airdata.baro_offset_ft(baro[0]))


def test_alt_bad_clears_when_baro_is_fixed(db):
    database.write("BARO", (29.92, False, True, False))
    p = _fed(_plugin(altitude=True), V1)
    assert _read("ALT")[2] is True
    database.write("BARO", (29.92, False, False, False))
    p.thread.feed(V1 + CRLF)
    assert _read("ALT")[2] is False


@pytest.mark.parametrize(
    "raw,key,limit",
    [
        ({"pitch": 950}, "PITCH", 90.0),
        ({"pitch": -950}, "PITCH", -90.0),
        ({"palt": 99999}, "ALT", 60000.0),
        ({"palt": -9999}, "ALT", -2000.0),
    ],
)
def test_out_of_range_value_is_clamped_and_bad(db, raw, key, limit):
    # The database clamps silently; a clamped value must not read as good.
    p = _fed(_plugin(**ALL), _frame(**raw))
    assert _read(key) == (limit, False, True, False)
    p.thread.feed(V1 + CRLF)
    assert _read(key)[2] is False


def test_low_ias_frame_publishes_airspeed_good(db):
    # V2 is taxi: IAS 25.0 kt, below min_ias.  AOA goes bad; IAS is real.
    _fed(_plugin(**ALL), V2)
    assert _read("AOA")[2] is True
    assert _read("IAS") == (25.0, False, False, False)
    assert _read("PITCH") == (-0.8, False, False, False)
    assert _read("ALT")[2] is False


def test_uncalibrated_box_still_publishes_outputs(db):
    _fed(_plugin(**ALL), V5)
    assert database.read("AOA.Warn") is None
    assert _read("IAS") == (85.4, False, False, False)


def test_rejected_frame_writes_no_output(db):
    p = _fed(_plugin(**ALL), V1)
    before = list(db.calls)
    p.thread.feed(V4 + CRLF)
    assert db.calls == before


# -- staleness and port failure ---------------------------------------------


def test_outputs_go_old_on_their_own_tol(db, monkeypatch):
    _fed(_plugin(**ALL), V1)
    now = database.time.time()
    monkeypatch.setattr(database.time, "time", lambda: now + 0.4)
    database.update()
    assert _read("PITCH")[1] is True  # tol 300 ms
    assert _read("IAS")[1] is False  # tol 2000 ms
    monkeypatch.setattr(database.time, "time", lambda: now + 2.1)
    database.update()
    assert _read("IAS")[1] is True
    assert _read("ALT")[1] is True


def test_port_failure_fails_every_output_and_a_frame_clears_it(db):
    p = _fed(_plugin(**ALL), V1)
    p.thread._port_failed("unplugged")
    for key in ("AOA", "PITCH", "ROLL", "IAS", "ALT"):
        assert database.read(key)[4] is True, key
    p.thread.feed(V1 + CRLF)
    for key in ("AOA", "PITCH", "ROLL", "IAS", "ALT"):
        assert database.read(key)[4] is False, key


def test_port_failure_leaves_outputs_that_are_off_alone(db):
    p = _fed(_plugin(airspeed=True), V1)
    p.thread._port_failed("unplugged")
    assert database.read("IAS")[4] is True
    assert database.read("PITCH")[4] is False
    assert database.read("ALT")[4] is False


# -- an older database -------------------------------------------------------


def test_missing_key_turns_that_output_off_and_keeps_aoa(caplog):
    database.init(io.StringIO(_db_config(("AOA", "PITCH", "IAS", "ALT", "BARO"))))
    caplog.set_level("ERROR")
    p = _fed(_plugin(**ALL), V1, V1)
    assert "no ROLL key in the database: ROLL output turned off" in caplog.text
    assert caplog.text.count("ROLL output turned off") == 1
    assert p.thread.outputs == ["PITCH", "IAS", "ALT"]
    assert _read("AOA")[0] == 9.8
    assert _read("PITCH")[0] == 5.2
    p.thread._port_failed("unplugged")  # must not raise on the missing key
    assert database.read("PITCH")[4] is True


def test_missing_baro_turns_alt_off(caplog):
    database.init(io.StringIO(_db_config(("AOA", "IAS", "ALT"))))
    caplog.set_level("ERROR")
    p = _fed(_plugin(airspeed=True, altitude=True), V1, V1)
    assert caplog.text.count("no BARO key in the database: ALT output turned off") == 1
    assert p.thread.outputs == ["IAS"]
    assert database.get_raw_item("ALT").written is False
    assert _read("IAS")[0] == 85.4
