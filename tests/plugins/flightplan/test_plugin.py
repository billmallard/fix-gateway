"""Integration/lifecycle tests for fixgw.plugins.flightplan (FP2,
fix-gateway#23) -- the fixgw.database wiring around the pure Engine class
(see test_engine.py for the navigation-logic tests).

Uses the real `database` fixture (tests/conftest.py, loads the actual
database.yaml) so the route block / command channel / guidance outputs are
exercised through real FIX keys and callbacks, exactly as the gateway does
it -- no mocking of fixgw.database itself.
"""

import json
import time

import pytest

import fixgw.plugin as plugin_base
import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine


def make_config(tmp_path, **overrides):
    config = {
        "CONFIGPATH": str(tmp_path),
        "state_file": str(tmp_path / "flightplan_state.json"),
        "restore_delay": 0.0,
        "rate_hz": 5,
        "integrity_key": "GPS_ACCURACY_HORIZ",
    }
    config.update(overrides)
    return config


def write_route(database, waypoints, name="TEST", seq=1):
    for i, w in enumerate(waypoints, start=1):
        database.write(f"FPL{i}ID", w.id)
        database.write(f"FPL{i}LAT", w.lat)
        database.write(f"FPL{i}LON", w.lon)
        database.write(f"FPL{i}TYPE", w.type)
        database.write(f"FPL{i}ROLE", w.role)
    database.write("FPLCOUNT", len(waypoints))
    database.write("FPLNAME", name)
    database.write("FPLSEQ", seq)


def test_route_block_committed_on_fplseq_change_loads_engine_route(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    waypoints = [engine.Waypoint("KSBA", 34.42621, -119.84037), engine.Waypoint("KSMX", 34.89892, -120.45758)]
    write_route(database, waypoints, name="TESTROUTE", seq=7)

    assert pl.thread.engine.count == 2
    assert pl.thread.engine.route_name == "TESTROUTE"
    assert pl.thread.engine.seq == 7
    assert [w.id for w in pl.thread.engine.route] == ["KSBA", "KSMX"]


def test_command_channel_act_writes_ack_and_engine_state(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    waypoints = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)]
    write_route(database, waypoints, seq=1)

    database.write("LAT", 0.0)
    database.write("LONG", 0.0)
    database.write("FPLCMD", "1 ACT 2")

    assert database.read("FPLCMDACK")[0] == 1
    assert pl.thread.engine.act_leg == 2
    assert pl.thread.engine.mode == "LEG"


def test_command_channel_rejects_bad_slot_with_reason(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    write_route(database, [engine.Waypoint("A", 0.0, 0.0)], seq=1)

    database.write("FPLCMD", "1 ACT 9")

    assert database.read("FPLCMDACK")[0] == -1
    assert database.read("FPLMSG")[0] == "BAD SLOT"


def test_position_updates_write_guidance_outputs(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    # A freshly-initialized GPS_FIX_TYPE defaults to 0 and has not gone "old"
    # yet (that needs its 2s tol to elapse) -- write a good 3-D fix explicitly
    # so this happy-path test doesn't race the integrity gate's "unpublished
    # key" detection (see test_engine.py's integrity-gate tests for that).
    database.write("GPS_FIX_TYPE", 3)
    waypoints = [
        engine.Waypoint("A", 0.0, 0.0, type=engine.TYPE_AIRPORT),
        engine.Waypoint("B", 0.0, 1.0, type=engine.TYPE_AIRPORT),
    ]
    write_route(database, waypoints, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", 0.0)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")

    database.write("LONG", 0.5)
    pl.thread._run_update_cycle()  # bypass the 5Hz rate limiter for a deterministic read

    assert database.read("FPLCRS")[0] == pytest.approx(90.0, abs=0.1)
    assert database.read("FPLTF")[0] == engine.TF_TO
    assert database.read("WPNAME")[0] == "B"
    assert database.read("WPDIS")[0] > 0


def test_position_updates_are_rate_limited_to_configured_hz(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path, rate_hz=5), {})
    write_route(database, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], seq=1)
    database.write("FPLCMD", "1 ACT 2")

    pl.thread._last_update_time = time.time()
    before = database.read("WPDIS")[0]
    database.write("LAT", 0.0)
    database.write("LONG", 0.1)  # within the 200ms rate-limit window
    after = database.read("WPDIS")[0]
    assert after == before  # suppressed by the rate limiter


def test_wpete_bad_flag_set_below_30kt(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    database.write("GPS_FIX_TYPE", 3)
    write_route(database, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], seq=1)
    database.write("FPLCMD", "1 ACT 2")
    database.write("GS", 20.0)
    pl.thread._run_update_cycle()

    item = database.get_raw_item("WPETE")
    assert item.bad is True


def test_persistence_restore_republishes_route_block_and_fplseq(database, tmp_path):
    state_file = tmp_path / "flightplan_state.json"
    e = engine.Engine()
    route = [engine.Waypoint("A", 1.0, 2.0), engine.Waypoint("B", 3.0, 4.0)]
    e.load_route(route, "RESTORED", 42)
    e.handle_command("1 ACT 2", (1.0, 2.0, True))
    state_file.write_text(json.dumps(e.to_persisted_dict()))

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, state_file=str(state_file)), {})
    pl.thread._restore()

    assert database.read("FPLSEQ")[0] == 42
    assert database.read("FPLCOUNT")[0] == 2
    assert database.read("FPL1ID")[0] == "A"
    assert database.read("FPL2ID")[0] == "B"
    assert pl.thread.engine.act_leg == 2
    assert pl.thread.engine.mode == "LEG"


def test_restore_does_not_reset_activation_via_own_fplseq_callback(database, tmp_path):
    # The republish-on-restore write to FPLSEQ must not be re-consumed by this
    # plugin's own FPLSEQ callback -- that would immediately reset the very
    # activation state restore just re-published (fixed via the _restoring
    # guard in fixgw/plugins/flightplan/__init__.py).
    state_file = tmp_path / "flightplan_state.json"
    e = engine.Engine()
    route = [engine.Waypoint("A", 1.0, 2.0), engine.Waypoint("B", 3.0, 4.0)]
    e.load_route(route, "RESTORED", 42)
    e.handle_command("1 ACT 2", (1.0, 2.0, True))
    state_file.write_text(json.dumps(e.to_persisted_dict()))

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, state_file=str(state_file)), {})
    pl.thread._restore()

    assert pl.thread.engine.act_leg == 2  # NOT reset to 0


def test_restore_does_not_auto_sequence_a_direct_to_before_a_real_position_arrives(database, tmp_path):
    # A restored DIRECT-to must survive the plugin's own periodic guidance
    # tick even before any live LAT/LONG has been written this process. LAT/
    # LONG default to 0.0 and are not flagged "old" until their tol elapses
    # (same grace window documented for GPS_FIX_TYPE above), so without this
    # guard the very first post-restore tick computes guidance against (0, 0)
    # -- which reads as far past the DTO target -- and silently auto-
    # sequences the DIRECT-to into a LEG activation on the next route
    # waypoint. Found live on the bench driving this by hand (AER-802): a
    # bench/aircraft restart with no position source connected yet discards
    # the pilot's Direct-To every time.
    state_file = tmp_path / "flightplan_state.json"
    e = engine.Engine()
    route = [
        engine.Waypoint("KSBA", 34.42621, -119.84037),
        engine.Waypoint("GVO", 34.53142, -120.09106),
        engine.Waypoint("KSMX", 34.89892, -120.45758),
    ]
    e.load_route(route, "TEST", 1)
    e.handle_command("1 DTO 1", (34.55, -120.05, True))
    assert e.mode == "DIRECT" and e.act_leg == 1
    state_file.write_text(json.dumps(e.to_persisted_dict()))

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, state_file=str(state_file)), {})
    pl.thread._restore()
    assert pl.thread.engine.mode == "DIRECT"
    assert pl.thread.engine.act_leg == 1

    # The periodic 1Hz tick, with LAT/LONG still at their never-written
    # default (0.0, 0.0) and not yet "old".
    pl.thread._run_update_cycle()

    assert pl.thread.engine.mode == "DIRECT"  # must not have auto-sequenced
    assert pl.thread.engine.act_leg == 1
    assert database.get_raw_item("FPLCRS").fail is True


def test_persistence_flush_writes_atomically_on_change(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    write_route(database, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], seq=1)
    database.write("FPLCMD", "1 ACT 2")

    pl.thread._maybe_persist()

    state_file = tmp_path / "flightplan_state.json"
    assert state_file.exists()
    saved = json.loads(state_file.read_text())
    assert saved["act_leg"] == 2
    assert saved["seq"] == 1


def test_lifecycle_start_stop_real_thread(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path, restore_delay=0.05), {})
    pl.run()
    assert pl.thread.is_alive()
    time.sleep(0.15)  # let it pass the restore point at least once
    pl.stop()
    assert not pl.thread.is_alive()
    assert pl.get_status()["Route"] == ""


def test_stop_raises_pluginfail_when_thread_will_not_join(database, tmp_path, monkeypatch):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    monkeypatch.setattr(pl.thread, "is_alive", lambda: True)
    monkeypatch.setattr(pl.thread, "join", lambda timeout: None)
    monkeypatch.setattr(pl.thread, "stop", lambda: None)

    with pytest.raises(plugin_base.PluginFail):
        pl.stop()


def test_get_status_reports_route_state_and_active_leg(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    write_route(database, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], name="R1", seq=1)
    database.write("FPLCMD", "1 ACT 2")

    status = pl.get_status()
    assert status == {"Route": "R1", "State": "LEG", "ActiveLeg": 2}
