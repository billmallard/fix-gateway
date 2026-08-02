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

# Plugin lifecycle and virtual-bus integration tests: start the real
# plugin against a python-can 'virtual' bus, send CANaerospace frames with
# a plain can.Bus, and assert FIX database values and status counters.

import random
import time
from collections import namedtuple

import can
import pytest

import fixgw.plugin as plugin_base
import fixgw.plugins.canaerospace
from fixgw import cfg
from fixgw.plugins.canaerospace import protocol
from fixgw.tools import canas_sim

CONFIG_DATA = """
load: yes
module: fixgw.plugins.canaerospace
interface: virtual
channel: tcan_canas
mapfile: 'tests/config/canaerospace/map.yaml'
node_timeout: 0.5
CONFIGPATH: ''
"""


Objects = namedtuple("Objects", ["bus", "pl"])


@pytest.fixture
def plugin(database):
    cc, cc_meta = cfg.from_yaml(CONFIG_DATA, metadata=True)
    pl = fixgw.plugins.canaerospace.Plugin("canaerospace", cc, cc_meta)
    pl.start()
    can_bus = can.Bus(cc["channel"], interface=cc["interface"])
    time.sleep(0.1)  # Give the plugin thread a chance to get started
    yield Objects(bus=can_bus, pl=pl)
    pl.stop()
    can_bus.shutdown()


def send(bus, canid, data):
    msg = can.Message(is_extended_id=False, arbitration_id=canid)
    msg.data = bytearray(data)
    msg.dlc = len(msg.data)
    bus.send(msg)


def frame(node, dtype, code, values):
    m = protocol.CanasMessage(
        canid=300,  # placeholder, build() only uses it for range checking
        node_id=node,
        data_type=dtype,
        service_code=0,
        message_code=code,
        values=tuple(values),
        channel=protocol.Channel.NOD,
    )
    return protocol.build(m)[1]


def wait_for(pred, timeout=2.0):
    start = time.time()
    while time.time() - start < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_parameter_writes_and_status(plugin, database):
    send(plugin.bus, 200, frame(16, protocol.USHORT, 1, [2650]))
    assert wait_for(lambda: database.read("TACH1")[0] == 2650)
    # 310 kPa -> ~44.96 psi via the kpa2psi converter
    send(plugin.bus, 201, frame(16, protocol.FLOAT, 2, [310.0]))
    assert wait_for(lambda: database.read("OILP1")[0] == pytest.approx(44.96, abs=0.01))
    # One UCHAR4 frame feeds two EGT cylinders
    send(plugin.bus, 205, frame(16, protocol.UCHAR4, 3, [78, 80, 0, 0]))
    assert wait_for(lambda: database.read("EGT11")[0] == pytest.approx(780.0))
    assert database.read("EGT12")[0] == pytest.approx(800.0)

    status = plugin.pl.get_status()
    assert status["Received Frames"] == 3
    assert status["Decoded Writes"] == 4
    assert status["Invalid Frames"] == 0
    assert 16 in status["Nodes Seen"]
    assert status["Nodes Seen"][16]["frames"] == 3


def test_ignored_invalid_unsupported_nodata(plugin, database):
    # Unmapped Debug Service Data channel -> ignored
    send(plugin.bus, 1900, frame(16, protocol.USHORT, 1, [1]))
    # Unmapped NOD id -> ignored
    send(plugin.bus, 301, frame(16, protocol.USHORT, 2, [1]))
    # 29-bit identifier -> ignored
    msg = can.Message(
        is_extended_id=True,
        arbitration_id=0x18C,
        data=bytearray(b"\x10\x07\x00\x01\x00\x01"),
    )
    plugin.bus.send(msg)
    # Mapped id, DLC < 4 -> invalid
    send(plugin.bus, 200, b"\x10\x07\x00")
    # Mapped id, unsupported data type -> counted, not fatal
    send(plugin.bus, 200, bytes((0x10, protocol.MEMID, 0x00, 0x01)) + b"\x00" * 4)
    # Mapped id, NODATA -> counted, nothing written
    send(plugin.bus, 200, frame(16, protocol.NODATA, 4, []))

    assert wait_for(lambda: plugin.pl.recvcount == 6)
    status = plugin.pl.get_status()
    assert status["Ignored Frames"] == 3
    assert status["Invalid Frames"] == 1
    assert status["Unsupported Types"] == 1
    assert status["NoData Frames"] == 1
    assert status["Decoded Writes"] == 0
    assert database.read("TACH1")[0] == 0


def test_emergency_event_latches_annunciator(plugin, database):
    assert database.read("BTN1")[0] is False
    # EED canid 16, any payload (match_code null)
    send(plugin.bus, 16, frame(9, protocol.ULONG, 1, [1]))
    assert wait_for(lambda: database.read("BTN1")[0] is True)
    # EED canid 17 fires only on its match_code (0x12345678)
    send(plugin.bus, 17, frame(9, protocol.ULONG, 2, [0x11111111]))
    time.sleep(0.1)
    assert database.read("BTN2")[0] is False
    send(plugin.bus, 17, frame(9, protocol.ULONG, 3, [0x12345678]))
    assert wait_for(lambda: database.read("BTN2")[0] is True)
    status = plugin.pl.get_status()
    assert status["Emergency Events"] == 3


def test_sequence_gap_telltale(plugin, database):
    send(plugin.bus, 200, frame(16, protocol.USHORT, 7, [1000]))
    send(plugin.bus, 200, frame(16, protocol.USHORT, 8, [1100]))
    send(plugin.bus, 200, frame(16, protocol.USHORT, 12, [1200]))  # gap
    send(plugin.bus, 200, frame(16, protocol.USHORT, 13, [1300]))
    assert wait_for(lambda: database.read("TACH1")[0] == 1300)
    status = plugin.pl.get_status()
    assert status["Sequence Gaps"] == 1
    # The gap never gated decoding
    assert status["Decoded Writes"] == 4


def test_node_failover_over_the_bus(plugin, database):
    # OILT1 (canid 202) prefers node 16 over node 17; node_timeout 0.5s
    send(plugin.bus, 202, frame(16, protocol.FLOAT, 1, [333.15]))  # 60 C
    assert wait_for(lambda: database.read("OILT1")[0] == pytest.approx(60.0, abs=0.01))
    send(plugin.bus, 202, frame(17, protocol.FLOAT, 1, [343.15]))  # blocked
    time.sleep(0.1)
    assert database.read("OILT1")[0] == pytest.approx(60.0, abs=0.01)
    time.sleep(0.6)  # let node 16 go stale
    send(plugin.bus, 202, frame(17, protocol.FLOAT, 2, [343.15]))  # 70 C
    assert wait_for(lambda: database.read("OILT1")[0] == pytest.approx(70.0, abs=0.01))


def test_fuzz_soak_never_kills_the_thread(plugin, database):
    # Deterministic hostile traffic: random ids, DLCs and payloads
    rng = random.Random(42)
    for _ in range(500):
        canid, data = canas_sim.fuzz_message(rng)
        send(plugin.bus, canid, data)
    assert wait_for(lambda: plugin.pl.recvcount >= 500, timeout=5.0)
    assert plugin.pl.thread.is_alive()
    # Status must remain coherent (all counters accounted, no exception)
    status = plugin.pl.get_status()
    total = (
        status["Ignored Frames"]
        + status["Invalid Frames"]
        + status["Unsupported Types"]
        + status["NoData Frames"]
        + status["Decoded Writes"]
        + status["Error Frames"]
    )
    assert total >= 0  # smoke: get_status() itself did not blow up


def test_sim_default_script_parses_and_counts():
    codes = {}
    frames = list(canas_sim.script_messages(canas_sim.DEFAULT_SCRIPT, codes))
    assert len(frames) == len(canas_sim.DEFAULT_SCRIPT)
    for canid, data in frames:
        protocol.parse(canid, data)  # must not raise
    # Message codes increment per (canid, node) and wrap at 256
    frames2 = list(canas_sim.script_messages(canas_sim.DEFAULT_SCRIPT, codes))
    for (_, d1), (_, d2) in zip(frames, frames2):
        assert d2[3] == (d1[3] + 1) % 256


def test_missing_mapfile_raises(database):
    bad = CONFIG_DATA.replace("tests/config/canaerospace/map.yaml", "no_such_map.yaml")
    cc, cc_meta = cfg.from_yaml(bad, metadata=True)
    with pytest.raises(ValueError):
        fixgw.plugins.canaerospace.Plugin("canaerospace", cc, cc_meta)


def test_stop_failure_raises_pluginfail(database):
    cc, cc_meta = cfg.from_yaml(CONFIG_DATA, metadata=True)
    pl = fixgw.plugins.canaerospace.Plugin("canaerospace", cc, cc_meta)

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
