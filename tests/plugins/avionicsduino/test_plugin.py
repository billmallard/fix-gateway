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

# Plugin lifecycle and virtual-bus integration: start the real plugin on a
# python-can 'virtual' bus, send frames from avionicsduino_sim with a
# plain can.Bus, and assert FIX values, quality flags and status counters.

import math
import random
import time
from collections import namedtuple

import can
import pytest
import yaml

import fixgw.plugin as plugin_base
import fixgw.plugins.avionicsduino
from fixgw import cfg
from fixgw.plugins.avionicsduino import protocol
from fixgw.tools import avionicsduino_sim as sim

CONFIG_DATA = """
load: AVIONICSDUINO
module: fixgw.plugins.avionicsduino
interface: virtual
channel: tcan_avionicsduino
mapfile: '{CONFIG}/avionicsduino/ahrs21_rm3_rdam12.yaml'
CONFIGPATH: 'src/fixgw/config'
"""

Objects = namedtuple("Objects", ["bus", "pl"])


def make_plugin(extra=""):
    cc, cc_meta = cfg.from_yaml(CONFIG_DATA + extra, metadata=True)
    return fixgw.plugins.avionicsduino.Plugin("avionicsduino", cc, cc_meta)


@pytest.fixture
def plugin(database):
    pl = make_plugin()
    pl.start()
    can_bus = can.Bus("tcan_avionicsduino", interface="virtual")
    time.sleep(0.1)  # let the plugin thread start
    yield Objects(bus=can_bus, pl=pl)
    pl.stop()
    can_bus.shutdown()


def send(bus, canid, data, extended=False):
    bus.send(can.Message(arbitration_id=canid, data=data, is_extended_id=extended))


def send_tick(bus, tick, t, without=()):
    for canid, data in sim.frames_for_tick(tick, t, without):
        send(bus, canid, data)


def wait_for(pred, timeout=2.0):
    start = time.time()
    while time.time() - start < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return pred()


def test_sim_frames_land_in_the_database(plugin, database):
    t = 10.0  # heading 120 magnetic, track 115 true
    for tick in range(4):
        send_tick(plugin.bus, tick, t)
    assert wait_for(lambda: database.read("GPS_SATS_VISIBLE")[0] == 14)
    assert wait_for(lambda: database.read("CURRNT")[0] == pytest.approx(12.3, abs=1e-4))
    assert database.read("ROLL")[0] == pytest.approx(sim.BANK_DEG, abs=1e-4)
    assert database.read("PITCH")[0] == pytest.approx(2.0)
    assert database.read("ANORM")[0] == pytest.approx(
        1 / math.cos(math.radians(sim.BANK_DEG)), abs=1e-5
    )
    assert database.read("HEAD")[0] == pytest.approx(120.0, abs=1e-3)
    assert database.read("TRACK")[0] == pytest.approx(115.0, abs=1e-3)
    assert database.read("GS")[0] == pytest.approx(185 / 1.852, abs=1e-3)
    assert database.read("OAT")[0] == pytest.approx(12.5)
    assert database.read("MAGZ")[0] == pytest.approx(0.45, abs=1e-6)
    assert database.read("TIMEZH")[0] == 15
    assert database.read("LAT")[0] == pytest.approx(
        45.0 + 0.01 * math.cos(math.radians(120.0)), abs=1e-9
    )
    status = plugin.pl.get_status()
    assert status["Invalid Frames"] == 0
    assert status["AHRS Counter Gaps"] == 0
    assert set(status["Nodes Seen"]) == {"ahrs", "remote", "rdam", "ems"}


def test_alt_and_vs_stay_untouched_by_default(plugin, database):
    alt, vs, baro = (database.read(k)[0] for k in ("ALT", "VS", "BARO"))
    send(plugin.bus, 46, protocol.encode(46, {
        "qnh_cinhg": 3012, "qnh_alt_ft": 4500, "density_alt_ft": 5200, "baro_vs_fpm": 700,
    }))
    send(plugin.bus, 20, protocol.encode(20, {"roll_rad": 0.0, "pitch_deg": 0.0}))
    assert wait_for(lambda: plugin.pl.recvcount == 2)
    time.sleep(0.05)
    assert (database.read("ALT")[0], database.read("VS")[0]) == (alt, vs)
    assert database.read("BARO")[0] == baro
    assert plugin.pl.get_status()["RDAM Altitude"] == "off"


def test_alt_and_vs_options_on(database):
    pl = make_plugin("rdam_altitude: true\nrdam_vertical_speed: true\n")
    pl.start()
    bus = can.Bus("tcan_avionicsduino", interface="virtual")
    try:
        baro = database.read("BARO")[0]
        time.sleep(0.1)
        send(bus, 46, protocol.encode(46, {
            "qnh_cinhg": 3012, "qnh_alt_ft": 4500, "density_alt_ft": 5200, "baro_vs_fpm": 700,
        }))
        assert wait_for(lambda: database.read("ALT")[0] == 4500)
        assert database.read("VS")[0] == 700
        assert database.read("BARO")[0] == baro
        assert pl.get_status()["RDAM Altitude"] == "on"
    finally:
        pl.stop()
        bus.shutdown()


def test_a_node_that_goes_quiet_marks_its_keys_old(plugin, database):
    send_tick(plugin.bus, 0, 0.0)
    assert wait_for(lambda: database.read("HEAD")[0] == pytest.approx(90.0, abs=1e-3))
    head = database.get_raw_item("HEAD")
    roll = database.get_raw_item("ROLL")
    # The remote module stops; the AHRS keeps going at its own rates.
    deadline = time.time() + (head.tol + 200) / 1000.0
    tick = 1
    while time.time() < deadline:
        send_tick(plugin.bus, tick, tick / sim.TICK_HZ, without=("remote",))
        tick += 1
        time.sleep(1 / sim.TICK_HZ)
    time.sleep(0.05)
    database.update()
    assert head.old
    assert database.get_raw_item("MAGX").old
    assert database.get_raw_item("OAT").old
    assert not roll.old
    # It comes back
    send_tick(plugin.bus, 0, 0.0)
    assert wait_for(lambda: not head.old)


def test_ignored_and_invalid_frames(plugin, database):
    send(plugin.bus, 20, protocol.encode(20, {"roll_rad": 0.1, "pitch_deg": 1.0}), extended=True)
    send(plugin.bus, 11, bytes(8))  # no layout
    send(plugin.bus, 1000, bytes(8))  # no layout
    send(plugin.bus, 20, bytes(4))  # short
    send(plugin.bus, 36, bytes(7))  # short
    assert wait_for(lambda: plugin.pl.recvcount == 5)
    status = plugin.pl.get_status()
    assert status["Ignored Frames"] == 3
    assert status["Invalid Frames"] == 2
    assert status["Decoded Writes"] == 0


def test_counter_gap_telltale(plugin, database):
    for count in (7, 8, 12, 13):
        send(plugin.bus, 10, protocol.encode(10, {"count": count}))
    assert wait_for(lambda: plugin.pl.recvcount == 4)
    time.sleep(0.05)
    assert plugin.pl.get_status()["AHRS Counter Gaps"] == 1


def test_the_plugin_never_transmits(database):
    pl = make_plugin()
    pl.start()
    listener = can.Bus("tcan_avionicsduino", interface="virtual")
    sender = can.Bus("tcan_avionicsduino", interface="virtual")
    try:
        time.sleep(0.1)
        sent = 0
        for tick in range(20):
            for canid, data in sim.frames_for_tick(tick, tick / sim.TICK_HZ):
                send(sender, canid, data)
                sent += 1
        assert wait_for(lambda: pl.recvcount == sent)
        seen = 0
        while listener.recv(0.2) is not None:
            seen += 1
        # The listener hears the sender's frames and nothing else.
        assert seen == sent
    finally:
        pl.stop()
        listener.shutdown()
        sender.shutdown()


def test_fuzz_soak_never_kills_the_thread(plugin, database):
    rng = random.Random(42)
    for _ in range(500):
        canid, data = sim.fuzz_frame(rng)
        send(plugin.bus, canid, data)
    assert wait_for(lambda: plugin.pl.recvcount >= 500, timeout=5.0)
    assert plugin.pl.thread.is_alive()
    plugin.pl.get_status()


def test_unknown_config_option_warns(database, caplog):
    make_plugin("node_timeout: 2.0\n")
    assert "Unknown config option 'node_timeout'" in caplog.text


def test_missing_mapfile_raises(database):
    cc, cc_meta = cfg.from_yaml(
        CONFIG_DATA.replace("ahrs21_rm3_rdam12.yaml", "no_such.yaml"), metadata=True
    )
    with pytest.raises(ValueError):
        fixgw.plugins.avionicsduino.Plugin("avionicsduino", cc, cc_meta)


def test_stop_before_run_is_safe(database):
    make_plugin().stop()


def test_stop_failure_raises_pluginfail(database):
    pl = make_plugin()

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


# --- shipped config -----------------------------------------------------


def shipped_connection():
    with open("src/fixgw/config/connections/avionicsduino.yaml") as f:
        return yaml.safe_load(f)["avionicsduino"]


def test_shipped_connection_ships_off():
    body = shipped_connection()
    assert body["load"] == "AVIONICSDUINO"
    with open("src/fixgw/config/preferences.yaml") as f:
        prefs = yaml.safe_load(f)
    assert prefs["enabled"]["AVIONICSDUINO"] is False
    assert body["rdam_altitude"] is False
    assert body["rdam_vertical_speed"] is False
    assert body["smoothing_tau"] == 0.0


def test_shipped_connection_builds_the_plugin(database):
    body = shipped_connection()
    body.pop("load")
    body.pop("module")
    body["CONFIGPATH"] = "src/fixgw/config"
    pl = fixgw.plugins.avionicsduino.Plugin("avionicsduino", body, None)
    assert pl.mapping.options == {"rdam_altitude": False, "rdam_vertical_speed": False}
    assert pl.mapping.smoothing_tau == 0.0


# --- simulator ----------------------------------------------------------


def test_sim_sends_each_id_at_its_source_rate():
    counts = {}
    for tick in range(int(sim.TICK_HZ)):
        for canid, _ in sim.frames_for_tick(tick, tick / sim.TICK_HZ):
            counts[canid] = counts.get(canid, 0) + 1
    for canid, layout in protocol.LAYOUTS.items():
        assert counts[canid] == (layout.rate_hz or 5), canid


def test_sim_frames_decode_and_carry_the_source_quirks():
    frames = dict(sim.frames_for_tick(0, 70.0))  # heading 300, track 295
    v = protocol.decode(20, frames[20])
    assert v["roll_rad"] == pytest.approx(math.radians(sim.BANK_DEG))
    v = protocol.decode(24, frames[24])
    assert v["track_deg"] == pytest.approx(-65.0, abs=1e-4)  # -180..+180
    assert len(frames[28]) == 4
    assert len(frames[58]) == 8
    v = protocol.decode(48, frames[48])
    assert isinstance(v["ias_kmh"], int)


def test_sim_without_drops_a_node():
    frames = dict(sim.frames_for_tick(0, 0.0, without=("remote",)))
    assert not {28, 42, 43, 44, 45} & set(frames)
    assert 20 in frames
