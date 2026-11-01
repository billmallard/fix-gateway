"""Tests for the FlightGear (fgfs) fix-gateway plugin.

The protocol tests drive the SHIPPED fix_fgfs.xml through an emulator of
FlightGear's generic protocol writer (src/Network/generic.cxx: each field is
``offset + value * factor`` printed with the chunk's format, joined by the
var separator, ended by the line separator), then through the plugin into a
real fix-gateway database. Socket tests use real UDP on 127.0.0.1 with
ephemeral ports.
"""

import os
import socket
import sys
import time
import types
import xml.etree.ElementTree as ET

import pytest
import yaml

import fixgw.plugin as plugin_base
import fixgw.plugins.fgfs as fgfs

CONFIG_DIR = os.path.join("src", "fixgw", "config", "connections")
SHIPPED_XML = os.path.join(CONFIG_DIR, "fgfs", "fix_fgfs.xml")


# ---------------------------------------------------------------------------
# FlightGear generic-protocol emulator
# ---------------------------------------------------------------------------

def fg_sentence(props, path=SHIPPED_XML, section="output"):
    """Build the sentence FlightGear would send for ``props`` {node: value}.
    A node not in ``props`` reads 0, as an unset FlightGear property does."""
    gen = ET.parse(path).getroot().find("generic").find(section)
    seps = fgfs.NAMED_SEPARATORS
    var_sep = seps.get(gen.findtext("var_separator"), gen.findtext("var_separator"))
    line_sep = seps.get(gen.findtext("line_separator"), gen.findtext("line_separator"))
    fields = []
    for chunk in gen.findall("chunk"):
        node = chunk.findtext("node")
        factor = float(chunk.findtext("factor") or 1.0)
        offset = float(chunk.findtext("offset") or 0.0)
        val = offset + float(props.get(node, 0.0)) * factor
        fmt = chunk.findtext("format")
        if chunk.findtext("type") in ("int", "integer", "byte", "word"):
            val = int(val)
        fields.append(fmt % val)
    return var_sep.join(fields) + line_sep


class Parent:
    """Minimal plugin parent over a real database."""

    def __init__(self, db, config=None):
        self.db = db
        self.config = config or {}
        self.log = plugin_base.logging.getLogger("test.fgfs")

    def db_get_item(self, key):
        return self.db.get_raw_item(key)

    def db_read(self, key):
        return self.db.read(key)

    def db_write(self, key, value):
        self.db.write(key, value)


_open_threads = []


@pytest.fixture(autouse=True)
def close_threads():
    yield
    while _open_threads:
        _open_threads.pop().close()


def make_thread(db, config=None, path=SHIPPED_XML):
    cfg = {"recv_host": "127.0.0.1", "recv_port": 0, "send_host": "127.0.0.1"}
    cfg.update(config or {})
    output, input_ = fgfs.parse_protocol_file(path)
    t = fgfs.MainThread(Parent(db, cfg), output, input_)
    t.resolve_keys()
    _open_threads.append(t)
    return t


def value(db, key):
    return db.read(key)[0]


# ---------------------------------------------------------------------------
# Protocol file parsing
# ---------------------------------------------------------------------------

def write_protocol(path, body, root_tag="PropertyList"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<{0}><generic>{1}</generic></{0}>".format(root_tag, body), encoding="utf-8")
    return str(path)


def test_parse_reads_keys_formats_and_named_separators(tmp_path):
    p = write_protocol(tmp_path / "p.xml", """
      <output>
        <line_separator>newline</line_separator>
        <var_separator>tab</var_separator>
        <chunk><name>alt : altitude</name><format>%.0f</format></chunk>
        <chunk><name>IAS</name></chunk>
        <chunk><format>%.1f</format></chunk>
      </output>
      <input>
        <var_separator>;</var_separator>
        <chunk><name>BARO: setting</name></chunk>
      </input>""")
    out, inp = fgfs.parse_protocol_file(p)
    assert out.keys == ["ALT", "IAS"]          # a chunk with no name is skipped
    assert out.chunks[0].format == "%.0f"
    assert out.chunks[1].format == "%.2f"       # default
    assert out.var_separator == "\t"
    assert out.line_separator == "\n"
    assert inp.keys == ["BARO"]
    assert inp.var_separator == ";"             # literal
    assert inp.line_separator == "\n"           # default


def test_parse_without_input_section_gives_empty_input(tmp_path):
    p = write_protocol(tmp_path / "p.xml", "<output><chunk><name>IAS</name></chunk></output>")
    out, inp = fgfs.parse_protocol_file(p)
    assert out.keys == ["IAS"]
    assert inp.chunks == []


def test_parse_rejects_bad_root_and_binary_mode(tmp_path):
    bad = write_protocol(tmp_path / "bad.xml", "<output/>", root_tag="Wrong")
    with pytest.raises(ValueError, match="Root Tag is not PropertyList"):
        fgfs.parse_protocol_file(bad)
    binary = write_protocol(tmp_path / "bin.xml", "<binary_mode>true</binary_mode><output/>")
    with pytest.raises(ValueError, match="binary_mode"):
        fgfs.parse_protocol_file(binary)


def test_find_protocol_file_search_order(tmp_path, monkeypatch):
    monkeypatch.setattr(fgfs, "PACKAGED_PROTOCOL_DIR", str(tmp_path / "pkg"))
    packaged = write_protocol(tmp_path / "pkg" / "x.xml", "<output/>")
    assert fgfs.find_protocol_file(str(tmp_path / "root"), "x.xml") == packaged
    in_root = write_protocol(tmp_path / "root" / "x.xml", "<output/>")
    assert fgfs.find_protocol_file(str(tmp_path / "root"), "x.xml") == in_root
    in_protocol = write_protocol(tmp_path / "root" / "Protocol" / "x.xml", "<output/>")
    assert fgfs.find_protocol_file(str(tmp_path / "root"), "x.xml") == in_protocol
    with pytest.raises(FileNotFoundError):
        fgfs.find_protocol_file(str(tmp_path / "root"), "missing.xml")


def test_find_protocol_file_expands_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = write_protocol(tmp_path / "fg" / "Protocol" / "x.xml", "<output/>")
    assert fgfs.find_protocol_file("~/fg", "x.xml") == p


def test_packaged_protocol_dir_holds_the_shipped_xml():
    assert os.path.samefile(
        fgfs.find_protocol_file("/nonexistent", "fix_fgfs.xml"), SHIPPED_XML)


# ---------------------------------------------------------------------------
# Sentence decoding
# ---------------------------------------------------------------------------

def section(*keys, sep=","):
    return fgfs.Section([fgfs.Chunk(k) for k in keys], var_separator=sep)


def test_decode_rejects_a_field_count_mismatch():
    s = section("A", "B", "C")
    assert fgfs.decode_sentence("1,2", s) is None
    assert fgfs.decode_sentence("1,2,3,4", s) is None
    assert fgfs.decode_sentence("1,2,3", s) == {"A": 1.0, "B": 2.0, "C": 3.0}


def test_decode_sums_chunks_that_name_the_same_key():
    s = section("BRG", "X", "BRG")
    assert fgfs.decode_sentence("5,7,-13", s) == {"BRG": -8.0, "X": 7.0}


def test_decode_drops_bad_fields_and_every_part_of_their_sum():
    s = section("A", "B", "B", "C")
    assert fgfs.decode_sentence("1,junk,2,nan", s) == {"A": 1.0}
    assert fgfs.decode_sentence("inf,1,2,3", s) == {"B": 3.0, "C": 3.0}


def test_decode_honours_the_var_separator():
    assert fgfs.decode_sentence("1;2", section("A", "B", sep=";")) == {"A": 1.0, "B": 2.0}


# ---------------------------------------------------------------------------
# Shipped XML: key parity with the xplane plugin, units and signs
# ---------------------------------------------------------------------------

def xplane_written_keys():
    with open(os.path.join(CONFIG_DIR, "xplane.yaml")) as f:
        cfg = yaml.safe_load(f)["xplane"]
    keys = set(cfg["datarefs"])
    for slots in cfg["recv"].values():
        keys.update(s for s in slots if s not in (None, "_", "x", ""))
    return keys


def test_shipped_xml_writes_every_key_the_xplane_plugin_writes():
    out, _ = fgfs.parse_protocol_file(SHIPPED_XML)
    missing = xplane_written_keys() - set(out.keys)
    assert not missing, "xplane writes keys fgfs does not: {}".format(sorted(missing))


def test_shipped_xml_keys_are_all_in_the_database(database):
    out, inp = fgfs.parse_protocol_file(SHIPPED_XML)
    missing = []
    for key in out.keys + inp.keys:
        try:
            database.get_raw_item(key)
        except KeyError:
            missing.append(key)
    assert missing == []


def test_shipped_xml_emulated_sentence_round_trips(database):
    t = make_thread(database)
    props = {
        "/instrumentation/airspeed-indicator/indicated-speed-kt": 98.4,
        "/velocities/airspeed-kt": 104.2,
        "/velocities/groundspeed-kt": 91.0,
        "/velocities/vertical-speed-fps": -8.5,          # -> -510 fpm
        "/orientation/pitch-deg": -2.5,
        "/orientation/roll-deg": 15.25,
        "/orientation/heading-magnetic-deg": 347.0,
        "/environment/magnetic-variation-deg": 13.0,     # east 13 -> MAGVAR -13
        "/orientation/yaw-rate-degps": 3.0,
        "/accelerations/pilot/y-accel-fps_sec": 3.2174,  # -> 0.1 g
        "/orientation/alpha-deg": 4.5,
        "/orientation/track-deg": 0.0,
        "/orientation/track-magnetic-deg": 347.0,
        "/orientation/path-deg": -3.0,
        "/position/latitude-deg": 37.5135,
        "/position/longitude-deg": -122.2493,
        "/position/altitude-ft": 2500.4,
        "/position/altitude-agl-ft": 2480.0,
        "/autopilot/settings/heading-bug-deg": 270.0,
        "/instrumentation/gps/desired-course-deg": 300.0,
        "/instrumentation/gps/cdi-deflection": -5.0,     # -> -0.5
        "/instrumentation/gps/wp/wp[1]/to-flag": 1,
        "/instrumentation/gps/wp/wp[1]/from-flag": 0,
        "/sim/time/elapsed-sec": 1234.5,
        "/instrumentation/nav[0]/radials/selected-deg": 110.0,
        "/instrumentation/nav[0]/heading-needle-deflection-norm": 0.25,
        "/instrumentation/nav[0]/gs-needle-deflection-norm": 0.5,
        "/instrumentation/nav[0]/gs-in-range": 1,
        "/instrumentation/nav[0]/in-range": 1,
        "/instrumentation/nav[0]/to-flag": 0,
        "/instrumentation/nav[0]/from-flag": 1,
        "/instrumentation/nav[0]/heading-deg": 5.0,      # true 5, var E13 -> 352 mag
        "/instrumentation/nav[1]/in-range": 0,
        "/instrumentation/nav[1]/gs-in-range": 0,
        "/instrumentation/nav[1]/heading-deg": 200.0,
        "/environment/temperature-degc": 15.0,
        "/engines/engine[0]/rpm": 2400,
        "/engines/engine[0]/oil-temperature-degf": 212.0,  # -> 100 C
        "/engines/engine[0]/egt-degf": 1400.0,
        "/engines/engine[0]/cht-degf": 32.0,                # -> 0 C
    }
    t.save_sentence(fg_sentence(props).rstrip("\n"))
    assert t.msg_bad == 0 and t.msg_recv == 1

    assert value(database, "IAS") == pytest.approx(98.4)
    assert value(database, "TAS") == pytest.approx(104.2)
    assert value(database, "GS") == pytest.approx(91.0)
    assert value(database, "VS") == pytest.approx(-510.0)
    assert value(database, "PITCH") == pytest.approx(-2.5)
    assert value(database, "ROLL") == pytest.approx(15.25)
    assert value(database, "HEAD") == pytest.approx(347.0)
    # FIX: MAG = TRUE + MAGVAR, west positive. True heading 0 at var E13 is
    # magnetic 347, so MAGVAR must be -13.
    assert value(database, "MAGVAR") == pytest.approx(-13.0)
    assert value(database, "ROT") == pytest.approx(3.0)
    assert value(database, "ALAT") == pytest.approx(0.1, abs=1e-3)
    assert value(database, "AOA") == pytest.approx(4.5)
    assert value(database, "TRACK") == pytest.approx(0.0)
    assert value(database, "VPATH") == pytest.approx(-3.0)
    assert value(database, "LAT") == pytest.approx(37.5135)
    assert value(database, "LONG") == pytest.approx(-122.2493)
    assert value(database, "ALT") == pytest.approx(2500.0)
    assert value(database, "AGL") == pytest.approx(2480.0)
    assert value(database, "HEADBUG") == pytest.approx(270.0)
    assert value(database, "EXTCRS") == pytest.approx(300.0)
    assert value(database, "EXTCDI") == pytest.approx(-0.5)
    assert value(database, "EXTTF") == 1                       # TO
    assert value(database, "GPSGSI") == pytest.approx(0.0)
    assert value(database, "GPSGSF") == pytest.approx(1.0)     # no glidepath
    assert value(database, "NAV1CRS") == pytest.approx(110.0)
    assert value(database, "NAV1CDI") == pytest.approx(0.25)
    assert value(database, "NAV1GSI") == pytest.approx(0.5)
    assert value(database, "NAV1GSF") == pytest.approx(0.0)    # GS received
    assert value(database, "NAV1DH") == pytest.approx(1.0)
    assert value(database, "NAV1TF") == pytest.approx(2.0)     # FROM
    assert value(database, "VOR1BRG") == pytest.approx(352.0)  # wrapped, not clamped to 0
    assert value(database, "NAV2GSF") == pytest.approx(1.0)    # no GS
    assert value(database, "NAV2DH") == pytest.approx(0.0)
    assert value(database, "NAV2TF") == pytest.approx(0.0)     # off
    assert value(database, "VOR2BRG") == pytest.approx(187.0)
    assert value(database, "OAT") == pytest.approx(15.0)
    assert value(database, "TACH1") == pytest.approx(2400.0)
    assert value(database, "OILT1") == pytest.approx(100.0, abs=0.1)
    assert value(database, "CHT11") == pytest.approx(0.0, abs=0.1)


def test_mismatched_sentence_is_dropped_whole(database):
    t = make_thread(database)
    database.write("IAS", 50.0)
    good = fg_sentence({"/instrumentation/airspeed-indicator/indicated-speed-kt": 120.0})
    short = good.rstrip("\n").rsplit(",", 1)[0]          # one field missing
    t.save_sentence(short)
    assert t.msg_bad == 1 and t.msg_recv == 0
    assert value(database, "IAS") == pytest.approx(50.0)  # nothing written


def test_ingest_reassembles_sentences_split_across_datagrams(database):
    t = make_thread(database)
    s = fg_sentence({"/orientation/roll-deg": 30.0}).encode()
    t.ingest(s[:20])
    assert t.msg_recv == 0
    t.ingest(s[20:] + s)                                  # rest + a whole second one
    assert t.msg_recv == 2
    assert value(database, "ROLL") == pytest.approx(30.0)


def test_unknown_receive_keys_are_skipped(database, tmp_path):
    p = write_protocol(tmp_path / "p.xml", """
      <output>
        <chunk><name>NOSUCHKEY</name></chunk>
        <chunk><name>ROLL</name></chunk>
      </output>""")
    t = make_thread(database, path=p)
    t.save_sentence("7,12.5")
    assert value(database, "ROLL") == pytest.approx(12.5)


# ---------------------------------------------------------------------------
# FIX -> FlightGear send path
# ---------------------------------------------------------------------------

def udp_listener():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(0.5)
    return s, s.getsockname()[1]


def run_briefly(t, seconds=0.35):
    t.start()
    time.sleep(seconds)
    t.stop()
    t.join(2.0)
    t.close()


def test_send_is_off_by_default(database):
    listener, port = udp_listener()
    t = make_thread(database, {"send_port": port, "rate": 50})
    assert t.input.keys == ["BARO"]       # the shipped XML has an input chunk
    assert t.send_enabled is False
    run_briefly(t)
    with pytest.raises(socket.timeout):
        listener.recv(1024)
    assert t.msg_sent == 0
    listener.close()


def test_send_true_sends_input_chunks_with_their_format(database):
    listener, port = udp_listener()
    database.write("BARO", 30.12)
    t = make_thread(database, {"send": True, "send_port": port, "rate": 50})
    assert t.send_enabled is True
    run_briefly(t)
    assert listener.recv(1024) == b"30.12\n"
    assert t.msg_sent >= 5                 # ~50 Hz for 0.35 s
    listener.close()


def test_send_turns_itself_off_when_an_input_key_is_unknown(database, tmp_path):
    p = write_protocol(tmp_path / "p.xml", """
      <output><chunk><name>ROLL</name></chunk></output>
      <input><chunk><name>NOSUCHKEY</name></chunk></input>""")
    t = make_thread(database, {"send": True, "send_port": 9}, path=p)
    assert t.send_enabled is False


# ---------------------------------------------------------------------------
# Plugin lifecycle, end to end over UDP
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_server(monkeypatch):
    mod = types.ModuleType("fixgw.server")
    mod.plugins = {}
    monkeypatch.setitem(sys.modules, "fixgw.server", mod)
    return mod


def make_plugin(config=None):
    cfg = {"fg_root": "/nonexistent", "xml_file": "fix_fgfs.xml",
           "recv_host": "127.0.0.1", "recv_port": 0}
    cfg.update(config or {})
    return fgfs.Plugin("fgfs", cfg, {})


def test_plugin_ingests_a_real_udp_sentence(database, fake_server):
    p = make_plugin()
    fake_server.plugins["fgfs"] = p
    p.start()
    try:
        port = p.thread.sock.getsockname()[1]
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(fg_sentence({"/orientation/pitch-deg": 7.5}).encode(), ("127.0.0.1", port))
        tx.close()
        deadline = time.monotonic() + 2.0
        while p.thread.msg_recv == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert value(database, "PITCH") == pytest.approx(7.5)
        status = p.get_status()
        assert status["Sending to"] == "disabled"
        assert status["Messages"]["Received"] == 1
        assert os.path.samefile(status["Protocol file"], SHIPPED_XML)
    finally:
        p.shutdown()
    assert not p.thread.is_alive()


@pytest.mark.parametrize("module", ["fixgw.plugins.xplane", "fixgw.plugins.demo"])
def test_plugin_refuses_to_start_beside_another_sim_source(database, fake_server, module):
    other = type("Plugin", (), {"__module__": module})()
    p = make_plugin()
    fake_server.plugins.update({"sim": other, "fgfs": p})
    with pytest.raises(plugin_base.PluginFail, match="sim also loaded"):
        p.start()
    assert p.thread is None and not p.is_running()


def test_plugin_starts_beside_unrelated_plugins(database, fake_server):
    other = type("Plugin", (), {"__module__": "fixgw.plugins.netfix"})()
    p = make_plugin()
    fake_server.plugins.update({"netfix": other, "fgfs": p})
    p.start()
    try:
        assert p.is_running()
    finally:
        p.shutdown()


def test_plugin_fails_loudly_on_a_missing_protocol_file(database, fake_server, monkeypatch):
    monkeypatch.setattr(fgfs, "PACKAGED_PROTOCOL_DIR", "/nonexistent")
    p = make_plugin({"xml_file": "missing.xml"})
    with pytest.raises(plugin_base.PluginFail, match="missing.xml"):
        p.start()
    assert not p.is_running()
    p.shutdown()                            # stop with no thread is a no-op


def test_shipped_connection_config_is_receive_only_and_loads_the_shipped_xml():
    with open(os.path.join(CONFIG_DIR, "flight_gear_simulator.yaml")) as f:
        cfg = yaml.safe_load(f)["fgfs"]
    assert cfg["module"] == "fixgw.plugins.fgfs"
    assert cfg.get("send", False) is False
    assert cfg["xml_file"] == "fix_fgfs.xml"
