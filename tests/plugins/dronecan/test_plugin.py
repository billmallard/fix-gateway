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

# Plugin lifecycle and virtual-bus integration tests: start the real plugin
# on a python-can 'virtual' bus and feed it frames encoded by dronecan_sim
# (the dronecan library's own transport), then assert FIX database values,
# quality flags and status counters.

import itertools
import math
import random
import sys
import threading
import time
from collections import namedtuple

import can
import pytest

import fixgw.plugin as plugin_base
import fixgw.plugins.dronecan as dc_plugin
from fixgw import cfg
from fixgw.plugins.dronecan import mapping as dc_mapping
from fixgw.tools import dronecan_sim as sim

_channels = itertools.count()

CONFIG_DATA = """
load: yes
module: fixgw.plugins.dronecan
interface: virtual
channel: {channel}
bitrate: 1000000
mapfile: 'tests/config/dronecan/map.yaml'
CONFIGPATH: '{configpath}'
{extra}
"""

Objects = namedtuple("Objects", ["bus", "pl", "transfer_ids"])


def make_config(extra="", configpath="", channel=None):
    channel = channel or "tdc_plugin_{}".format(next(_channels))
    text = CONFIG_DATA.format(channel=channel, configpath=configpath, extra=extra)
    return cfg.from_yaml(text, metadata=True)


def start_plugin(cc, cc_meta):
    pl = dc_plugin.Plugin("dronecan", cc, cc_meta)
    pl.start()
    bus = can.Bus(cc["channel"], interface="virtual")
    assert wait_for(lambda: pl.thread.node is not None)
    return Objects(bus=bus, pl=pl, transfer_ids={})


@pytest.fixture
def plugin(database):
    obj = start_plugin(*make_config())
    yield obj
    obj.pl.stop()
    obj.bus.shutdown()


def wait_for(pred, timeout=3.0):
    start = time.time()
    while time.time() - start < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def send(obj, node, message, **fields):
    for canid, data in sim.frames(sim.build(message, fields), node, obj.transfer_ids):
        obj.bus.send(can.Message(arbitration_id=canid, data=data, is_extended_id=True))


def send_status(obj, node, health=0, uptime=10):
    send(obj, node, "uavcan.protocol.NodeStatus", uptime_sec=uptime, health=health)


MAG = "uavcan.equipment.ahrs.MagneticFieldStrength2"


def test_sim_demo_cycle_end_to_end(plugin, database):
    t = 9.0  # demo heading 90 deg: field points left (-Y)
    for canid, data in sim.cycle_frames(sim.default_messages(t), plugin.transfer_ids):
        plugin.bus.send(
            can.Message(arbitration_id=canid, data=data, is_extended_id=True)
        )
    assert wait_for(lambda: database.read("OAT")[0] != 0.0)
    assert wait_for(lambda: database.read("MAGY")[0] == pytest.approx(-0.22, abs=1e-3))
    assert database.read("MAGX")[0] == pytest.approx(0.0, abs=1e-3)
    assert database.read("MAGZ")[0] == pytest.approx(0.42, abs=1e-3)
    assert database.read("VOLT")[0] == pytest.approx(13.9, abs=0.01)
    assert database.read("CURRNT")[0] == pytest.approx(12.5, abs=0.01)
    assert database.read("OAT")[0] == pytest.approx(15.0, abs=0.15)

    status = plugin.pl.get_status()
    assert status["Transfers Received"] == 3
    assert status["Values Written"] == 6
    assert status["Frames Received"] == 3
    assert status["Node ID"] == "anonymous"
    assert status["Path Errors"] == 0
    assert status["Transfer Errors"] == 0


def test_multi_frame_transfer_and_source_filter(plugin, database):
    lat, lon = 35.123456789, -97.4
    fix = dict(
        latitude_deg_1e8=round(lat * 1e8),
        longitude_deg_1e8=round(lon * 1e8),
        sats_used=9,
        status=3,
    )
    assert len(sim.frames(sim.build("uavcan.equipment.gnss.Fix2", fix), 50, {})) > 1
    send(plugin, 51, "uavcan.equipment.gnss.Fix2", **fix)  # not the mapped node
    send(plugin, 50, "uavcan.equipment.gnss.Fix2", **fix)
    assert wait_for(lambda: database.read("LAT")[0] == pytest.approx(lat, abs=1e-8))
    assert database.read("LONG")[0] == pytest.approx(lon, abs=1e-8)
    status = plugin.pl.get_status()
    assert status["Filtered"] == 1
    assert status["Values Written"] == 2


def test_node_status_tracked_and_health_flags_data(plugin, database):
    send_status(plugin, 42, health=dc_mapping.HEALTH_OK, uptime=5)
    assert wait_for(lambda: 42 in plugin.pl.nodes)
    status = plugin.pl.get_status()
    assert status["Nodes"][42]["health"] == "OK"
    assert status["Nodes"][42]["uptime_s"] == 5

    send(plugin, 42, MAG, sensor_id=0, magnetic_field_ga=[0.1, 0.2, 0.3])
    assert wait_for(lambda: database.read("MAGX")[0] == pytest.approx(0.1, abs=1e-3))
    assert database.get_raw_item("MAGX").bad is False

    send_status(plugin, 42, health=dc_mapping.HEALTH_ERROR, uptime=6)
    assert wait_for(lambda: plugin.pl.nodes[42]["health"] == dc_mapping.HEALTH_ERROR)
    send(plugin, 42, MAG, sensor_id=0, magnetic_field_ga=[0.15, 0.2, 0.3])
    assert wait_for(lambda: database.get_raw_item("MAGX").bad is True)
    assert database.read("MAGX")[0] == pytest.approx(0.15, abs=1e-3)

    send_status(plugin, 42, health=dc_mapping.HEALTH_OK, uptime=7)
    assert wait_for(lambda: plugin.pl.nodes[42]["health"] == dc_mapping.HEALTH_OK)
    send(plugin, 42, MAG, sensor_id=0, magnetic_field_ga=[0.12, 0.2, 0.3])
    assert wait_for(lambda: database.get_raw_item("MAGX").bad is False)


def test_nan_marks_fail_over_the_bus(plugin, database):
    send(
        plugin,
        43,
        "uavcan.equipment.air_data.StaticTemperature",
        static_temperature=293.15,
    )
    assert wait_for(lambda: database.read("OAT")[0] == pytest.approx(20.0, abs=0.15))
    send(
        plugin,
        43,
        "uavcan.equipment.air_data.StaticTemperature",
        static_temperature=float("nan"),
    )
    assert wait_for(lambda: database.get_raw_item("OAT").fail is True)
    assert database.read("OAT")[0] == pytest.approx(20.0, abs=0.15)
    assert plugin.pl.get_status()["Non-finite Values"] == 1


def test_foreign_and_unknown_traffic_is_counted_not_fatal(plugin, database):
    # 11-bit CAN-FIX / CANaerospace style frame
    plugin.bus.send(
        can.Message(arbitration_id=0x180, data=b"\x01\x02", is_extended_id=False)
    )
    # Single-frame broadcast of a data type id the library has no DSDL for
    # (a vendor message): priority 24, dtid 30000, node 60, tail SOT|EOT.
    # (Not 20000: the library ships ArduPilot's vendor DSDL, which uses it.)
    canid = (24 << 24) | (30000 << 8) | 60
    plugin.bus.send(
        can.Message(arbitration_id=canid, data=b"\x01\xc0", is_extended_id=True)
    )
    # Multi-frame transfer with a corrupted payload byte: CRC mismatch.
    frames = sim.frames(
        sim.build("uavcan.equipment.gnss.Fix2", {"sats_used": 5}), 50, {}
    )
    first_id, first = frames[0]
    corrupted = [(first_id, bytes([first[0], first[1], first[2] ^ 0xFF]) + first[3:])]
    for cid, data in corrupted + frames[1:]:
        plugin.bus.send(can.Message(arbitration_id=cid, data=data, is_extended_id=True))

    assert wait_for(lambda: plugin.pl.get_status()["Transfer Errors"] == 2)
    status = plugin.pl.get_status()
    assert status["Foreign Frames"] == 1
    assert status["Values Written"] == 0
    assert status["Reopens"] == 0
    assert plugin.pl.thread.is_alive()
    # and the bridge still decodes afterwards
    send(plugin, 42, MAG, sensor_id=0, magnetic_field_ga=[0.3, 0.0, 0.4])
    assert wait_for(lambda: database.read("MAGX")[0] == pytest.approx(0.3, abs=1e-3))


def test_fuzz_soak_never_kills_the_thread(plugin, database):
    rng = random.Random(42)
    for _ in range(500):
        canid, data, extended = sim.fuzz_message(rng)
        plugin.bus.send(
            can.Message(arbitration_id=canid, data=data, is_extended_id=extended)
        )
    assert wait_for(
        lambda: plugin.pl.get_status()["Frames Received"] >= 500, timeout=10.0
    )
    assert plugin.pl.thread.is_alive()
    status = plugin.pl.get_status()
    assert status["Reopens"] == 0
    assert status["Foreign Frames"] > 0
    # a good transfer still gets through after the storm
    send(plugin, 42, MAG, sensor_id=0, magnetic_field_ga=[-0.25, 0.1, 0.4])
    assert wait_for(lambda: database.read("MAGX")[0] == pytest.approx(-0.25, abs=1e-3))


def test_anonymous_plugin_transmits_nothing(plugin, database):
    listener = can.Bus(plugin.pl.channel, interface="virtual")
    try:
        send(plugin, 42, MAG, sensor_id=0, magnetic_field_ga=[0.1, 0.1, 0.1])
        send_status(plugin, 42)
        time.sleep(1.5)  # longer than the library's NodeStatus period
        seen = []
        while True:
            msg = listener.recv(0.1)
            if msg is None:
                break
            seen.append(msg)
        # only the two frames this test sent; nothing from the plugin
        assert len(seen) == 2
    finally:
        listener.shutdown()


def test_node_id_publishes_only_nodestatus(database):
    obj = start_plugin(*make_config("node_id: 100"))
    listener = can.Bus(obj.pl.channel, interface="virtual")
    try:
        time.sleep(1.6)
        seen = []
        while True:
            msg = listener.recv(0.1)
            if msg is None:
                break
            seen.append(msg)
        assert seen, "a node with an id must publish NodeStatus"
        for msg in seen:
            assert msg.arbitration_id & 0x7F == 100  # source node
            assert (msg.arbitration_id >> 8) & 0xFFFF == 341  # NodeStatus
        assert obj.pl.get_status()["Node ID"] == 100
    finally:
        listener.shutdown()
        obj.pl.stop()
        obj.bus.shutdown()


def test_node_created_on_plugin_thread(database, monkeypatch):
    made = []
    real = dc_plugin.make_node

    def recording(driver, node_id):
        made.append((threading.current_thread(), node_id, driver))
        return real(driver, node_id)

    monkeypatch.setattr(dc_plugin, "make_node", recording)
    obj = start_plugin(*make_config())
    try:
        assert made[0][0] is obj.pl.thread
        assert made[0][1] is None
        assert made[0][2].listen_only is True
    finally:
        obj.pl.stop()
        obj.bus.shutdown()


class DyingNode:
    """A node whose driver has died: every spin raises a CAN error."""

    def __init__(self):
        self.closed = False
        self.spins = 0

    def add_handler(self, *args):
        pass

    def spin(self, timeout):
        self.spins += 1
        raise can.CanError("bus off")

    def close(self):
        self.closed = True


def test_repeated_driver_errors_reopen_the_node(database, monkeypatch):
    nodes = []

    def dying(driver, node_id):
        driver.close()
        nodes.append(DyingNode())
        return nodes[-1]

    monkeypatch.setattr(dc_plugin, "make_node", dying)
    monkeypatch.setattr(dc_plugin.MainThread, "_warn", lambda self, k, m: None)
    cc, meta = make_config()
    pl = dc_plugin.Plugin("dronecan", cc, meta)
    pl.start()
    try:
        # REOPEN_AFTER failed spins (1 s apart), close, 1 s back-off, reopen
        assert wait_for(lambda: len(nodes) >= 2, timeout=8.0)
        assert pl.reopen_count >= 1
        assert nodes[0].closed is True
        assert nodes[0].spins == dc_plugin.REOPEN_AFTER
        assert pl.spin_error_count >= dc_plugin.REOPEN_AFTER
    finally:
        pl.stop()


def test_open_failure_is_retried(database, monkeypatch):
    calls = []

    def broken(driver, node_id):
        driver.close()
        calls.append(1)
        raise OSError("no such device")

    monkeypatch.setattr(dc_plugin, "make_node", broken)
    cc, meta = make_config()
    pl = dc_plugin.Plugin("dronecan", cc, meta)
    pl.start()
    try:
        assert wait_for(lambda: len(calls) >= 2, timeout=4.0)
        assert pl.open_errors >= 2
        assert pl.thread.is_alive()
    finally:
        pl.stop()


def test_stop_is_prompt_on_a_quiet_bus(plugin):
    start = time.monotonic()
    plugin.pl.stop()
    assert time.monotonic() - start < 1.5
    assert not plugin.pl.thread.is_alive()


class FakeMonitor:
    instances = []

    def __init__(self, node):
        self.node = node
        self.closed = False
        FakeMonitor.instances.append(self)

    def close(self):
        self.closed = True


class FakeAllocator:
    instances = []

    def __init__(self, node, monitor, database_storage=None):
        self.node = node
        self.monitor = monitor
        self.database_storage = database_storage
        self.closed = False
        FakeAllocator.instances.append(self)

    def close(self):
        self.closed = True


def test_allocator_constructed_and_closed(database, monkeypatch, tmp_path):
    FakeMonitor.instances.clear()
    FakeAllocator.instances.clear()
    monkeypatch.setattr(dc_plugin, "NodeMonitor", FakeMonitor)
    monkeypatch.setattr(dc_plugin, "CentralizedServer", FakeAllocator)
    obj = start_plugin(
        *make_config("node_id: 100\nallocator: true", configpath=str(tmp_path))
    )
    try:
        assert len(FakeAllocator.instances) == 1
        alloc = FakeAllocator.instances[0]
        assert alloc.monitor is FakeMonitor.instances[0]
        assert alloc.database_storage == str(tmp_path / "dronecan" / "allocation.db")
        assert (tmp_path / "dronecan").is_dir()
        assert obj.pl.get_status()["Allocator"] == "on"
    finally:
        obj.pl.stop()
        obj.bus.shutdown()
    assert alloc.closed is True
    assert FakeMonitor.instances[0].closed is True


@pytest.mark.parametrize(
    "extra,why",
    [
        ("allocator: true", "needs a node_id"),
        ("node_id: 0", "1-125"),
        ("node_id: 126", "1-125"),
        ("node_id: true", "1-125"),
        ("allocator: 'yes'", "true or false"),
    ],
)
def test_config_validation(database, extra, why):
    cc, meta = make_config(extra)
    with pytest.raises(ValueError, match=why):
        dc_plugin.Plugin("dronecan", cc, meta)


def test_missing_mapfile_raises(database):
    cc, meta = make_config()
    cc["mapfile"] = "no_such_map.yaml"
    with pytest.raises(ValueError):
        dc_plugin.Plugin("dronecan", cc, meta)


def test_stop_failure_raises_pluginfail(database):
    cc, meta = make_config()
    pl = dc_plugin.Plugin("dronecan", cc, meta)

    class StuckThread:
        def stop(self):
            pass

        def is_alive(self):
            return True

        def join(self, timeout):
            pass

    pl.thread = StuckThread()
    with pytest.raises(plugin_base.PluginFail):
        pl.stop()


def test_shipped_configs_load(database):
    # The shipped mapfile must validate against the shipped database with
    # only the magnetometer live (the other examples stay commented out:
    # possible second writer, or not bench-checked), and the connection
    # yaml must ship with load: no.
    m = dc_mapping.Mapping("src/fixgw/config/dronecan/map.yaml")
    assert list(m.inputs) == ["uavcan.equipment.ahrs.MagneticFieldStrength2"]
    fields = m.inputs["uavcan.equipment.ahrs.MagneticFieldStrength2"]["entries"][0][
        "fields"
    ]
    assert [f["fixid"] for f in fields] == ["MAGX", "MAGY", "MAGZ"]
    with open("src/fixgw/config/connections/dronecan.yaml") as f:
        conn = cfg.from_yaml(f.read())
    assert conn["dronecan"]["load"] is False
    assert conn["dronecan"]["node_id"] is None
    assert conn["dronecan"]["allocator"] is False


def test_shipped_commented_examples_validate(database, tmp_path):
    # Uncommenting the shipped examples must give a valid mapfile: proves
    # the documented field paths match the installed library's DSDL.
    with open("src/fixgw/config/dronecan/map.yaml") as f:
        text = f.read()
    lines = []
    for line in text.splitlines():
        if line.startswith("  # ") and line[4:5] in ("-", " ") and "message:" in line:
            lines.append("  " + line[4:])
        elif line.startswith("  #   "):
            lines.append("  " + line[4:])
        else:
            lines.append(line)
    p = tmp_path / "map.yaml"
    p.write_text("\n".join(lines))
    m = dc_mapping.Mapping(str(p))
    assert set(m.inputs) == {
        "uavcan.equipment.ahrs.MagneticFieldStrength2",
        "uavcan.equipment.power.CircuitStatus",
        "uavcan.equipment.air_data.StaticTemperature",
        "uavcan.equipment.gnss.Fix2",
    }


# --- dronecan_sim -------------------------------------------------------


def test_sim_transfer_ids_increment_and_wrap():
    ids = {}
    msg = sim.build(MAG, {"magnetic_field_ga": [0, 0, 0]})
    tails = [sim.frames(msg, 42, ids)[0][1][-1] & 0x1F for _ in range(34)]
    assert tails[:3] == [0, 1, 2]
    assert tails[31:] == [31, 0, 1]


def test_sim_mag_vector():
    for heading in (0, 45, 90, 180, 270):
        x, y, z = sim.mag_vector(heading)
        assert math.hypot(x, y) == pytest.approx(sim.FIELD_H)
        assert z == sim.FIELD_V
        # heading recovers from the level-attitude vector
        assert math.degrees(math.atan2(-y, x)) % 360 == pytest.approx(heading % 360)


def test_sim_script_and_errors(tmp_path):
    good = tmp_path / "s.yaml"
    good.write_text(
        "messages:\n"
        "  - message: uavcan.equipment.power.CircuitStatus\n"
        "    node: 43\n"
        "    fields: {circuit_id: 0, voltage: 12.0}\n"
    )
    script = sim.load_script(str(good))
    assert script[0][0] == 43
    assert float(script[0][1].voltage) == pytest.approx(12.0)
    empty = tmp_path / "e.yaml"
    empty.write_text("frames: []\n")
    with pytest.raises(SystemExit):
        sim.load_script(str(empty))
    with pytest.raises(SystemExit):
        sim.build("uavcan.equipment.nope.Nope", {})


def test_sim_fuzz_ranges():
    rng = random.Random(1)
    widths = set()
    for _ in range(200):
        canid, data, extended = sim.fuzz_message(rng)
        widths.add(extended)
        assert 0 <= canid < (1 << (29 if extended else 11))
        assert len(data) <= 8
    assert widths == {True, False}


def test_sim_main_drives_the_plugin(plugin, database, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dronecan_sim",
            "--interface",
            "virtual",
            "--channel",
            plugin.pl.channel,
            "--rate",
            "20",
            "--duration",
            "1.2",
            "--health",
            "error",
        ],
    )
    sim.main()
    assert "sent" in capsys.readouterr().out
    assert wait_for(lambda: database.read("VOLT")[0] == pytest.approx(13.9, abs=0.01))
    status = plugin.pl.get_status()
    assert status["Nodes"][sim.MAG_NODE]["health"] == "ERROR"
    assert status["Nodes"][sim.POWER_NODE]["health"] == "ERROR"
    assert status["Transfer Errors"] == 0
    # data sent after the first ERROR NodeStatus is flagged
    assert database.get_raw_item("MAGX").bad is True
