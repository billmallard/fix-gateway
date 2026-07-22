#  Copyright (c) 2026 MakerPlane contributors
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
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

# Unit tests for the CANaerospace mapfile loader and the decoded-message
# to FIX-database dispatch (scale/offset/converters, node priority,
# strict_types, ERROR flagging, validation errors).

import pytest

import fixgw.plugins.canaerospace.mapping as canas_mapping
from fixgw.plugins.canaerospace import protocol


class FakeLog:
    def __init__(self):
        self.debugs = []
        self.warnings = []

    def debug(self, message):
        self.debugs.append(message)

    def warning(self, message):
        self.warnings.append(message)


def make_mapfile(tmp_path, text):
    p = tmp_path / "map.yaml"
    p.write_text(text)
    return str(p)


def make_mapping(tmp_path, text, log=None, node_timeout=2.0):
    return canas_mapping.Mapping(
        make_mapfile(tmp_path, text), log or FakeLog(), node_timeout
    )


def msg(canid, dtype, values, node=16, code=0):
    return protocol.CanasMessage(
        canid=canid,
        node_id=node,
        data_type=dtype,
        service_code=0,
        message_code=code,
        values=tuple(values),
        channel=protocol.channel_for(canid),
    )


def test_scale_offset_write(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: TACH1
    scale: 0.5
    offset: 100.0
""",
    )
    m.inputMap(msg(300, protocol.USHORT, [1000]))
    assert database.read("TACH1")[0] == 600
    assert m.write_count == 1
    assert m.interesting[300] is True
    assert m.interesting[301] is False


def test_converters(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: OILT1
    converter: k2c
  - canid: 301
    fixid: OILP1
    converter: kpa2psi
  - canid: 302
    fixid: H2OT1
    converter: pct
""",
    )
    m.inputMap(msg(300, protocol.FLOAT, [373.15]))
    m.inputMap(msg(301, protocol.FLOAT, [300.0]))
    m.inputMap(msg(302, protocol.FLOAT, [0.85]))
    assert database.read("OILT1")[0] == pytest.approx(100.0, abs=0.01)
    assert database.read("OILP1")[0] == pytest.approx(43.51, abs=0.01)
    assert database.read("H2OT1")[0] == pytest.approx(85.0, abs=0.01)


def test_multi_element_frame_feeds_multiple_fixids(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: EGT11
    element: 0
    scale: 10.0
  - canid: 300
    fixid: EGT12
    element: 1
    scale: 10.0
""",
    )
    m.inputMap(msg(300, protocol.UCHAR4, [78, 80, 0, 0]))
    assert database.read("EGT11")[0] == pytest.approx(780.0)
    assert database.read("EGT12")[0] == pytest.approx(800.0)
    assert m.write_count == 2


def test_element_beyond_frame_values(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: EGT13
    element: 2
""",
    )
    # USHORT only carries one value; element 2 cannot be extracted
    m.inputMap(msg(300, protocol.USHORT, [500]))
    assert m.recvinvalidcount == 1
    assert m.write_count == 0


def test_node_priority_failover(tmp_path, database, monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(canas_mapping.time, "monotonic", lambda: now["t"])
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: OILT1
    nodes: [16, 17]
""",
        node_timeout=2.0,
    )
    # Lane B accepted while lane A has never been seen
    m.inputMap(msg(300, protocol.FLOAT, [50.0], node=17))
    assert database.read("OILT1")[0] == pytest.approx(50.0)
    # Lane A takes over
    m.inputMap(msg(300, protocol.FLOAT, [60.0], node=16))
    assert database.read("OILT1")[0] == pytest.approx(60.0)
    # Lane B blocked while lane A is fresh
    now["t"] += 1.0
    m.inputMap(msg(300, protocol.FLOAT, [70.0], node=17))
    assert database.read("OILT1")[0] == pytest.approx(60.0)
    assert m.recvignorecount == 1
    # Lane A stale -> lane B accepted again
    now["t"] += 2.1
    m.inputMap(msg(300, protocol.FLOAT, [80.0], node=17))
    assert database.read("OILT1")[0] == pytest.approx(80.0)
    # A sender not in the list is ignored
    m.inputMap(msg(300, protocol.FLOAT, [90.0], node=99))
    assert database.read("OILT1")[0] == pytest.approx(80.0)
    assert m.recvignorecount == 2


def test_type_mismatch_counted_but_decoded(tmp_path, database):
    log = FakeLog()
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: TACH1
    type: USHORT
""",
        log=log,
    )
    # Wire says SHORT, mapfile expects USHORT: counted, still decoded
    m.inputMap(msg(300, protocol.SHORT, [2650]))
    assert m.type_mismatch_count == 1
    assert database.read("TACH1")[0] == 2650
    assert len(log.warnings) == 1
    # Second mismatch on the same canid is rate-limited to debug
    m.inputMap(msg(300, protocol.SHORT, [2700]))
    assert len(log.warnings) == 1
    assert m.type_mismatch_count == 2


def test_strict_types_drops_mismatch(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
strict_types: true
inputs:
  - canid: 300
    fixid: TACH1
    type: USHORT
""",
    )
    m.inputMap(msg(300, protocol.SHORT, [2650]))
    assert m.type_mismatch_count == 1
    assert m.write_count == 0
    assert database.read("TACH1")[0] == 0


def test_error_frame_sets_fail_until_next_good_value(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: OILP1
""",
    )
    m.inputMap(msg(300, protocol.ERROR, [0xDEADBEEF]))
    # value tuple: (value, annunciate, old, bad, fail, secfail)
    assert database.read("OILP1")[4] is True
    assert m.error_frame_count == 1
    m.inputMap(msg(300, protocol.FLOAT, [55.0]))
    assert database.read("OILP1")[0] == pytest.approx(55.0)
    assert database.read("OILP1")[4] is False


def test_events(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
events:
  - canid: 16
    fixid: BTN1
    match_code: null
  - canid: 17
    fixid: BTN2
    match_code: 305419896
""",
    )
    assert database.read("BTN1")[0] is False
    m.eventMap(16, 1)
    assert database.read("BTN1")[0] is True
    assert m.event_count == 1
    # match_code mismatch does not fire
    m.eventMap(17, 0x11111111)
    assert database.read("BTN2")[0] is False
    m.eventMap(17, 0x12345678)
    assert database.read("BTN2")[0] is True
    # unmapped event canid is a no-op
    m.eventMap(99, None)


def test_unmapped_canid_ignored(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - canid: 300
    fixid: TACH1
""",
    )
    m.inputMap(msg(301, protocol.USHORT, [1]))
    assert m.recvignorecount == 1
    assert m.write_count == 0


def test_missing_mapfile_raises():
    with pytest.raises(ValueError):
        canas_mapping.Mapping("no_such_mapfile.yaml", FakeLog())


def test_shipped_rotax_skeleton_loads(database):
    m = canas_mapping.Mapping(
        "src/fixgw/config/canaerospace/rotax_is_map.yaml", FakeLog()
    )
    assert not m.input_map
    assert not m.event_map
    assert not any(m.interesting)


@pytest.mark.parametrize(
    "yaml_text",
    [
        # canid missing
        """
inputs:
  - fixid: TACH1
""",
        # canid out of range
        """
inputs:
  - canid: 2032
    fixid: TACH1
""",
        # canid not an int
        """
inputs:
  - canid: 'x'
    fixid: TACH1
""",
        # unknown fixid
        """
inputs:
  - canid: 300
    fixid: NOSUCHKEY
""",
        # duplicate (canid, element)
        """
inputs:
  - canid: 300
    fixid: TACH1
  - canid: 300
    fixid: PROP1
""",
        # bad element
        """
inputs:
  - canid: 300
    fixid: TACH1
    element: 4
""",
        # bad converter
        """
inputs:
  - canid: 300
    fixid: TACH1
    converter: furlongs
""",
        # bad type name
        """
inputs:
  - canid: 300
    fixid: TACH1
    type: MEMID
""",
        # nodes not a list
        """
inputs:
  - canid: 300
    fixid: TACH1
    nodes: 16
""",
        # strict_types not a bool
        """
strict_types: 'yes'
inputs: []
""",
        # ignore_fixid_missing not a bool
        """
ignore_fixid_missing: 1
inputs: []
""",
        # event canid outside EED range
        """
events:
  - canid: 300
    fixid: BTN1
""",
        # event match_code not an int
        """
events:
  - canid: 16
    fixid: BTN1
    match_code: 'x'
""",
    ],
)
def test_validation_errors(tmp_path, database, yaml_text):
    with pytest.raises(ValueError):
        make_mapping(tmp_path, yaml_text)


def test_ignore_fixid_missing_skips_entry(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
ignore_fixid_missing: true
inputs:
  - canid: 300
    fixid: NOSUCHKEY
  - canid: 301
    fixid: TACH1
""",
    )
    # The unknown fixid is inert, the good one still works
    assert m.interesting[300] is False
    assert m.interesting[301] is True
    m.inputMap(msg(301, protocol.USHORT, [1200]))
    assert database.read("TACH1")[0] == 1200


def test_unknown_top_level_key_warns_not_fails(tmp_path, database):
    log = FakeLog()
    make_mapping(
        tmp_path,
        """
bogus_key: 1
inputs:
  - canid: 300
    fixid: TACH1
""",
        log=log,
    )
    assert any("bogus_key" in w for w in log.warnings)
