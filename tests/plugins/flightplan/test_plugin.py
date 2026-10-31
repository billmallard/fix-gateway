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

import fixgw.geo as geo
import fixgw.plugin as plugin_base
import fixgw.plugins.flightplan as flightplan
import fixgw.plugins.flightplan.engine as engine

from .routes import load


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


def write_route(database, waypoints, name="TEST", seq=1, segments=None, **provenance):
    # The editor's commit order: every slot and the segment table, then
    # FPLCOUNT, then FPLSEQ last. Without an explicit table the route goes
    # out as one waypoint row per slot (RS1).
    if segments is None:
        segments = engine.waypoint_segments(waypoints)
    for i, w in enumerate(waypoints, start=1):
        database.write(f"FPL{i}ID", w.id)
        database.write(f"FPL{i}LAT", w.lat)
        database.write(f"FPL{i}LON", w.lon)
        database.write(f"FPL{i}TYPE", w.type)
        database.write(f"FPL{i}PT", w.pt)
        database.write(f"FPL{i}CRS", w.crs)
        database.write(f"FPL{i}DST", w.dst)
        database.write(f"FPL{i}ALT", w.alt)
        database.write(f"FPL{i}SPD", w.spd)
        database.write(f"FPL{i}SGI", w.sgi)
        database.write(f"FPL{i}FLAGS", w.flags)
        database.write(f"FPL{i}CTRLAT", w.ctrlat)
        database.write(f"FPL{i}CTRLON", w.ctrlon)
        database.write(f"FPL{i}TURN", w.turn)
    for k, seg in enumerate(segments, start=1):
        database.write(f"FPLS{k}KIND", seg.kind)
        database.write(f"FPLS{k}ID", seg.id)
        database.write(f"FPLS{k}ENT", seg.ent)
        database.write(f"FPLS{k}EXT", seg.ext)
        database.write(f"FPLS{k}FIRST", seg.first)
        database.write(f"FPLS{k}LAST", seg.last)
    database.write("FPLSEGCNT", len(segments))
    database.write("FPLCOUNT", len(waypoints))
    database.write("FPLNAME", name)
    database.write("FPLAPRTYPE", provenance.get("aprtype", ""))
    database.write("FPLDBCYC", provenance.get("dbcyc", ""))
    database.write("FPLSEQ", seq)


def test_route_block_committed_on_fplseq_change_loads_engine_route(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    waypoints = [engine.Waypoint("KSBA", 34.42621, -119.84037), engine.Waypoint("KSMX", 34.89892, -120.45758)]
    write_route(database, waypoints, name="TESTROUTE", seq=7)

    assert pl.thread.engine.count == 2
    assert pl.thread.engine.route_name == "TESTROUTE"
    assert pl.thread.engine.seq == 7
    assert [w.id for w in pl.thread.engine.route] == ["KSBA", "KSMX"]


def test_route_block_committed_loads_leg_fields_and_provenance(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    waypoints = [
        engine.Waypoint("IAF", 0.0, 0.0, type=engine.TYPE_FIX, pt="IF", sgi=1),
        engine.Waypoint(
            "FAF", 0.0, 1.0, type=engine.TYPE_FIX, pt="CF", crs=90.0, dst=5.0,
            alt="+3500", spd=150, sgi=1, flags=engine.FLAG_FAF,
        ),
    ]
    segments = [engine.Segment(engine.SEGKIND_APPROACH, "I33L", "IAF", "FAF", 1, 2)]
    write_route(database, waypoints, seq=1, segments=segments, aprtype="ILS", dbcyc="2609")

    loaded = pl.thread.engine.route[1]
    assert loaded.pt == "CF" and loaded.crs == 90.0 and loaded.dst == 5.0
    assert loaded.alt == "+3500" and loaded.spd == 150
    assert loaded.flags == engine.FLAG_FAF
    assert loaded.sgi == 1
    seg = pl.thread.engine.segments[0]
    assert (seg.kind, seg.id, seg.ent, seg.ext) == (engine.SEGKIND_APPROACH, "I33L", "IAF", "FAF")
    assert pl.thread.engine.aprtype == "ILS"
    assert pl.thread.engine.dbcyc == "2609"


def test_route_block_loads_pa13_arc_and_turn_fields(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    waypoints = [
        engine.Waypoint("A", 34.0, -120.1667),
        engine.Waypoint(
            "ARC", 34.1667, -120.0, pt="RF", dst=10.0, turn="R", ctrlat=34.0, ctrlon=-120.0,
        ),
    ]
    write_route(database, waypoints, seq=1)

    loaded = pl.thread.engine.route[1]
    assert loaded.pt == "RF" and loaded.turn == "R"
    assert loaded.ctrlat == pytest.approx(34.0) and loaded.ctrlon == pytest.approx(-120.0)


def test_ca_leg_terminates_on_the_alt_key_via_the_real_wire(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    database.write("GPS_FIX_TYPE", 3)
    waypoints = [
        engine.Waypoint("A", 0.0, 0.0, type=engine.TYPE_AIRPORT),
        engine.Waypoint("CALVL", 0.1, 0.1, pt="CA", crs=90.0, alt="+3000"),
        engine.Waypoint("B", 0.0, 1.0, type=engine.TYPE_AIRPORT),
    ]
    write_route(database, waypoints, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", 0.0)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")

    database.write("ALT", 2000.0)
    pl.thread._run_update_cycle()
    assert pl.thread.engine.act_leg == 2  # below the coded altitude -- still on the CA leg

    database.write("ALT", 3200.0)
    pl.thread._run_update_cycle()
    assert pl.thread.engine.act_leg == 3
    assert database.read("FPLMSG")[0] == "SEQ ALT -> B"


def test_route_with_unsupported_leg_type_rejected_writes_fplmsg_keeps_previous_route(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    good = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)]
    write_route(database, good, name="GOOD", seq=1)
    assert pl.thread.engine.route_name == "GOOD"

    bad = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("ARC", 0.0, 1.0, pt="FC")]
    write_route(database, bad, name="BAD", seq=2)

    assert pl.thread.engine.route_name == "GOOD"  # guardrail 1 -- no partial load
    assert pl.thread.engine.seq == 1
    assert database.read("FPLMSG")[0] == "UNSUPP FC ARC"


def test_vector_leg_annunciates_vectors_and_suspends_via_plugin(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    database.write("GPS_FIX_TYPE", 3)
    waypoints = [
        engine.Waypoint("A", 0.0, 0.0, type=engine.TYPE_AIRPORT),
        engine.Waypoint("VEC", 0.0, 0.05, type=engine.TYPE_FIX, pt="VA", crs=90.0),
    ]
    write_route(database, waypoints, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", 0.0)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")

    pl.thread._run_update_cycle()

    assert database.read("FPLPHASE")[0] == "VECTORS"
    assert database.read("FPLSTATE")[0] == engine.STATE_SUSP
    assert database.read("FPLCRS")[0] == pytest.approx(0.0)
    assert database.read("FPLXTK")[0] == pytest.approx(0.0)


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
    database.write("LAT", 0.0)
    database.write("LONG", 0.0)
    database.write("FPLCMD", "1 ACT 2")
    database.write("GS", 20.0)
    pl.thread._run_update_cycle()

    item = database.get_raw_item("WPETE")
    assert item.bad is True


def test_persistence_restore_republishes_route_block_and_fplseq(database, tmp_path):
    state_file = tmp_path / "flightplan_state.json"
    e = engine.Engine()
    route = [
        engine.Waypoint("A", 1.0, 2.0),
        engine.Waypoint("B", 3.0, 4.0, pt="CF", crs=45.0, flags=engine.FLAG_FAF),
    ]
    load(e, route, "RESTORED", 42, dbcyc="2609")
    e.handle_command("1 ACT 2", (1.0, 2.0, True))
    state_file.write_text(json.dumps(e.to_persisted_dict()))

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, state_file=str(state_file)), {})
    pl.thread._restore()

    assert database.read("FPLSEQ")[0] == 42
    assert database.read("FPLCOUNT")[0] == 2
    assert database.read("FPL1ID")[0] == "A"
    assert database.read("FPL2ID")[0] == "B"
    assert database.read("FPL2PT")[0] == "CF"
    assert database.read("FPL2CRS")[0] == pytest.approx(45.0)
    assert database.read("FPL2FLAGS")[0] == engine.FLAG_FAF
    assert database.read("FPL2SGI")[0] == 2
    assert database.read("FPLSEGCNT")[0] == 2
    assert database.read("FPLS2KIND")[0] == engine.SEGKIND_WAYPOINT
    assert database.read("FPLS2ENT")[0] == "B" and database.read("FPLS2EXT")[0] == "B"
    assert database.read("FPLS2FIRST")[0] == 2 and database.read("FPLS2LAST")[0] == 2
    assert database.read("FPLDBCYC")[0] == "2609"
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
    load(e, route, "RESTORED", 42)
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
    load(e, route, "TEST", 1)
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


def _persist_active_plan(state_file):
    e = engine.Engine()
    route = [
        engine.Waypoint("KSBA", 34.42621, -119.84037),
        engine.Waypoint("GVO", 34.53142, -120.09106),
        engine.Waypoint("KSMX", 34.89892, -120.45758),
    ]
    load(e, route, "INFLIGHT", 9)
    e.handle_command("1 ACT 2", (34.45, -119.9, True))
    snapshot = e.to_persisted_dict()
    state_file.write_text(json.dumps(snapshot))
    return snapshot


def test_position_feed_during_restore_delay_does_not_overwrite_the_persisted_plan(database, tmp_path):
    # fix-gateway#50 (G48): a real GPS streams from the moment the gateway
    # starts, so LAT/LONG callbacks run the guidance cycle -- and its persist
    # hook -- inside restore_delay, while the engine is still empty. Without
    # the guard, the empty snapshot replaced the state file before _restore()
    # read it and an in-flight restart came back with no plan (FP8, AER-809).
    state_file = tmp_path / "flightplan_state.json"
    snapshot = _persist_active_plan(state_file)

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, restore_delay=1.0), {})
    pl.run()
    try:
        # A 5 Hz position feed through the restore window, as on the bench.
        deadline = time.time() + 0.3
        while time.time() < deadline:
            database.write("LAT", 34.45)
            database.write("LONG", -119.9)
            time.sleep(0.2)
        assert pl.thread._position_seen  # the callbacks really ran
        assert pl.thread.engine.count == 0  # still inside the window
        assert json.loads(state_file.read_text()) == snapshot

        deadline = time.time() + 3.0
        while pl.thread.engine.count == 0 and time.time() < deadline:
            time.sleep(0.05)
    finally:
        pl.stop()

    assert pl.thread.engine.route_name == "INFLIGHT"
    assert pl.thread.engine.act_leg == 2
    assert database.read("FPLCOUNT")[0] == 3
    assert database.read("FPLSEQ")[0] == 9
    assert json.loads(state_file.read_text())["route"] == snapshot["route"]


def test_stop_inside_restore_delay_does_not_overwrite_the_persisted_plan(database, tmp_path):
    # The same window from the other end: run() flushes on exit, so a gateway
    # stopped (or restarted again) before the restore ran must not write its
    # still-empty engine over the plan it never read.
    state_file = tmp_path / "flightplan_state.json"
    snapshot = _persist_active_plan(state_file)

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, restore_delay=5.0), {})
    pl.run()
    time.sleep(0.1)
    pl.stop()

    assert pl.thread.engine.count == 0
    assert json.loads(state_file.read_text()) == snapshot


def test_persistence_flush_writes_atomically_on_change(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    pl.thread._restore()  # no state file: nothing to restore, persistence opens
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


# ---------------------------------------------------------------------------
# RS1 (fix-gateway#39): the segment table on the real wire
# ---------------------------------------------------------------------------


def _airway_route():
    route = [
        engine.Waypoint("DEP", 0.0, 0.0, sgi=1),
        engine.Waypoint("ENTRY", 0.0, 0.5, sgi=2),
        engine.Waypoint("MID", 0.0, 1.0, sgi=2),
        engine.Waypoint("EXIT", 0.0, 1.5, sgi=2),
        engine.Waypoint("DEST", 0.0, 2.0, sgi=3),
    ]
    segments = [
        engine.Segment(engine.SEGKIND_WAYPOINT, "", "DEP", "DEP", 1, 1),
        engine.Segment(engine.SEGKIND_AIRWAY, "V1", "ENTRY", "EXIT", 2, 4),
        engine.Segment(engine.SEGKIND_WAYPOINT, "", "DEST", "DEST", 5, 5),
    ]
    return route, segments


def test_segment_table_committed_on_the_bus_loads_and_drives_actseg_segrem(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    route, segments = _airway_route()
    write_route(database, route, seq=1, segments=segments)
    assert [s.id for s in pl.thread.engine.segments] == ["", "V1", ""]
    assert [w.sgi for w in pl.thread.engine.route] == [1, 2, 2, 2, 3]

    database.write("GPS_FIX_TYPE", 3)  # 0 is not yet "old" and would fail guidance
    database.write("LAT", 0.0)
    database.write("LONG", 0.75)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 3")  # ENTRY -> MID
    pl.thread._run_update_cycle()

    assert database.read("FPLACTSEG")[0] == 2  # V1
    one_deg_nm = 60.04
    assert database.read("FPLSEGREM")[0] == pytest.approx(0.25 * one_deg_nm + 0.5 * one_deg_nm, rel=1e-3)


def test_bad_segment_table_on_the_bus_rejects_the_commit_with_its_reason(database, tmp_path):
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    route, segments = _airway_route()
    write_route(database, route, name="GOOD", seq=1, segments=segments)

    route, segments = _airway_route()
    segments[1].ext = "DEST"  # exit id does not match the last slot
    write_route(database, route, name="BAD", seq=2, segments=segments)

    assert database.read("FPLMSG")[0] == "SEGEXT S2 DEST"
    assert pl.thread.engine.route_name == "GOOD"
    assert pl.thread.engine.seq == 1


def test_restore_of_a_bad_state_file_posts_the_reason_and_keeps_the_file(database, tmp_path):
    state_file = tmp_path / "flightplan_state.json"
    e = engine.Engine()
    route, segments = _airway_route()
    load(e, route, "AWY", 3, segments)
    snapshot = e.to_persisted_dict()
    snapshot["segments"][1]["first"] = 3
    state_file.write_text(json.dumps(snapshot))

    pl = flightplan.Plugin("flightplan", make_config(tmp_path, state_file=str(state_file)), {})
    pl.thread._restore()
    assert database.read("FPLMSG")[0] == "SEGRANGE S2 3-4"
    assert pl.thread.engine.count == 0

    pl.thread._run_update_cycle()  # the persist hook must not overwrite the evidence
    assert json.loads(state_file.read_text())["segments"][1]["first"] == 3


# ---------------------------------------------------------------------------
# Integrity gate through the database (fix-gateway#52)
# ---------------------------------------------------------------------------

FT_PER_NM = flightplan.GPS_ACCURACY_FT_PER_NM


def _nm_lon(nm):
    # Along the equator, for building synthetic straight-line routes.
    return nm / geo.distance_nm(0.0, 0.0, 0.0, 1.0)


def _phase_case(phase):
    # (route, activation command, aircraft longitude, HAL in nm) for one
    # flight phase, each with the aircraft on the active leg.
    if phase == "ENR":
        route = [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, _nm_lon(200.0))]
        return route, "1 ACT 2", _nm_lon(100.0), 2.0
    if phase == "TERM":
        route = [
            engine.Waypoint("A", 0.0, 0.0, type=engine.TYPE_AIRPORT),
            engine.Waypoint("B", 0.0, _nm_lon(5.0), type=engine.TYPE_AIRPORT),
        ]
        return route, "1 ACT 2", _nm_lon(2.0), 1.0
    # LNAV: IAF -> FAF -> MAP -> MAHP, aircraft on the FAF -> MAP leg.
    route = [
        engine.Waypoint("IAF", 0.0, 0.0, type=engine.TYPE_FIX, flags=engine.FLAG_IAF),
        engine.Waypoint("FAF", 0.0, _nm_lon(5.0), type=engine.TYPE_FIX, flags=engine.FLAG_FAF),
        engine.Waypoint("MAP", 0.0, _nm_lon(10.0), type=engine.TYPE_MAPPOINT, flags=engine.FLAG_MAP),
        engine.Waypoint("MAHP", 0.0, _nm_lon(12.0), type=engine.TYPE_FIX, flags=engine.FLAG_MAHP),
    ]
    return route, "1 ACT 3", _nm_lon(7.0), 0.3


def _guide_with_accuracy(database, pl, accuracy_ft):
    database.write("GPS_ACCURACY_HORIZ", accuracy_ft)
    pl.thread._run_update_cycle()
    return database.read("FPLINTEG")[0], database.read("FPLPHASE")[0]


@pytest.mark.parametrize("phase", ["ENR", "TERM", "LNAV"])
def test_accuracy_above_each_hal_written_to_the_database_raises_loi(database, tmp_path, phase):
    # GPS_ACCURACY_HORIZ is published in feet and was capped at 200 ft
    # (0.033 nm), so every write clamped below every HAL and the gate could
    # never fire (FP8 on the bench, AER-809). Drive the key through the real
    # database, not Engine.update, so the key's own range is under test.
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    route, cmd, lon, hal_nm = _phase_case(phase)
    database.write("GPS_FIX_TYPE", 3)
    write_route(database, route, seq=1)
    database.write("LAT", 0.0)
    database.write("LONG", lon)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", cmd)

    below_ft = 0.9 * hal_nm * FT_PER_NM
    assert _guide_with_accuracy(database, pl, below_ft) == (True, phase)

    above_ft = 1.1 * hal_nm * FT_PER_NM
    assert _guide_with_accuracy(database, pl, above_ft) == (False, "LOI")
    assert database.read("GPS_ACCURACY_HORIZ")[0] == pytest.approx(above_ft)

    # A receiver reporting more than the key can hold saturates at its max,
    # and the max itself must still be above every HAL.
    assert _guide_with_accuracy(database, pl, 1.0e7) == (False, "LOI")


# ---------------------------------------------------------------------------
# Navigation input staleness (AER-4217, QA1 F4)
# ---------------------------------------------------------------------------


class _Clock:
    """Wall clock for fixgw.database's item ages and the plugin's cycle
    time alike -- both read time.time()."""

    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _flying(database, tmp_path, monkeypatch):
    clock = _Clock(time.time())  # the database stamped its initial values with the real clock
    monkeypatch.setattr(time, "time", clock)
    pl = flightplan.Plugin("flightplan", make_config(tmp_path), {})
    write_route(database, [engine.Waypoint("A", 0.0, 0.0), engine.Waypoint("B", 0.0, 1.0)], seq=1)
    database.write("GS", 120.0)
    database.write("MAGVAR", 0.0)
    database.write("FPLCMD", "1 ACT 2")
    return pl, clock


def _gps_epoch(database, lon, fix_type=3):
    # One receiver epoch as gpsd publishes it.
    database.write("GPS_FIX_TYPE", fix_type)
    database.write("LAT", 0.0)
    database.write("LONG", lon)


def _first_flag_after(pl, database, clock, seconds, step=0.2, each_step=None):
    """Advance the clock in `step`s, one guidance cycle per step (finer than
    the plugin's own 1 Hz tick, so this finds the earliest flag the inputs
    allow); return the seconds elapsed at the first flagged FPLCRS, or None."""
    t0 = clock.t
    for _ in range(int(round(seconds / step))):
        clock.t += step
        if each_step is not None:
            each_step(clock.t - t0)
        pl.thread._run_update_cycle()
        if database.get_raw_item("FPLCRS").fail:
            return clock.t - t0
    return None


def test_dead_gps_link_flags_guidance_within_the_position_bound(database, tmp_path, monkeypatch):
    # QA1 F4: the receiver (or its link) stops sending with the last fix type
    # still 3D. Nothing writes GPS_FIX_TYPE, LAT or LONG again. The guidance
    # must flag no later than the engine's position bound after the last fix
    # -- not after LAT/LONG's database tol plus the bound (20 s + 5 s before
    # AER-4217).
    pl, clock = _flying(database, tmp_path, monkeypatch)
    for i in range(5):
        clock.t += 1.0
        _gps_epoch(database, 0.002 * i)
        pl.thread._run_update_cycle()
    assert database.get_raw_item("FPLCRS").fail is False

    flagged = _first_flag_after(pl, database, clock, 40.0)

    assert flagged is not None
    assert flagged <= 2.0 + 0.2 + 1e-6  # engine.POSITION_TIMEOUT_S carries the derivation


def test_one_hz_receiver_with_jitter_never_flags(database, tmp_path, monkeypatch):
    # The bound must not trip on the slowest healthy source: a 1 Hz receiver
    # whose epochs land up to half an epoch late (serial burst, gpsd queue).
    pl, clock = _flying(database, tmp_path, monkeypatch)
    _gps_epoch(database, 0.0)
    gaps = [1.0, 1.5, 0.5, 1.5, 1.0, 1.5, 0.5, 1.0] * 4
    lon = 0.0
    for gap in gaps:
        for _ in range(int(round(gap / 0.1))):
            clock.t += 0.1
            pl.thread._run_update_cycle()
            assert database.get_raw_item("FPLCRS").fail is False
        lon += 0.0005 * gap
        _gps_epoch(database, lon)


def test_fix_type_that_goes_silent_while_position_flows_flags_guidance(database, tmp_path, monkeypatch):
    # The mavlink plugin takes LAT/LONG from GLOBAL_POSITION_INT (the
    # autopilot's fused estimate) and GPS_FIX_TYPE from GPS_RAW_INT -- two
    # streams. If the GPS stream stops, position keeps flowing as a dead-
    # reckoned estimate with no fix behind it. A fix-type key that WAS
    # publishing and went silent is a failed input, not "this installation
    # has no fix-type source".
    pl, clock = _flying(database, tmp_path, monkeypatch)
    _gps_epoch(database, 0.0)
    pl.thread._run_update_cycle()
    assert database.get_raw_item("FPLCRS").fail is False

    def position_only(elapsed):
        database.write("LAT", 0.0)
        database.write("LONG", 0.0001 * elapsed)

    flagged = _first_flag_after(pl, database, clock, 30.0, each_step=position_only)

    tol_s = database.get_raw_item("GPS_FIX_TYPE").tol / 1000.0
    assert flagged is not None
    assert flagged <= tol_s + 0.2 + 1e-6


def test_fix_type_never_published_still_skips_the_gate(database, tmp_path, monkeypatch):
    # The bench's X-Plane FMS source writes position but never GPS_FIX_TYPE;
    # the key going "old" from its startup default must keep meaning "no
    # fix-type source here", as before.
    pl, clock = _flying(database, tmp_path, monkeypatch)

    def position_only(elapsed):
        database.write("LAT", 0.0)
        database.write("LONG", 0.0001 * elapsed)

    # Until the startup default 0 goes old it reads as "no fix" (the grace
    # window test_position_updates_write_guidance_outputs works around).
    clock.t += database.get_raw_item("GPS_FIX_TYPE").tol / 1000.0 + 0.1
    position_only(0.0)
    pl.thread._run_update_cycle()
    assert database.get_raw_item("FPLCRS").fail is False

    assert _first_flag_after(pl, database, clock, 10.0, each_step=position_only) is None


def test_dead_position_link_without_a_fix_type_source_flags_within_the_bound(database, tmp_path, monkeypatch):
    # The same dead link from a source with no fix-type key (the bench's
    # X-Plane FMS): only the position's own age can catch it.
    pl, clock = _flying(database, tmp_path, monkeypatch)
    clock.t += database.get_raw_item("GPS_FIX_TYPE").tol / 1000.0 + 0.1
    for i in range(5):
        clock.t += 1.0
        database.write("LAT", 0.0)
        database.write("LONG", 0.002 * i)
        pl.thread._run_update_cycle()
    assert database.get_raw_item("FPLCRS").fail is False

    flagged = _first_flag_after(pl, database, clock, 40.0)

    assert flagged is not None
    assert flagged <= 2.0 + 0.2 + 1e-6
