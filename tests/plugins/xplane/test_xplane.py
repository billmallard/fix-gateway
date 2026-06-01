"""Unit tests for the X-Plane fix-gateway plugin.

Exercises the data-driven recv/send mappings, packet parsing, and the
legacy ``idxN:`` config form. Real UDP sockets are mocked via patches
on ``socket.socket`` so the tests don't bind any ports.
"""

import struct
from unittest.mock import MagicMock, patch, call

import pytest

from fixgw.plugins.xplane import (
    HEADER,
    NO_VALUE,
    MainThread,
    Plugin,
    _build_index_map,
    _normalise_slot,
    _parse_slot_list,
)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_normalise_slot_handles_skip_aliases():
    for empty in (None, "", "_", "x", "X", " _ ", "  "):
        assert _normalise_slot(empty) is None
    assert _normalise_slot("ias") == "IAS"
    assert _normalise_slot("LAT") == "LAT"
    assert _normalise_slot(" Long ") == "LONG"


def test_parse_slot_list_list_form_pads_to_eight():
    slots = _parse_slot_list(["PITCH", "ROLL", "_", "HEAD"])
    assert slots == ["PITCH", "ROLL", None, "HEAD", None, None, None, None]


def test_parse_slot_list_legacy_comma_form():
    slots = _parse_slot_list("IAS,X,X,TAS,X,X,X,X")
    assert slots == ["IAS", None, None, "TAS", None, None, None, None]


def test_parse_slot_list_truncates_to_eight():
    extra = list("ABCDEFGHIJ")  # 10 entries
    slots = _parse_slot_list(extra)
    assert len(slots) == 8
    assert slots == [c.upper() for c in "ABCDEFGH"]


def test_build_index_map_str_and_int_keys():
    m = _build_index_map({
        20: ["LAT", "LONG", "ALT", "AGL", "_", "_", "_", "_"],
        "17": "PITCH,ROLL,_,HEAD,_,_,_,_",
        "not-an-index": ["IAS"],
    })
    assert set(m.keys()) == {17, 20}
    assert m[20] == ["LAT", "LONG", "ALT", "AGL", None, None, None, None]
    assert m[17] == ["PITCH", "ROLL", None, "HEAD", None, None, None, None]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_parent():
    parent = MagicMock()
    parent.config = {
        "ipaddress": "10.0.0.99",
        "udp_in": 49001,
        "udp_out": 49002,
        "recv": {
            3:  ["IAS", "_", "TAS", "GS", "_", "_", "_", "_"],
            17: ["PITCH", "ROLL", "_", "HEAD", "_", "_", "_", "_"],
            20: ["LAT", "LONG", "ALT", "AGL", "_", "_", "_", "_"],
        },
        "send": {
            25: ["THR1", "_", "_", "_", "_", "_", "_", "_"],
        },
    }
    parent.log = MagicMock()
    parent.db_write = MagicMock()
    # db_read returns (value, ...) tuples in the netfix client; some plugin
    # builds return scalars. The plugin handles both.
    parent.db_read = MagicMock(return_value=(0.75,))
    return parent


@pytest.fixture
def main_thread(mock_parent):
    with patch("socket.socket"):
        return MainThread(mock_parent)


# ---------------------------------------------------------------------------
# writedata — X-Plane -> FIX
# ---------------------------------------------------------------------------

def test_writedata_speeds(mock_parent, main_thread):
    main_thread.writedata(3, [105.0, 102.0, 110.0, 95.0, 0, 0, 0, 0])
    mock_parent.db_write.assert_has_calls([
        call("IAS", 105.0),
        call("TAS", 110.0),
        call("GS", 95.0),
    ], any_order=True)
    # "_" slots must not produce writes
    written_keys = [c.args[0] for c in mock_parent.db_write.call_args_list]
    assert "X" not in written_keys
    assert "_" not in written_keys


def test_writedata_attitude(mock_parent, main_thread):
    main_thread.writedata(17, [-2.5, 8.3, 271.0, 273.0, 0, 0, 0, 0])
    mock_parent.db_write.assert_has_calls([
        call("PITCH", -2.5),
        call("ROLL", 8.3),
        call("HEAD", 273.0),
    ], any_order=True)


def test_writedata_position(mock_parent, main_thread):
    main_thread.writedata(20, [34.42, -119.85, 12000.0, 11500.0, 0, 0, 0, 0])
    mock_parent.db_write.assert_has_calls([
        call("LAT", 34.42),
        call("LONG", -119.85),
        call("ALT", 12000.0),
        call("AGL", 11500.0),
    ], any_order=True)


def test_writedata_unmapped_index_is_silent(mock_parent, main_thread):
    main_thread.writedata(99, [0.0] * 8)
    mock_parent.db_write.assert_not_called()
    mock_parent.log.debug.assert_called_with("xplane: unmapped index %d", 99)


def test_writedata_filters_no_value_sentinel(mock_parent, main_thread):
    """X-Plane fills slots that aren't enabled in the data-output
    panel with -999.0. The plugin must NOT propagate that value to the
    FIX database — it would silently turn downstream consumers (e.g.
    ``HEAD - MAGVAR`` correction in pyEfis) into nonsense."""
    main_thread.writedata(20, [-999.0, -999.0, -999.0, -999.0, 0, 0, 0, 0])
    # No db_write call should have fired for the four sentinel slots
    # (the remaining four are mapped to "_" so they wouldn't write anyway).
    mock_parent.db_write.assert_not_called()


def test_writedata_filters_sentinel_per_slot(mock_parent, main_thread):
    """A mixed row with some valid floats and some -999 sentinels must
    publish only the valid slots."""
    # idx 20 slots: [LAT, LONG, ALT, AGL, _, _, _, _]
    main_thread.writedata(20, [34.5, -999.0, 1000.0, -999.0, 0, 0, 0, 0])
    keys_written = sorted(c.args[0] for c in mock_parent.db_write.call_args_list)
    assert keys_written == ["ALT", "LAT"]
    # And the values came through unmodified
    by_key = {c.args[0]: c.args[1] for c in mock_parent.db_write.call_args_list}
    assert by_key["LAT"] == 34.5
    assert by_key["ALT"] == 1000.0


def test_writedata_db_failure_is_logged_not_raised(mock_parent, main_thread):
    mock_parent.db_write.side_effect = RuntimeError("no such key")
    # Should not propagate; the loop must keep running across bad keys.
    main_thread.writedata(20, [34.0, -120.0, 1000.0, 800.0, 0, 0, 0, 0])
    assert mock_parent.log.warning.called


# ---------------------------------------------------------------------------
# Packet parsing
# ---------------------------------------------------------------------------

def _make_packet(rows):
    """Build a well-formed X-Plane DATA packet from (index, [8 floats])."""
    buf = HEADER + b"\x00"
    for idx, floats in rows:
        pad = list(floats) + [0.0] * (8 - len(floats))
        buf += struct.pack("<i", idx) + struct.pack("<8f", *pad)
    return buf


def test_ingest_packet_dispatches_each_row(mock_parent, main_thread):
    pkt = _make_packet([
        (3,  [120.0, 0, 130.0, 110.0, 0, 0, 0, 0]),
        (17, [1.5, -2.0, 270.0, 272.0, 0, 0, 0, 0]),
    ])
    main_thread._ingest_packet(pkt)
    keys = sorted(c.args[0] for c in mock_parent.db_write.call_args_list)
    assert "IAS" in keys and "TAS" in keys and "GS" in keys
    assert "PITCH" in keys and "ROLL" in keys and "HEAD" in keys


def test_ingest_packet_rejects_bad_header(mock_parent, main_thread):
    main_thread._ingest_packet(b"XXXX\x00" + b"\x00" * 36)
    mock_parent.db_write.assert_not_called()
    assert mock_parent.log.error.called


def test_ingest_packet_rejects_bad_length(mock_parent, main_thread):
    # 5-byte header + 17 body bytes (not a 36-byte multiple)
    main_thread._ingest_packet(HEADER + b"\x00" + b"\x00" * 17)
    mock_parent.db_write.assert_not_called()
    assert mock_parent.log.error.called


# ---------------------------------------------------------------------------
# senddata — FIX -> X-Plane
# ---------------------------------------------------------------------------

def test_senddata_packs_one_row_per_index(mock_parent, main_thread):
    sent = []
    main_thread.sock.sendto = MagicMock(side_effect=lambda buf, addr: sent.append((buf, addr)))
    main_thread.senddata()
    assert len(sent) == 1
    buf, addr = sent[0]
    assert addr == ("10.0.0.99", 49002)
    assert buf[:4] == HEADER
    # idx i32 + 8 floats
    idx = struct.unpack_from("<i", buf, HEADER_LEN := 5)[0]
    assert idx == 25
    # slot 0 holds THR1; mock_parent.db_read returned (0.75,)
    slot0 = struct.unpack_from("<f", buf, 5 + 4)[0]
    assert slot0 == pytest.approx(0.75)
    # slots 1..7 must be the NO_VALUE sentinel
    for i in range(1, 8):
        s = buf[5 + 4 + i * 4: 5 + 4 + (i + 1) * 4]
        assert s == NO_VALUE


def test_senddata_falls_back_to_zero_on_db_read_error(mock_parent, main_thread):
    mock_parent.db_read.side_effect = RuntimeError("unknown key")
    sent = []
    main_thread.sock.sendto = MagicMock(side_effect=lambda buf, addr: sent.append(buf))
    main_thread.senddata()
    slot0 = struct.unpack_from("<f", sent[0], 9)[0]
    assert slot0 == 0.0


# ---------------------------------------------------------------------------
# Legacy idxN config form (kept for backwards compatibility)
# ---------------------------------------------------------------------------

def test_legacy_idx_keys_become_send_map():
    parent = MagicMock()
    parent.config = {
        "ipaddress": "127.0.0.1",
        "udp_in": 49001,
        "udp_out": 49002,
        "idx25": "THR1, THR2, x, x, x, x, x, x",
        "idx29": "MIX1, _, _, _, _, _, _, _",
    }
    parent.log = MagicMock()
    parent.db_read = MagicMock(return_value=(0.5,))
    with patch("socket.socket"):
        t = MainThread(parent)
    assert set(t.send_map.keys()) == {25, 29}
    assert t.send_map[25][:2] == ["THR1", "THR2"]
    assert t.send_map[29] == ["MIX1", None, None, None, None, None, None, None]
    # recv_map left empty when no `recv:` block is provided.
    assert t.recv_map == {}


# ---------------------------------------------------------------------------
# Plugin lifecycle
# ---------------------------------------------------------------------------

def test_plugin_lifecycle(mock_parent):
    with patch("socket.socket"):
        plugin_instance = Plugin("xplane", mock_parent.config, MagicMock())
    with patch.object(plugin_instance.thread, "start") as mock_start, \
            patch.object(plugin_instance.thread, "stop") as mock_stop, \
            patch.object(plugin_instance.thread, "is_alive", return_value=False):
        plugin_instance.run()
        mock_start.assert_called_once()
        plugin_instance.stop()
        mock_stop.assert_called_once()
