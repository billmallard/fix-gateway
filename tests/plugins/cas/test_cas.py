"""
Unit tests for the cas plugin (CAS2, AER-6083; CAS1 brief section 12.1).

Most tests build the plugin on the shipped database, call setup() (run()
without the thread) and drive step() with a fake clock, so timing is exact.
The C1 tests run the real thread.

Coverage:
  - Each shipped entry raises its exact text and level (FHA CAS-5)
  - Priority: warnings, cautions, advisories; unacknowledged first; newest
    first within each
  - Edge detection on flags; limits and values only on a valid key
  - Acknowledge by sequence; a later message is not acknowledged; a CASACK
    ahead of CASSEQ is ignored; CASSEQ starts above any CASACK (C3)
  - The acknowledge button: rising edge only, debounced (C3)
  - latch, clear_delay, on_delay, the never-written grace
  - NAVFAIL follows the HSI rule; NAV FAIL only on lost guidance (C5)
  - Catalogue refusals: ';', '|', length, case, unknown fields, duplicates
  - The keys are rewritten every second; slots past 12 are counted (CASCNT)
  - A slow or failing evaluation does not slow the writer of a watched key,
    and a failing callback does not raise into the writer (C1)
"""
import threading
import time

import pytest
import yaml

import fixgw.plugin as plugin_base
from fixgw.plugins import cas

SHIPPED = "src/fixgw/config/connections/cas.yaml"


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def shipped_config():
    with open(SHIPPED) as f:
        return yaml.safe_load(f)["cas"]


def make(database, entries=None, clock=None, **options):
    config = shipped_config() if entries is None else {"entries": entries}
    config.update(options)
    p = cas.Plugin("cas", config, None)
    p.clock = clock or Clock()
    p.setup()
    return p


def step(p, dt=0.0):
    p.clock.t += dt
    p.step(now=p.clock.t)


def slots(database):
    return [database.read(k)[0] for k in cas.SLOT_KEYS]


def lines(database):
    """The active lines as (level, acked, id, text) from the bus slots."""
    out = []
    for s in slots(database):
        if s:
            level, ack, ident, text, _detail = s.split("|")
            out.append((int(level), ack == "1", ident, text))
    return out


def ids(database):
    return [line[2] for line in lines(database)]


def past_test(p, database):
    """Step past CAS TEST so it is out of the list."""
    step(p, p.test_time + 0.1)
    assert "CASTEST" not in ids(database)


def fresh(database, key, value):
    database.get_raw_item(key).set(value, old=False, bad=False, fail=False)


# -- each shipped entry: exact text and level (FHA CAS-5) --------------------

def test_shipped_catalogue_loads_without_refusals(database):
    p = make(database)
    assert p.refused == []
    assert [m.entry.id for m in p.messages] == ["CASTEST", "APLINK", "HDGFAIL",
                                                 "NAVFAIL"]


def test_cas_test_is_an_advisory_at_start_for_five_seconds(database):
    p = make(database)
    step(p)
    assert lines(database) == [(1, False, "CASTEST", "CAS TEST")]
    step(p, 4.9)
    assert "CASTEST" in ids(database)
    step(p, 0.2)
    assert "CASTEST" not in ids(database)


def test_cas_test_cannot_be_turned_off(database):
    p = make(database, test_time=0)
    step(p)
    assert "CASTEST" in ids(database)


@pytest.mark.parametrize("engaged,level", [(True, 3), (False, 2)])
def test_ap_link_lost_warning_if_engaged_caution_if_not(database, engaged, level):
    p = make(database)
    fresh(database, "HEAD", 90.0)
    fresh(database, "APENG", engaged)
    past_test(p, database)
    assert "APLINK" not in ids(database)
    database.get_raw_item("APENG").old = True
    step(p)
    assert (level, False, "APLINK", "AP LINK LOST") in lines(database)


def test_ap_link_lost_never_on_an_install_without_an_autopilot(database):
    p = make(database)
    database.get_raw_item("APENG").old = True  # never valid in this run
    past_test(p, database)
    assert "APLINK" not in ids(database)


def test_ap_link_lost_clears_5_s_after_the_link_is_steady(database):
    p = make(database)
    fresh(database, "APENG", True)
    past_test(p, database)
    item = database.get_raw_item("APENG")
    item.old = True
    step(p)
    assert "APLINK" in ids(database)
    # Intermittent: 1 s back, 1 s gone, three times. One line, no flapping.
    for _ in range(3):
        fresh(database, "APENG", True)
        step(p, 1.0)
        assert "APLINK" in ids(database)
        item.old = True
        step(p, 1.0)
        assert "APLINK" in ids(database)
    seq = database.read("CASSEQ")[0]
    fresh(database, "APENG", True)
    step(p)
    step(p, 4.9)
    assert "APLINK" in ids(database)
    assert database.read("CASSEQ")[0] == seq  # no change while it held
    step(p, 0.2)
    assert "APLINK" not in ids(database)


def test_hdg_fail_is_a_caution_on_heading_invalid(database):
    p = make(database)
    fresh(database, "HEAD", 90.0)
    past_test(p, database)
    assert "HDGFAIL" not in ids(database)
    for flag in ("old", "bad", "fail"):
        setattr(database.get_raw_item("HEAD"), flag, True)
        step(p)
        assert (2, False, "HDGFAIL", "HDG FAIL") in lines(database)
        fresh(database, "HEAD", 90.0)
        step(p)
        assert "HDGFAIL" not in ids(database)


def test_hdg_fail_waits_30_s_for_a_heading_at_power_up(database):
    """HEAD's initial value expires 2 s after start, before an AHRS (or the
    demo) has written it: no HDG FAIL at every boot, but one if heading
    never comes."""
    p = make(database)
    database.get_raw_item("HEAD").expire()  # never written
    step(p)
    step(p, 29.9)
    assert "HDGFAIL" not in ids(database)
    step(p, 0.2)
    assert (2, False, "HDGFAIL", "HDG FAIL") in lines(database)


def test_an_ini_seeded_heading_is_not_a_reading(database):
    """The server writes ini values before any plugin starts, credited to no
    plugin. The bench's custom.ini has HEAD = 20.0, which armed HDG FAIL at
    once instead of after the grace."""
    database.write("HEAD", 20.0)  # as the server's ini loader does
    p = make(database)
    step(p)
    database.get_raw_item("HEAD").expire()
    step(p, 1)
    assert "HDGFAIL" not in ids(database)
    step(p, 29.2)
    assert "HDGFAIL" in ids(database)


def test_an_ini_seeded_fuel_value_still_raises_no_data(database):
    database.write("FUELQ3", 10.0)  # as the server's ini loader does
    p = make(database, fuel_no_data_tanks=[3], fuel_no_data_grace=10)
    step(p, 10.1)
    assert "FUELQ3ND" in ids(database)


def test_a_plugin_write_before_start_is_a_reading(database):
    """A source that started before cas has really written its key."""
    g = {"__name__": "fixgw.plugins.xplane", "database": database}
    exec("database.get_raw_item('APENG').set(True, old=False, bad=False, "
         "fail=False)", g)
    p = make(database)
    step(p)
    database.get_raw_item("APENG").old = True
    step(p, 6)
    assert (3, False, "APLINK", "AP LINK LOST") in lines(database)


@pytest.mark.parametrize("tank", [1, 2, 3])
def test_fuel_qty_no_data_is_a_caution_for_a_listed_tank(database, tank):
    p = make(database, fuel_no_data_tanks=[tank], fuel_no_data_grace=30)
    fresh(database, "HEAD", 90.0)
    step(p)
    step(p, 29.9)
    fresh(database, "HEAD", 90.0)
    step(p)
    assert "FUELQ{}ND".format(tank) not in ids(database)
    step(p, 0.2)
    assert (2, False, "FUELQ{}ND".format(tank),
            "FUEL QTY {} NO DATA".format(tank)) in lines(database)
    database.write("FUELQ{}".format(tank), 12.0)
    step(p)
    assert "FUELQ{}ND".format(tank) not in ids(database)


def test_fuel_no_data_only_for_listed_tanks(database):
    p = make(database)  # shipped: no tanks listed
    step(p, 60)
    assert not [i for i in ids(database) if i.startswith("FUELQ")]


# -- NAV FAIL and NAVFAIL (brief 5.4, C5) ------------------------------------

def nav_valid(database, valid=True):
    fresh(database, "CDI", 0.1)
    fresh(database, "CDIV", 1.0 if valid else 0.0)
    fresh(database, "COURSE", 90.0)


def test_nav_fail_is_a_caution_on_loss_of_guidance_the_pilot_had(database):
    p = make(database)
    fresh(database, "HEAD", 90.0)
    nav_valid(database)
    past_test(p, database)
    assert database.read("NAVFAIL")[0] is False
    assert "NAVFAIL" not in ids(database)
    database.get_raw_item("CDI").fail = True
    step(p)
    assert (2, False, "NAVFAIL", "NAV FAIL") in lines(database)
    assert database.read("NAVFAIL")[0] is True
    database.get_raw_item("CDI").fail = False
    step(p)
    assert "NAVFAIL" not in ids(database)


def test_no_nav_fail_on_a_source_never_received(database):
    """An untuned VOR flags NAV on the HSI, which is information; as a
    caution it would stand on every VFR flight (C5)."""
    p = make(database)
    database.write("NAVSRC", 0)
    nav_valid(database, valid=False)  # tuned to nothing: CDIV 0
    past_test(p, database)
    assert database.read("NAVFAIL")[0] is True  # the HSI flag
    assert "NAVFAIL" not in ids(database)  # but no caution
    nav_valid(database, valid=True)  # received
    step(p)
    nav_valid(database, valid=False)  # and lost
    step(p)
    assert "NAVFAIL" in ids(database)


def test_switching_to_a_never_received_source_raises_nothing(database):
    p = make(database)
    database.write("NAVSRC", 2)
    nav_valid(database)
    past_test(p, database)
    database.write("NAVSRC", 0)
    nav_valid(database, valid=False)
    step(p)
    assert database.read("NAVFAIL")[0] is True
    assert "NAVFAIL" not in ids(database)


@pytest.mark.parametrize("key,flag", [("CDI", "old"), ("CDI", "bad"),
                                      ("CDI", "fail"), ("COURSE", "bad"),
                                      ("COURSE", "fail"), ("CDIV", 0.4)])
def test_navfail_follows_the_hsi_rule(database, key, flag):
    p = make(database)
    nav_valid(database)
    step(p)
    assert database.read("NAVFAIL")[0] is False
    if isinstance(flag, str):
        setattr(database.get_raw_item(key), flag, True)
    else:
        fresh(database, key, flag)
    step(p, 0.1)  # inside the 1 s refresh: the change goes out at once
    assert database.read("NAVFAIL")[0] is True


@pytest.mark.parametrize("key,flag", [("CDIV", "old"), ("COURSE", "old")])
def test_navfail_ignores_what_the_hsi_ignores(database, key, flag):
    """The HSI reads CDIV's value and COURSE's bad and fail only; a stale
    CDIV of 1 still shows the needle, and COURSE is a setting (tol 0)."""
    p = make(database)
    nav_valid(database)
    step(p)
    setattr(database.get_raw_item(key), flag, True)
    step(p, 0.1)
    assert database.read("NAVFAIL")[0] is False


# -- priority, acknowledge, sequence ----------------------------------------

def value_entry(ident, level, key):
    return {"id": ident, "level": level, "text": ident,
            "when": {"value": "{} == true".format(key)}}


# Bool keys with tol 0 from the shipped database, as plain triggers.
TRIGGERS = ["HIDEBUTTON", "SYSMENU", "CASACKBTN"]


def priority_catalogue():
    return [value_entry("ADV", "advisory", "HIDEBUTTON"),
            value_entry("CAUA", "caution", "SYSMENU"),
            value_entry("CAUB", "caution", "HIDEBUTTON"),
            value_entry("WARN", "warning", "SYSMENU")]


def test_priority_level_then_unacknowledged_then_newest(database):
    p = make(database, priority_catalogue())
    past_test(p, database)
    database.write("HIDEBUTTON", True)  # ADV, CAUB
    step(p, 1)
    database.write("SYSMENU", True)  # CAUA, WARN, newer
    step(p, 1)
    assert ids(database) == ["WARN", "CAUA", "CAUB", "ADV"]
    database.write("CASACK", database.read("CASSEQ")[0])
    step(p)
    assert all(acked for _l, acked, _i, _t in lines(database))
    # A new caution after the acknowledge sorts above the acknowledged one.
    p.messages[3].active = False  # drop CAUB so it can come back new
    p.messages[3].onset_seq = None
    step(p)
    assert ids(database)[:2] == ["WARN", "CAUB"]
    assert lines(database)[1][1] is False
    assert database.read("CASTOP")[0] == 2


def test_castop_is_the_highest_unacknowledged_level(database):
    p = make(database, priority_catalogue())
    past_test(p, database)
    assert database.read("CASTOP")[0] == 0
    database.write("HIDEBUTTON", True)
    step(p)
    assert database.read("CASTOP")[0] == 2
    database.write("CASACK", database.read("CASSEQ")[0])
    step(p)
    assert database.read("CASTOP")[0] == 0


def test_acknowledge_covers_only_what_was_published(database):
    p = make(database, priority_catalogue())
    past_test(p, database)
    database.write("HIDEBUTTON", True)
    step(p)
    shown = database.read("CASSEQ")[0]
    database.write("SYSMENU", True)
    step(p)  # WARN and CAUA arrive after the pilot looked
    database.write("CASACK", shown)
    step(p)
    acked = {i: a for _l, a, i, _t in lines(database)}
    assert acked == {"WARN": False, "CAUA": False, "CAUB": True, "ADV": True}


def test_casack_ahead_of_casseq_is_ignored(database):
    p = make(database, priority_catalogue())
    past_test(p, database)
    database.write("HIDEBUTTON", True)
    step(p)
    database.write("CASACK", database.read("CASSEQ")[0] + 1)
    step(p)
    assert not any(a for _l, a, _i, _t in lines(database))


def test_a_stale_ignored_casack_does_not_swallow_a_real_one(database):
    p = make(database, priority_catalogue())
    past_test(p, database)
    seq = database.read("CASSEQ")[0]
    database.write("CASACK", seq + 1)  # ahead: ignored
    step(p)
    database.write("HIDEBUTTON", True)
    step(p)
    assert database.read("CASSEQ")[0] == seq + 1
    database.write("CASACK", seq + 1)  # the same number, now real
    step(p)
    assert all(a for _l, a, _i, _t in lines(database))


def test_casseq_starts_above_a_stale_casack_after_restart(database):
    """Bench step 15 (C3): CASACK high, restart the plugin, raise a caution:
    it must arrive unacknowledged."""
    database.write("CASACK", 500)
    p = make(database, priority_catalogue())
    step(p)
    assert database.read("CASSEQ")[0] > 500
    p.stop()
    p2 = make(database, priority_catalogue())
    step(p2)
    assert database.read("CASSEQ")[0] > 501
    database.write("SYSMENU", True)
    step(p2)
    assert ("WARN" in ids(database)
            and not dict((i, a) for _l, a, i, _t in lines(database))["WARN"])


def test_casseq_is_monotonic_across_a_plugin_restart(database):
    p = make(database, priority_catalogue())
    for _ in range(5):
        database.write("HIDEBUTTON", not database.read("HIDEBUTTON")[0])
        step(p)
    last = database.read("CASSEQ")[0]
    p.stop()
    p2 = make(database, priority_catalogue())
    step(p2)
    assert database.read("CASSEQ")[0] > last


def test_button_rising_edge_acknowledges_and_is_debounced(database):
    p = make(database, priority_catalogue()[:1], ack_debounce=0.5)
    past_test(p, database)
    database.write("HIDEBUTTON", True)
    step(p)
    database.write("CASACKBTN", True)
    step(p, 0.01)
    assert all(a for _l, a, _i, _t in lines(database))
    # A new message, then contact chatter inside the debounce time: the
    # chatter must not acknowledge it.
    p.messages[1].active = False
    p.messages[1].onset_seq = None
    step(p, 0.01)
    for v in (False, True, False, True):
        database.write("CASACKBTN", v)
        step(p, 0.05)
    assert lines(database)[0][1] is False
    database.write("CASACKBTN", False)
    step(p, 0.5)
    database.write("CASACKBTN", True)  # a real second press
    step(p)
    assert lines(database)[0][1] is True


def test_button_rewritten_true_while_held_is_not_a_press(database):
    p = make(database, priority_catalogue()[:1], ack_debounce=0.5)
    past_test(p, database)
    database.write("CASACKBTN", True)
    step(p)
    database.write("HIDEBUTTON", True)  # a new message while it is held
    step(p, 1.0)
    database.write("CASACKBTN", True)  # the same level, rewritten
    step(p, 1.0)
    assert lines(database)[0][1] is False


def test_button_held_true_at_start_is_not_a_press(database):
    database.write("CASACKBTN", True)
    p = make(database, priority_catalogue()[:1])
    step(p)
    assert lines(database)[0] == (1, False, "CASTEST", "CAS TEST")


# -- conditions and timing ---------------------------------------------------

def test_annunciate_only_on_a_valid_key(database):
    """A failed value is published as 0, below every low limit: the limit
    message must not fire on it (brief 5.1 change 2)."""
    entry = {"id": "OILP", "level": "warning", "text": "OIL PRESS",
             "when": {"annunciate": "OILP1"}}
    p = make(database, [entry])
    item = database.get_raw_item("OILP1")
    item.set(60.0, old=False, bad=False, fail=False)
    item.annunciate = True
    step(p)
    assert "OILP" in ids(database)
    item.fail = True
    step(p)
    assert "OILP" not in ids(database)


def test_value_condition_only_on_a_valid_key(database):
    p = make(database, [value_entry("DISC", "caution", "APDISC")])
    fresh(database, "APDISC", True)
    step(p)
    assert "DISC" in ids(database)
    database.get_raw_item("APDISC").old = True
    step(p)
    assert "DISC" not in ids(database)


def test_a_short_blip_between_cycles_still_raises(database):
    """Changes are applied one at a time on the plugin thread, so a flag
    that sets and clears between two cycles still raises its line."""
    entry = dict(value_entry("BLIP", "caution", "HIDEBUTTON"), latch=True)
    p = make(database, [entry])
    step(p)
    database.write("HIDEBUTTON", True)
    database.write("HIDEBUTTON", False)
    step(p)
    assert "BLIP" in ids(database)


def test_latch_holds_until_acknowledged(database):
    entry = dict(value_entry("EVT", "caution", "HIDEBUTTON"), latch=True)
    p = make(database, [entry])
    database.write("HIDEBUTTON", True)
    step(p)
    database.write("HIDEBUTTON", False)
    step(p, 10)
    assert "EVT" in ids(database)
    database.write("CASACK", database.read("CASSEQ")[0])
    step(p)
    assert "EVT" not in ids(database)


def test_on_delay_holds_the_onset(database):
    entry = dict(value_entry("SLOW", "caution", "HIDEBUTTON"), on_delay=2)
    p = make(database, [entry])
    database.write("HIDEBUTTON", True)
    step(p)
    step(p, 1.9)
    assert "SLOW" not in ids(database)
    step(p, 0.2)
    assert "SLOW" in ids(database)


def test_never_written_waits_out_the_grace(database):
    entry = {"id": "NOFQ", "level": "caution", "text": "FUEL QTY R NO DATA",
             "when": {"never_written": "FUELQ2", "grace": 10}}
    p = make(database, [entry])
    step(p, 9.9)
    assert "NOFQ" not in ids(database)
    step(p, 0.2)
    assert (2, False, "NOFQ", "FUEL QTY R NO DATA") in lines(database)


def test_never_written_ignores_the_keys_own_expiry(database):
    entry = {"id": "NOFQ", "level": "caution", "text": "NO DATA",
             "when": {"never_written": "FUELQ2", "grace": 1}}
    p = make(database, [entry])
    database.get_raw_item("FUELQ2").expire()
    step(p, 2)
    assert "NOFQ" in ids(database)


# -- catalogue refusals ------------------------------------------------------

@pytest.mark.parametrize("change,reason", [
    ({"text": "AP; LOST"}, "';'"),
    ({"text": "AP|LOST"}, "'|'"),
    ({"detail": "SEE; FPL"}, "';'"),
    ({"text": "A" * 21}, "longer"),
    ({"text": "ap link lost"}, "upper case"),
    ({"level": "alarm"}, "level"),
    ({"id": "ap-link"}, "id"),
    ({"clear_dealy": 5}, "unknown field"),
    ({"when": {"invalid": "NOSUCHKEY"}}, "no FIX key"),
    ({"when": {"invalid": "HEAD", "value": "HEAD > 1"}}, "exactly one"),
    ({"when": {"invalid": "HEAD", "grace": 5, "after_valid": True}},
     "do not combine"),
    ({"when": {"annunciate": "OILP1", "grace": 5}}, "grace applies"),
    ({"level": "warning", "on_delay": 2}, "on_delay_reason"),
])
def test_bad_entry_is_refused_and_named_and_the_rest_load(database, caplog,
                                                          change, reason):
    good = {"id": "GOOD", "level": "caution", "text": "GOOD",
            "when": {"invalid": "HEAD"}}
    bad = dict(good, id="BAD", text="BAD")
    bad.update(change)
    p = make(database, [bad, good])
    assert [m.entry.id for m in p.messages] == ["CASTEST", "GOOD"]
    assert p.refused == [str(bad["id"])]
    assert reason in caplog.text and str(bad["id"]) in caplog.text


def test_duplicate_id_is_refused(database):
    e = {"id": "DUP", "level": "caution", "text": "DUP",
         "when": {"invalid": "HEAD"}}
    p = make(database, [e, dict(e)])
    assert [m.entry.id for m in p.messages] == ["CASTEST", "DUP"]
    assert p.refused == ["DUP"]


def test_missing_cas_keys_refuse_the_plugin(database, monkeypatch):
    real = database.get_raw_item

    def no_cascnt(key):
        if key == "CASCNT":
            raise KeyError(key)
        return real(key)

    monkeypatch.setattr(database, "get_raw_item", no_cascnt)
    p = cas.Plugin("cas", shipped_config(), None)
    with pytest.raises(plugin_base.PluginFail, match="CASCNT"):
        p.run()
    p.stop()  # safe after a refused start


# -- publishing --------------------------------------------------------------

def test_keys_are_rewritten_every_second(database):
    p = make(database)
    step(p)
    item = database.get_raw_item("CASSEQ")
    stamp = item.timestamp
    step(p, 0.5)
    assert item.timestamp == stamp
    step(p, 0.6)
    assert item.timestamp > stamp
    assert database.read("CASSEQ")[2] is False  # fresh, not old


def test_the_keys_go_old_when_the_plugin_stops_writing(database):
    p = make(database)
    step(p)
    item = database.get_raw_item("CASMSG1")
    item.timestamp -= 3.1
    assert database.read("CASMSG1")[2] is True


def test_fifteen_alerts_count_all_and_slot_12_is_the_least_urgent(database):
    entries = []
    for n in range(15):
        level = ("warning", "caution", "advisory")[n % 3]
        entries.append(value_entry("A{}".format(n), level, "HIDEBUTTON"))
    p = make(database, entries)
    past_test(p, database)
    database.write("HIDEBUTTON", True)
    step(p)
    assert database.read("CASCNT")[0] == 15
    got = lines(database)
    assert len(got) == 12
    levels = [g[0] for g in got]
    assert levels == sorted(levels, reverse=True)
    assert levels.count(3) == 5 and levels.count(2) == 5 and levels.count(1) == 2
    assert got[-1][0] == 1


def test_slot_grammar(database):
    entry = {"id": "DET", "level": "warning", "text": "AP LINK LOST",
             "detail": "HANDS ON", "when": {"value": "HIDEBUTTON == true"}}
    p = make(database, [entry])
    past_test(p, database)
    database.write("HIDEBUTTON", True)
    step(p)
    assert slots(database)[0] == "3|0|DET|AP LINK LOST|HANDS ON"
    assert slots(database)[1] == ""


def test_writes_are_credited_to_cas_alone(database):
    p = make(database)
    nav_valid(database)
    step(p)
    w = database.writers()
    for k in cas.SLOT_KEYS + ["CASCNT", "CASTOP", "CASSEQ", "NAVFAIL"]:
        assert w.get(k) == ["cas"], k


# -- C1: CAS must not slow the data it watches (brief 5.5, step 13) ----------

def _write_rate(database, key, seconds=1.0, hz=200):
    """Write key at hz from a writer thread for seconds; return the writes
    done and the longest single write, in seconds."""
    item = database.get_raw_item(key)
    longest = 0.0
    count = 0
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        t0 = time.monotonic()
        item.set(float(count % 360), old=False, bad=False, fail=False)
        if count % 50 == 0:
            item.set(float(count % 360), old=False, bad=True, fail=False)
        longest = max(longest, time.monotonic() - t0)
        count += 1
        time.sleep(1.0 / hz)
    return count, longest


def test_slow_failing_evaluation_does_not_slow_a_watched_writer(database,
                                                                monkeypatch):
    """Bench step 13 in a unit test: a 500 ms sleep and an exception in the
    evaluation path. HEAD is watched (HDG FAIL) and its flags toggle every
    50 writes, so the callback runs on every write and queues often. The
    write rate and the longest write stay at the no-CAS baseline."""
    base_count, base_longest = _write_rate(database, "HEAD")

    calls = []

    def broken(now, polled=True):
        calls.append(now)
        time.sleep(0.5)
        raise RuntimeError("injected")

    p = cas.Plugin("cas", shipped_config(), None)
    monkeypatch.setattr(p, "_evaluate", broken)
    p.run()
    try:
        count, longest = _write_rate(database, "HEAD")
        pitch_count, pitch_longest = _write_rate(database, "PITCH")
    finally:
        p.stop()
    print("C1 HEAD writes/s base {} with-CAS {}; longest write base {:.4f} s "
          "with-CAS {:.4f} s; PITCH {} longest {:.4f} s; evaluations {} errors {}"
          .format(base_count, count, base_longest, longest, pitch_count,
                  pitch_longest, len(calls), p.errors))
    assert calls, "the injected evaluation never ran"
    assert p.errors >= 1
    assert count >= 0.8 * base_count
    assert longest < 0.05, longest  # never the 500 ms sleep
    assert pitch_count >= 0.8 * base_count and pitch_longest < 0.05
    # A list that cannot be computed is not rewritten as current.
    assert database.read("CASSEQ")[0] == 0


def test_a_failing_callback_does_not_raise_into_the_writer(database):
    """The aux path in database.py calls callbacks with no try/except."""
    entry = {"id": "OILP", "level": "warning", "text": "OIL PRESS",
             "when": {"annunciate": "OILP1"}}
    p = make(database, [entry])

    class Broken:
        def put(self, item):
            raise RuntimeError("injected")

    p.events = Broken()
    item = database.get_raw_item("OILP1")
    item.set_aux_value("lowAlarm", 10.0)  # an aux write reaches the callback
    item.set(60.0, old=False, bad=True, fail=False)
    item.set(60.0, old=False, bad=False, fail=False)
    assert p.callback_errors >= 1


def test_callback_queues_flag_edges_only(database):
    p = make(database)
    while not p.events.empty():
        p.events.get_nowait()
    item = database.get_raw_item("HEAD")
    for n in range(100):
        item.set(float(n), old=False, bad=False, fail=False)
    assert p.events.qsize() <= 1  # one edge at most: from whatever came before


def test_thread_publishes_cas_test_on_the_real_loop(database):
    p = cas.Plugin("cas", shipped_config(), None)
    p.run()
    try:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not database.read("CASMSG1")[0]:
            time.sleep(0.05)
        assert database.read("CASMSG1")[0] == "1|0|CASTEST|CAS TEST|"
        assert database.read("CASCNT")[0] >= 1
    finally:
        p.stop()
    assert not p.thread.is_alive()
