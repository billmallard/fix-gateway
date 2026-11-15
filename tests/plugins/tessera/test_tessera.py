"""Tessera plugin against the scripted panel (TESS1, AER-5754).

The plugin runs on its own thread with the real database and real pyserial,
talking to tests/plugins/tessera/panel.py over a pty.  Timings the protocol
fixes (the 4 s answer wait, the 2.5 s handshake) are shortened per test
through MainThread's class attributes; the 1 s ping is measured as shipped.
"""

import json
import os
import time

import pytest
import yaml

import fixgw.plugin as plugin_base
import fixgw.plugins.tessera as tessera
from fixgw.plugins.tessera import protocol
from fixgw.plugins.tessera.page import BAD_TEXT, Page, PageError

from .panel import ScriptedPanel

SHIPPED_PAGE = "src/fixgw/config/tessera/page.yaml"


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, key, value, udata):
        self.calls.append((key, value[0]))


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(tessera.MainThread, "ack_seconds", 0.5)
    monkeypatch.setattr(tessera.MainThread, "handshake_seconds", 0.5)


@pytest.fixture
def rig(database, tmp_path, fast):
    """make(page=..., **panel_kwargs) -> (plugin, panel); torn down after."""
    made = []

    def make(page=SHIPPED_PAGE, config=None, **panel_kwargs):
        link = str(tmp_path / "ttyTESSERA")
        panel = ScriptedPanel(link, **panel_kwargs)
        if isinstance(page, dict):
            path = tmp_path / "page.yaml"
            path.write_text(yaml.safe_dump(page))
            page = str(path)
        cfg = {"port": link, "page": os.path.abspath(page), "reconnect_interval": 0.1, "identify_interval": 60}
        cfg.update(config or {})
        p = tessera.Plugin("tessera", cfg, {})
        p.run()
        made.append((p, panel))
        return p, panel

    yield make
    for p, panel in made:
        p.stop()
        panel.close()


def accepted(p, panel, n=1):
    return wait_for(lambda: panel.layouts_accepted >= n and p.thread.counters["acks"] >= n)


# -- handshake ---------------------------------------------------------------


def test_handshake_accepts_tessera_p4_protocol_4(rig):
    p, panel = rig()
    assert accepted(p, panel)
    assert panel.lines()[0][1] == '{"cmd":"get_info"}'
    assert p.thread.connected and p.thread.boot_id == 1234
    assert [k["id"] for k in panel.layout] == list(range(1, 11))
    assert p.get_status()["Firmware"] == "0.9.0"


@pytest.mark.parametrize(
    "kwargs",
    [{"board": "esp32-other"}, {"protocol": 3}, {"protocol": 5}, {"protocol": "4"}],
    ids=["other-board", "protocol-3", "protocol-5", "protocol-as-text"],
)
def test_handshake_refuses_other_devices_and_retries(rig, kwargs):
    p, panel = rig(**kwargs)
    assert wait_for(lambda: p.thread.counters["refused"] >= 2)
    assert not p.thread.connected
    assert panel.lines("AD_CFG") == [] and panel.lines("AD_PING") == []
    assert len(panel.lines('{"cmd":"get_info"}')) >= 2


def test_silent_device_is_not_a_panel(rig):
    p, panel = rig()
    panel.silent = True
    p2_errors = lambda: "did not identify" in p.thread.last_error  # noqa: E731
    # The first handshake may already have finished; force a fresh one.
    panel.unplug()
    panel.plug()
    assert wait_for(p2_errors)
    assert not p.thread.connected


def test_early_press_from_an_earlier_session_is_dropped(rig):
    p, panel = rig()
    assert accepted(p, panel)
    panel.unplug()
    panel.plug()
    # A press queued before the handshake answer: the plugin discards input
    # when it opens the port, and the press is never answered.
    panel.press(3)
    assert wait_for(lambda: p.thread.counters["connects"] == 2)
    assert accepted(p, panel, 2)


# -- ping ----------------------------------------------------------------------


def test_ping_every_second(rig):
    p, panel = rig()
    assert accepted(p, panel)
    time.sleep(4.2)
    pings = [t for t, _ in panel.lines("AD_PING")]
    gaps = [b - a for a, b in zip(pings, pings[1:])]
    assert len(pings) >= 4
    assert all(0.9 <= g <= 1.2 for g in gaps), gaps


def test_pings_continue_while_a_push_waits_for_its_answer(rig, monkeypatch):
    monkeypatch.setattr(tessera.MainThread, "ack_seconds", 3.0)
    p, panel = rig()
    panel.ignore_cfg_end = 1
    assert wait_for(lambda: p.thread.counters["acks"] == 1, 5)  # answered after the resend at 3 s
    pings = [t for t, _ in panel.lines("AD_PING")]
    first_end = panel.lines("AD_CFG_END")[0][0]
    assert sum(1 for t in pings if first_end < t < first_end + 3.0) >= 2


# -- the page push -------------------------------------------------------------


def long_page(n=18):
    return {"keys": [{"id": i, "label": "KEY %02d LONG LABEL TEXT" % i, "show": "BARO"} for i in range(1, n + 1)]}


def test_page_goes_in_380_character_chunks(rig):
    p, panel = rig(page=long_page())
    assert accepted(p, panel)
    begin = panel.lines("AD_CFG_BEGIN")
    data = panel.lines("AD_CFG_DATA ")
    end = panel.lines("AD_CFG_END")
    assert len(begin) == 1 and len(end) == 1
    assert begin[0][0] <= data[0][0] and data[-1][0] <= end[0][0]
    chunks = [line[len("AD_CFG_DATA ") :] for _, line in data]
    assert len(chunks) > 1
    assert all(len(c) == protocol.CFG_CHUNK for c in chunks[:-1]) and 0 < len(chunks[-1]) <= protocol.CFG_CHUNK
    payload = "".join(chunks)
    assert json.loads(payload)["buttons"][17]["label"] == "KEY 18 LONG LABEL TEXT"
    assert all(len(line.encode()) <= protocol.LINE_MAX for _, line in panel.lines())


def test_unanswered_page_is_sent_once_more_after_the_wait(rig):
    p, panel = rig()
    panel.ignore_cfg_end = 1
    assert accepted(p, panel)
    ends = [t for t, _ in panel.lines("AD_CFG_END")]
    assert len(ends) == 2
    assert 0.45 <= ends[1] - ends[0] <= 0.8
    assert p.thread.counters["resends"] == 1
    assert len(panel.lines("AD_CFG_BEGIN")) == 2


def test_page_unanswered_twice_resets_the_link(rig):
    p, panel = rig()
    panel.ignore_cfg_end = 2
    assert wait_for(lambda: p.thread.counters["connects"] >= 2 and p.thread.counters["acks"] >= 1, 5)
    assert "did not answer the page twice" in p.thread.last_error
    assert p.thread.counters["resends"] == 1


def test_invalid_answer_stops_pushes_and_presses(rig, database):
    page = {"keys": [{"id": 1, "label": "BARO", "show": "BARO", "color": "amber"},
                     {"id": 2, "label": "UP", "text": "+", "press": {"pulse": "TSBTN12"}}]}
    p, panel = rig(page=page)
    assert wait_for(lambda: p.thread.counters["invalid"] == 1)
    assert panel.answers[-1] == "AD_CFG_END INVALID"
    database.write("BARO", 30.10)
    time.sleep(0.4)
    assert len(panel.lines("AD_CFG_END")) == 1  # the same answer would come back
    assert p.thread.counters["resends"] == 0


def test_too_big_answer_is_reported(rig):
    p, panel = rig(page=long_page(), layout_max=1000)
    assert wait_for(lambda: p.thread.counters["too_big"] == 1)
    assert panel.answers[-1] == "AD_CFG_END TOO_BIG 1000"
    assert p.thread.rejected and p.thread.counters["resends"] == 0


def test_a_page_over_8192_bytes_is_never_sent(rig, monkeypatch):
    monkeypatch.setattr(protocol, "LAYOUT_MAX", 500)
    p, panel = rig(page=long_page())
    assert wait_for(lambda: p.thread.rejected)
    assert panel.lines("AD_CFG") == []


# -- live values ---------------------------------------------------------------


def tile(panel, key_id):
    return next(k for k in panel.layout if k["id"] == key_id)


def test_a_changed_value_is_pushed_in_place(rig, database):
    p, panel = rig()
    assert accepted(p, panel)
    database.write("BARO", 30.12)
    assert wait_for(lambda: tile(panel, 1)["gauge"]["t"] == "30.12")
    database.write("HEADBUG", 7.0)
    assert wait_for(lambda: tile(panel, 4)["gauge"]["t"] == "007")
    # Only label, colour and gauge text changed: the panel never rebuilt the
    # grid, so a press waiting for feedback would have survived.
    assert panel.rebuilds == 1


def test_bad_value_shows_dashes_without_a_rebuild(rig, database):
    p, panel = rig()
    assert accepted(p, panel)
    database.get_raw_item("BARO").bad = True
    assert wait_for(lambda: tile(panel, 1)["gauge"]["t"] == BAD_TEXT)
    assert tile(panel, 1)["color"] == "#FF3030"
    assert panel.rebuilds == 1


def test_an_unchanged_value_is_not_pushed_again(rig, database):
    p, panel = rig()
    assert accepted(p, panel)
    for _ in range(5):
        database.write("BARO", 29.92)
    time.sleep(0.4)
    assert len(panel.lines("AD_CFG_BEGIN")) == 1


# -- presses -------------------------------------------------------------------


def test_press_writes_its_fix_key_and_answers_ok(rig, database):
    rec = Recorder()
    database.callback_add("test", "TSBTN12", rec, None)
    p, panel = rig()
    assert accepted(p, panel)
    assert panel.press(3)
    assert wait_for(lambda: panel.feedback == [("ok", 3)])
    assert rec.calls == [("TSBTN12", True), ("TSBTN12", False)]


def test_press_on_a_key_without_an_action_flashes_red(rig, database):
    p, panel = rig()
    assert accepted(p, panel)
    before = database.writers()
    assert panel.press(1)
    assert wait_for(lambda: panel.feedback == [("alert", 1)])
    assert database.writers() == before


def test_long_press_writes_nothing(rig, database):
    rec = Recorder()
    database.callback_add("test", "TSBTN12", rec, None)
    p, panel = rig()
    assert accepted(p, panel)
    panel.press(3, hold=True)
    assert wait_for(lambda: panel.feedback == [("alert", 3)])
    assert rec.calls == []


def test_press_before_the_panel_accepted_the_page_writes_nothing(rig, database):
    """The panel shows the layout it saved until it accepts ours: a press on
    it must not run our page's action for that id."""
    rec = Recorder()
    database.callback_add("test", "TSBTN12", rec, None)
    p, panel = rig()
    panel.ignore_cfg_end = 99
    panel.layout = [{"id": 3, "label": "SAVED"}]
    assert wait_for(lambda: p.thread.connected)
    panel.press(3)
    assert wait_for(lambda: panel.feedback == [("alert", 3)])
    assert rec.calls == []


def test_a_press_writes_no_key_the_page_file_does_not_name(rig, database):
    """DoD 2: press every key id the panel can report, on the page or not."""
    page = Page.load(SHIPPED_PAGE)
    p, panel = rig()
    assert accepted(p, panel)
    before = {k: list(v) for k, v in database.writers().items()}
    for key_id in range(1, protocol.MAX_KEYS + 1):
        if not panel.press(key_id):
            # Not on the page: the firmware would send nothing, so inject the
            # event a confused panel might send anyway.
            panel.send(json.dumps({"type": "deck_event", "id": 900 + key_id, "button": key_id, "label": "X"}))
    assert wait_for(lambda: len(panel.feedback) == protocol.MAX_KEYS)
    written = {k for k, v in database.writers().items() if "tessera" in v and "tessera" not in before.get(k, [])}
    assert written == set(page.written_keys())
    assert written <= set(page.named_keys())
    assert sorted(i for kind, i in panel.feedback if kind == "ok") == [k.id for k in page.keys if k.press]


# -- reconnect -----------------------------------------------------------------


def test_reconnects_after_an_unplug_and_pushes_the_page_again(rig, database):
    rec = Recorder()
    database.callback_add("test", "TSBTN129", rec, None)
    p, panel = rig()
    assert accepted(p, panel)
    panel.unplug()
    assert wait_for(lambda: not p.thread.connected)
    time.sleep(0.3)  # unplugged for a few reconnect attempts
    panel.plug()
    assert wait_for(lambda: p.thread.counters["connects"] == 2)
    assert accepted(p, panel, 2)
    panel.press(5)
    assert wait_for(lambda: ("ok", 5) in panel.feedback)
    assert rec.calls == [("TSBTN129", True), ("TSBTN129", False)]


def test_a_new_boot_id_on_reconnect_is_a_panel_restart(rig):
    p, panel = rig()
    assert accepted(p, panel)
    panel.reboot(boot_id=5678, reset="panic")
    assert wait_for(lambda: p.thread.boot_id == 5678)
    assert p.thread.counters["restarts"] == 1
    assert accepted(p, panel, 2)


def test_a_restart_behind_an_open_port_is_caught_by_get_info(rig):
    p, panel = rig(config={"identify_interval": 0.3})
    assert accepted(p, panel)
    panel.reboot(boot_id=42, replug=False)
    assert wait_for(lambda: p.thread.boot_id == 42)
    assert p.thread.counters["restarts"] == 1
    assert p.thread.counters["connects"] == 1
    assert accepted(p, panel, 2)  # the page went again: the panel had reverted


def test_a_panel_that_stops_answering_is_reconnected(rig):
    p, panel = rig(config={"identify_interval": 0.3})
    assert accepted(p, panel)
    panel.silent = True
    assert wait_for(lambda: "stopped answering" in p.thread.last_error, 4)
    panel.silent = False
    assert wait_for(lambda: p.thread.counters["connects"] >= 2 and p.thread.connected, 4)


# -- the page file -------------------------------------------------------------


def test_shipped_page_names_only_keys_in_the_database(database):
    page = Page.load(SHIPPED_PAGE)
    for key in page.named_keys():
        database.get_raw_item(key)
    for key in page.pulsed_keys():
        assert database.get_raw_item(key).typestring == "bool"
    assert set(page.shown_keys()) == {"BARO", "HEADBUG", "COURSE", "MAPRANGE"}


def test_plugin_refuses_a_page_naming_a_missing_key(database, tmp_path):
    path = tmp_path / "page.yaml"
    path.write_text(yaml.safe_dump({"keys": [{"id": 1, "label": "X", "show": "NOSUCHKEY"}]}))
    with pytest.raises(plugin_base.PluginFail):
        tessera.Plugin("tessera", {"port": "/dev/null", "page": str(path)}, {})


def test_plugin_refuses_a_pulse_on_a_non_bool_key(database, tmp_path):
    path = tmp_path / "page.yaml"
    path.write_text(yaml.safe_dump({"keys": [{"id": 1, "label": "X", "press": {"pulse": "HEADBUG"}}]}))
    with pytest.raises(plugin_base.PluginFail):
        tessera.Plugin("tessera", {"port": "/dev/null", "page": str(path)}, {})


def test_page_path_is_relative_to_the_config_directory(database):
    p = tessera.Plugin("tessera", {"port": "/dev/null", "CONFIGPATH": os.path.abspath("src/fixgw/config")}, {})
    assert p.page.named_keys() == Page.load(SHIPPED_PAGE).named_keys()


@pytest.mark.parametrize(
    "keys",
    [
        [{"id": 0, "label": "X"}],
        [{"id": 19, "label": "X"}],
        [{"id": 1, "label": ""}],
        [{"id": 1, "label": "X" * 25}],
        [{"id": 1, "label": "X"}, {"id": 1, "label": "Y"}],
        [{"id": 1, "label": "X", "show": "BARO", "text": "Y"}],
        [{"id": 1, "label": "X", "press": {"pulse": "A", "set": "B"}}],
        [{"id": 1, "label": "X", "icon": "ok"}],
    ],
    ids=["id-0", "id-19", "empty-label", "long-label", "duplicate", "show-and-text", "two-actions", "icon"],
)
def test_page_file_errors(keys):
    with pytest.raises(PageError):
        Page({"keys": keys})


# -- question 1: what a full page costs ----------------------------------------


def full_page():
    """18 keys at the firmware's limits: 24-character labels, 7-character values."""
    return Page({"keys": [{"id": i, "label": "L" * protocol.LABEL_MAX, "show": "BARO"} for i in range(1, 19)]})


def test_full_page_size_and_wire_time():
    """The numbers doc/plugins/tessera.md quotes.  Through a real 8N1 UART at
    115200 baud the full page takes about 0.15 s to send.  The panel's USB
    Serial/JTAG port ignores the baud rate, so this is an upper bound; TESS2
    measures the real one."""
    read = lambda key: (123.4567, False, False, False, False, False)  # noqa: E731
    payload = protocol.layout_payload(full_page().buttons(read))
    wire = sum(len(line) + 1 for line in protocol.layout_lines(payload))
    assert len(payload) <= protocol.LAYOUT_MAX
    assert 1700 <= wire <= 1850, wire
    assert 0.14 <= protocol.wire_seconds(wire) <= 0.17
