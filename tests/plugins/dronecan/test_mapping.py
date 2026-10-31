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

# Unit tests for the DroneCAN mapfile loader and the decoded-message to
# FIX-database dispatch.  Messages are real DSDL objects built by the
# dronecan library, wrapped in a fake event: no bus involved.

from types import SimpleNamespace

import dronecan
import pytest

import fixgw.plugins.dronecan.mapping as dc_mapping

MAG2 = dronecan.uavcan.equipment.ahrs.MagneticFieldStrength2
CIRCUIT = dronecan.uavcan.equipment.power.CircuitStatus
STATIC_T = dronecan.uavcan.equipment.air_data.StaticTemperature

MAG_MAP = """
inputs:
  - message: uavcan.equipment.ahrs.MagneticFieldStrength2
    where: { sensor_id: 0 }
    fields:
      - { path: "magnetic_field_ga[0]", fixid: MAGX }
      - { path: "magnetic_field_ga[1]", fixid: MAGY }
      - { path: "magnetic_field_ga[2]", fixid: MAGZ }
"""


class FakeLog:
    def __init__(self):
        self.debugs = []
        self.warnings = []
        self.infos = []

    def debug(self, message):
        self.debugs.append(message)

    def info(self, message):
        self.infos.append(message)

    def warning(self, message):
        self.warnings.append(message)


def make_mapping(tmp_path, text, log=None):
    p = tmp_path / "map.yaml"
    p.write_text(text)
    return dc_mapping.Mapping(str(p), log or FakeLog())


def event(msg, node=42):
    return SimpleNamespace(message=msg, transfer=SimpleNamespace(source_node_id=node))


def deliver(m, msg, node=42):
    """Run the registered handler for msg's type, as node.spin() would."""
    name = dronecan.get_dronecan_data_type(msg).full_name
    m.handler_for(name)(event(msg, node))


def load_error(tmp_path, text, database=None):
    with pytest.raises(ValueError) as e:
        make_mapping(tmp_path, text)
    return str(e.value)


# --- path grammar and schema checks -------------------------------------


@pytest.mark.parametrize(
    "path,steps",
    [
        ("voltage", [("attr", "voltage")]),
        ("magnetic_field_ga[2]", [("attr", "magnetic_field_ga"), ("index", 2)]),
        (
            "timestamp.usec",
            [("attr", "timestamp"), ("attr", "usec")],
        ),
        (
            "ecef_position_velocity[0].velocity_xyz[1]",
            [
                ("attr", "ecef_position_velocity"),
                ("index", 0),
                ("attr", "velocity_xyz"),
                ("index", 1),
            ],
        ),
    ],
)
def test_parse_path(path, steps):
    assert dc_mapping.parse_path(path) == steps


@pytest.mark.parametrize(
    "path", ["", "1abc", "a..b", "a[", "a[x]", "a[-1]", ".a", "a.", "a b", None, 5]
)
def test_parse_path_rejects_bad_syntax(path):
    with pytest.raises(ValueError):
        dc_mapping.parse_path(path)


def test_check_path_terminal_types():
    t = dc_mapping.check_path(MAG2, dc_mapping.parse_path("sensor_id"), "sensor_id")
    assert t.category == t.CATEGORY_PRIMITIVE
    fix2 = dronecan.uavcan.equipment.gnss.Fix2
    dc_mapping.check_path(fix2, dc_mapping.parse_path("timestamp.usec"), "x")


@pytest.mark.parametrize(
    "dtype,path,why",
    [
        (MAG2, "magnetic_field", "has no field"),
        (MAG2, "sensor_id[0]", "non-array"),
        (MAG2, "magnetic_field_ga[3]", "past the array size"),
        (MAG2, "magnetic_field_ga", "numeric"),
        (MAG2, "sensor_id.x", "non-compound"),
        (dronecan.uavcan.equipment.gnss.Fix2, "timestamp", "numeric"),
    ],
)
def test_check_path_rejects_schema_mismatch(dtype, path, why):
    with pytest.raises(ValueError, match=why):
        dc_mapping.check_path(dtype, dc_mapping.parse_path(path), path)


def test_resolve_type():
    assert (
        dc_mapping.resolve_type("uavcan.equipment.ahrs.MagneticFieldStrength2") is MAG2
    )
    assert dc_mapping.resolve_type("uavcan.equipment.ahrs.NoSuchThing") is None
    assert dc_mapping.resolve_type("uavcan.equipment.ahrs") is None  # namespace
    assert dc_mapping.resolve_type("uavcan.protocol.GetNodeInfo") is None  # service
    assert dc_mapping.resolve_type("") is None
    assert dc_mapping.resolve_type(None) is None


# --- mapfile validation -------------------------------------------------


def test_missing_mapfile_raises(database):
    with pytest.raises(ValueError, match="Unable to open"):
        dc_mapping.Mapping("no/such/map.yaml", FakeLog())


def test_empty_mapfile_is_valid(tmp_path, database):
    m = make_mapping(tmp_path, "")
    assert m.inputs == {}
    m = make_mapping(tmp_path, "inputs: []\n")
    assert m.inputs == {}


def test_unknown_message_type(tmp_path, database):
    msg = load_error(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.ahrs.MagFieldStrength9
    fields:
      - { path: x, fixid: VOLT }
""",
    )
    assert "not a DroneCAN message type" in msg
    assert "Line" in msg or "line" in msg


@pytest.mark.parametrize(
    "body,why",
    [
        (
            "    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "'message' is missing",
        ),
        ("    message: uavcan.equipment.power.CircuitStatus\n", "'fields' is missing"),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n    fields: []\n",
            "non-empty list",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    colour: red\n    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "unknown key 'colour'",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    source_node: 0\n    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "source_node",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    source_node: 128\n    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "source_node",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    source_node: true\n    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "source_node",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    where: [circuit_id]\n    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "where must be a dictionary",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    where: { circuit: 0 }\n    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "has no field 'circuit'",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    where: { circuit_id: zero }\n"
            "    fields:\n      - { path: voltage, fixid: VOLT }\n",
            "compare against a number",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: volts, fixid: VOLT }\n",
            "has no field 'volts'",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: 'voltage[', fixid: VOLT }\n",
            "bad field path",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: voltage }\n",
            "'fixid' is missing",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: voltage, fixid: NOSUCHKEY }\n",
            "not a valid fixid",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: voltage, fixid: VOLT, scale: big }\n",
            "scale must be a number",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: voltage, fixid: VOLT, converter: v2mv }\n",
            "converter 'v2mv' unknown",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - { path: voltage, fixid: VOLT, units: V }\n",
            "unknown key 'units'",
        ),
        (
            "    message: uavcan.equipment.power.CircuitStatus\n"
            "    fields:\n      - voltage\n",
            "Fields should be dictionaries",
        ),
    ],
)
def test_validation_errors(tmp_path, database, body, why):
    msg = load_error(tmp_path, "inputs:\n  -\n" + body)
    assert why in msg


def test_inputs_must_be_list_of_dicts(tmp_path, database):
    assert "inputs must be a list" in load_error(tmp_path, "inputs: {a: 1}\n")
    assert "Inputs should be dictionaries" in load_error(tmp_path, "inputs: [3]\n")


def test_ignore_fixid_missing_must_be_bool(tmp_path, database):
    assert "true or false" in load_error(tmp_path, "ignore_fixid_missing: maybe\n")


def test_ignore_fixid_missing_makes_field_inert(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
ignore_fixid_missing: true
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    fields:
      - { path: voltage, fixid: NOSUCHKEY }
      - { path: current, fixid: CURRNT }
""",
    )
    entry = m.inputs["uavcan.equipment.power.CircuitStatus"]["entries"][0]
    assert [f["fixid"] for f in entry["fields"]] == ["CURRNT"]
    deliver(m, CIRCUIT(circuit_id=0, voltage=13.5, current=4.0))
    assert database.read("CURRNT")[0] == pytest.approx(4.0)
    assert m.write_count == 1


def test_ignore_fixid_missing_still_validates_paths(tmp_path, database):
    # A missing key may be tolerated; a typo in the DSDL path never is.
    msg = load_error(
        tmp_path,
        """
ignore_fixid_missing: true
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    fields:
      - { path: volts, fixid: NOSUCHKEY }
""",
    )
    assert "has no field 'volts'" in msg


def test_unknown_top_level_key_warns(tmp_path, database):
    log = FakeLog()
    make_mapping(tmp_path, "strict_types: true\ninputs: []\n", log)
    assert any("strict_types" in w for w in log.warnings)


# --- dispatch ------------------------------------------------------------


def test_mag_vector_written(tmp_path, database):
    m = make_mapping(tmp_path, MAG_MAP)
    deliver(m, MAG2(sensor_id=0, magnetic_field_ga=[0.21, -0.05, 0.4]))
    # float16 on the wire: ~3 significant digits
    assert database.read("MAGX")[0] == pytest.approx(0.21, abs=1e-3)
    assert database.read("MAGY")[0] == pytest.approx(-0.05, abs=1e-3)
    assert database.read("MAGZ")[0] == pytest.approx(0.4, abs=1e-3)
    assert m.write_count == 3
    assert m.transfer_count == 1


def test_scale_offset_and_converters(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    fields:
      - { path: voltage, fixid: VOLT, scale: 0.5, offset: 1.0 }
  - message: uavcan.equipment.air_data.StaticTemperature
    fields:
      - { path: static_temperature, fixid: OAT, converter: k2c }
""",
    )
    deliver(m, CIRCUIT(circuit_id=0, voltage=24.0, current=0.0))
    deliver(m, STATIC_T(static_temperature=288.15))
    assert database.read("VOLT")[0] == pytest.approx(13.0)
    # 288.15 K in float16 is 288.25; the converter runs on the wire value
    assert database.read("OAT")[0] == pytest.approx(15.1, abs=0.15)


def test_where_guard_filters(tmp_path, database):
    m = make_mapping(tmp_path, MAG_MAP)
    deliver(m, MAG2(sensor_id=1, magnetic_field_ga=[0.3, 0.3, 0.3]))
    assert database.read("MAGX")[0] == 0.0
    assert m.filtered_count == 1
    assert m.write_count == 0


def test_two_sensors_split_by_where(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    where: { circuit_id: 0 }
    fields:
      - { path: voltage, fixid: VOLT }
  - message: uavcan.equipment.power.CircuitStatus
    where: { circuit_id: 1 }
    fields:
      - { path: current, fixid: CURRNT }
""",
    )
    assert len(m.inputs["uavcan.equipment.power.CircuitStatus"]["entries"]) == 2
    deliver(m, CIRCUIT(circuit_id=0, voltage=13.5, current=50.0))
    deliver(m, CIRCUIT(circuit_id=1, voltage=99.0, current=7.0))
    assert database.read("VOLT")[0] == pytest.approx(13.5)
    assert database.read("CURRNT")[0] == pytest.approx(7.0)
    # each message fails exactly one of the two guards
    assert m.filtered_count == 2


def test_source_node_filter(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    source_node: 43
    fields:
      - { path: voltage, fixid: VOLT }
""",
    )
    deliver(m, CIRCUIT(voltage=12.0), node=44)
    assert database.read("VOLT")[0] != pytest.approx(12.0)
    assert m.filtered_count == 1
    deliver(m, CIRCUIT(voltage=12.0), node=43)
    assert database.read("VOLT")[0] == pytest.approx(12.0)


def test_dynamic_array_short_counts_path_error(tmp_path, database):
    log = FakeLog()
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.ahrs.MagneticFieldStrength2
    fields:
      - { path: "magnetic_field_covariance[0]", fixid: VOLT }
""",
        log,
    )
    # covariance is float16[<=9]; this sender sends none
    deliver(m, MAG2(magnetic_field_ga=[0.1, 0.1, 0.1]))
    deliver(m, MAG2(magnetic_field_ga=[0.1, 0.1, 0.1]))
    assert m.path_error_count == 2
    assert m.write_count == 0
    # warned once per (message, path), then quiet at debug
    assert len([w for w in log.warnings if "covariance" in w]) == 1
    assert len([d for d in log.debugs if "covariance" in d]) == 1


def test_wrong_shape_object_counts_path_error(tmp_path, database):
    # DSDL revision drift: the decoded object lacks the mapped field.
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    where: { circuit_id: 0 }
    fields:
      - { path: voltage, fixid: VOLT }
""",
    )
    entries = m.inputs["uavcan.equipment.power.CircuitStatus"]["entries"]
    m.dispatch(entries, event(SimpleNamespace(circuit_id=0, current=1.0)))
    assert m.path_error_count == 1
    m.dispatch(entries, event(SimpleNamespace(voltage=12.0)))  # guard path gone
    assert m.path_error_count == 2
    m.dispatch(entries, event(SimpleNamespace(circuit_id=0, voltage="n/a")))
    assert m.path_error_count == 3
    assert m.write_count == 0


def test_nonfinite_value_fails_key_and_keeps_last_value(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.air_data.StaticTemperature
    fields:
      - { path: static_temperature, fixid: OAT, converter: k2c }
""",
    )
    deliver(m, STATIC_T(static_temperature=293.15))
    good = database.read("OAT")[0]
    assert database.get_raw_item("OAT").fail is False
    # NaN is the DroneCAN "unknown" convention and survives float16 encoding
    deliver(m, STATIC_T(static_temperature=float("nan")))
    assert database.get_raw_item("OAT").fail is True
    assert database.read("OAT")[0] == good
    assert m.nonfinite_count == 1
    # the next finite reading clears the failure
    deliver(m, STATIC_T(static_temperature=303.15))
    assert database.get_raw_item("OAT").fail is False
    assert database.read("OAT")[0] == pytest.approx(30.0, abs=0.15)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf")])
def test_infinite_value_fails_key(tmp_path, database, bad):
    # The dronecan library saturates inf to 65504 when it builds a message,
    # but a raw float16 inf (0x7C00) from another sender's encoder decodes
    # as inf, so the mapping must not trust the library to have stopped it.
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    fields:
      - { path: voltage, fixid: VOLT }
""",
    )
    deliver(m, CIRCUIT(voltage=13.0))
    entries = m.inputs["uavcan.equipment.power.CircuitStatus"]["entries"]
    m.dispatch(entries, event(SimpleNamespace(voltage=bad)))
    assert database.get_raw_item("VOLT").fail is True
    assert database.read("VOLT")[0] == pytest.approx(13.0)
    assert m.nonfinite_count == 1


@pytest.mark.parametrize(
    "health,bad",
    [
        (dc_mapping.HEALTH_OK, False),
        (dc_mapping.HEALTH_WARNING, False),
        (dc_mapping.HEALTH_ERROR, True),
        (dc_mapping.HEALTH_CRITICAL, True),
    ],
)
def test_sender_health_sets_bad(tmp_path, database, health, bad):
    m = make_mapping(tmp_path, MAG_MAP)
    m.node_health[42] = health
    deliver(m, MAG2(sensor_id=0, magnetic_field_ga=[0.2, 0.0, 0.4]), node=42)
    for key in ("MAGX", "MAGY", "MAGZ"):
        assert database.get_raw_item(key).bad is bad
    # the value is still written: bad is a flag, not a gate
    assert database.read("MAGX")[0] == pytest.approx(0.2, abs=1e-3)


def test_bad_clears_when_sender_recovers(tmp_path, database):
    m = make_mapping(tmp_path, MAG_MAP)
    m.node_health[42] = dc_mapping.HEALTH_ERROR
    deliver(m, MAG2(sensor_id=0, magnetic_field_ga=[0.2, 0.0, 0.4]))
    assert database.get_raw_item("MAGX").bad is True
    m.node_health[42] = dc_mapping.HEALTH_OK
    deliver(m, MAG2(sensor_id=0, magnetic_field_ga=[0.2, 0.0, 0.4]))
    assert database.get_raw_item("MAGX").bad is False


def test_unknown_sender_health_is_not_bad(tmp_path, database):
    # A node whose NodeStatus has not been seen yet is not presumed bad.
    m = make_mapping(tmp_path, MAG_MAP)
    deliver(m, MAG2(sensor_id=0, magnetic_field_ga=[0.2, 0.0, 0.4]), node=99)
    assert database.get_raw_item("MAGX").bad is False


def test_database_rejection_counted(tmp_path, database):
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    fields:
      - { path: voltage, fixid: VOLT }
""",
    )

    class Rejecting:
        fail = False
        bad = False

        @property
        def value(self):
            return 0.0

        @value.setter
        def value(self, v):
            raise ValueError("rejected")

    entry = m.inputs["uavcan.equipment.power.CircuitStatus"]["entries"][0]
    entry["fields"][0]["item"] = Rejecting()
    deliver(m, CIRCUIT(voltage=12.0))
    assert m.invalid_count == 1
    assert m.write_count == 0


def test_handler_survives_malformed_event(tmp_path, database):
    log = FakeLog()
    m = make_mapping(
        tmp_path,
        """
inputs:
  - message: uavcan.equipment.power.CircuitStatus
    fields:
      - { path: voltage, fixid: VOLT }
""",
        log,
    )
    handler = m.handler_for("uavcan.equipment.power.CircuitStatus")
    handler(SimpleNamespace(message=CIRCUIT(voltage=1.0)))  # no .transfer
    handler(SimpleNamespace(message=CIRCUIT(voltage=1.0)))
    assert m.handler_error_count == 2
    assert m.transfer_count == 2
    assert len(log.warnings) == 1  # rate-limited to one warning
