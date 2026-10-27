"""RS1 (fix-gateway#39): the route segment table -- integrity validation on
every commit, FPLACTSEG/FPLSEGREM, direct-to inside an airway, and
persistence. makerplane/briefs/route_segments_plan.md sections 3, 5 and 9
(the maos-workspace repo).

The route here is synthetic: points along the equator, so leg lengths are
easy to reason about. It is shaped like the PA6/PA16 bench route (a
departure, an airway entered and left at named fixes, a destination); the
idents are invented.
"""

import pytest

import fixgw.geo as geo
import fixgw.plugins.flightplan.engine as engine

from .routes import load

WPT = engine.SEGKIND_WAYPOINT
AWY = engine.SEGKIND_AIRWAY


def nm_to_deg_lon(nm):
    return nm / geo.distance_nm(0.0, 0.0, 0.0, 1.0)


def airway_route():
    """DEP, then airway V1 entered at ENTRY and left at EXIT, then DEST.
    Slots 30 nm apart. Segments: 1 waypoint (DEP), 2 airway (slots 2..5),
    3 waypoint (DEST)."""
    ids = ["DEP", "ENTRY", "MID1", "MID2", "EXIT", "DEST"]
    sgi = [1, 2, 2, 2, 2, 3]
    route = [
        engine.Waypoint(i, 0.0, nm_to_deg_lon(30.0 * n), sgi=k)
        for n, (i, k) in enumerate(zip(ids, sgi))
    ]
    segments = [
        engine.Segment(WPT, "", "DEP", "DEP", 1, 1),
        engine.Segment(AWY, "V1", "ENTRY", "EXIT", 2, 5),
        engine.Segment(WPT, "", "DEST", "DEST", 6, 6),
    ]
    return route, segments


def leg_nm(route, a, b):
    """Great-circle length from slot a to slot b (1-based), along the plan."""
    total = 0.0
    for i in range(a, b):
        total += geo.distance_nm(route[i - 1].lat, route[i - 1].lon, route[i].lat, route[i].lon)
    return total


# ---------------------------------------------------------------------------
# A valid table loads
# ---------------------------------------------------------------------------


def test_waypoint_airway_waypoint_route_loads_with_its_segment_table():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    assert e.count == 6
    assert [(s.kind, s.id, s.ent, s.ext, s.first, s.last) for s in e.segments] == [
        (WPT, "", "DEP", "DEP", 1, 1),
        (AWY, "V1", "ENTRY", "EXIT", 2, 5),
        (WPT, "", "DEST", "DEST", 6, 6),
    ]


def test_every_segment_kind_loads():
    # departure, airway, arrival, approach, missed -- the procedure kinds
    # PA7 will write, with their idents in the table instead of the retired
    # FPLDPID/FPLSTARID/FPLAPRID.
    ids = ["RW33L", "D1", "A1", "A2", "S1", "IAF", "FAF", "MAP", "MA1", "MAHP"]
    sgi = [1, 1, 2, 2, 3, 4, 4, 4, 5, 5]
    route = [engine.Waypoint(i, 0.0, nm_to_deg_lon(5.0 * n), sgi=k) for n, (i, k) in enumerate(zip(ids, sgi))]
    segments = [
        engine.Segment(engine.SEGKIND_DEPARTURE, "HYDRR6.RW33L", "RW33L", "D1", 1, 2),
        engine.Segment(AWY, "V27", "A1", "A2", 3, 4),
        engine.Segment(engine.SEGKIND_ARRIVAL, "SADDE8", "S1", "S1", 5, 5),
        engine.Segment(engine.SEGKIND_APPROACH, "RNAV33L.HENER", "IAF", "MAP", 6, 8),
        engine.Segment(engine.SEGKIND_MISSED, "RNAV33L", "MA1", "MAHP", 9, 10),
    ]
    e = engine.Engine()
    load(e, route, "PROC", 1, segments)
    assert [s.kind for s in e.segments] == [2, 1, 3, 4, 5]


def test_a_full_route_of_entered_waypoints_is_one_row_each():
    # Bill, 2026-10-26: one row per entered waypoint, and the table is as
    # long as the slot block, so 100 entered waypoints take 100 rows and
    # load -- the table is never the limit.
    route = [engine.Waypoint(f"W{i}", 0.0, nm_to_deg_lon(i)) for i in range(100)]
    e = engine.Engine()
    load(e, route, "LONG", 1)
    assert len(e.segments) == 100
    assert all(s.kind == WPT and s.first == s.last == i for i, s in enumerate(e.segments, start=1))


def test_a_waypoint_row_spanning_two_slots_is_rejected():
    route = [
        engine.Waypoint("A", 0.0, 0.0, sgi=1),
        engine.Waypoint("B", 0.0, nm_to_deg_lon(30.0), sgi=1),
    ]
    segments = [engine.Segment(WPT, "", "A", "B", 1, 2)]
    with pytest.raises(engine.RouteRejected) as exc:
        load(engine.Engine(), route, "BAD", 1, segments)
    assert exc.value.reason == "SEGLEN S1 - 2"


def test_empty_route_loads_with_an_empty_table():
    e = engine.Engine()
    load(e, [], "EMPTY", 1, [])
    assert e.count == 0 and e.segments == []


# ---------------------------------------------------------------------------
# Integrity rules -- one negative test per rule, each a whole-commit
# rejection with its reason (guardrail 1). The brief's three named cases
# are the non-contiguous segment, the LAST that disagrees with FPLfSGI,
# and the exit id that does not match the last slot.
# ---------------------------------------------------------------------------


def _broken(mutate):
    route, segments = airway_route()
    mutate(route, segments)
    return route, segments


def _seg(**kw):
    def m(route, segments):
        for k, v in kw.items():
            setattr(segments[1], k, v)
    return m


def _sgi(slot, value):
    def m(route, segments):
        route[slot - 1].sgi = value
    return m


@pytest.mark.parametrize(
    "mutate,reason",
    [
        # SEGCNT: a route with no table, and more rows than slots.
        (lambda r, s: s.clear(), "SEGCNT 0"),
        (lambda r, s: s.extend([engine.Segment()] * 28), "SEGCNT 31"),
        # SEGSGI: a slot pointing at no segment, or past FPLSEGCNT.
        (_sgi(3, 0), "SEGSGI 3 MID1 0"),
        (_sgi(6, 4), "SEGSGI 6 DEST 4"),
        # SEGNONCONTIG: the airway's slots in two runs (V1, waypoint, V1) --
        # named at the first slot of the second run.
        (lambda r, s: (setattr(r[3], "sgi", 3), setattr(r[5], "sgi", 2)), "SEGNONCONTIG S2 EXIT"),
        # SEGORDER: slot 1 not in segment 1; a segment skipped; a trailing
        # segment no slot uses.
        (_sgi(1, 2), "SEGORDER S2 DEP"),
        (lambda r, s: (setattr(r[5], "sgi", 4), s.append(engine.Segment(WPT, "", "DEST", "DEST", 6, 6))),
         "SEGORDER S4 DEST"),
        (lambda r, s: s.append(engine.Segment(WPT, "", "X", "X", 7, 7)), "SEGORDER S4 UNUSED"),
        # SEGKIND: past missed (5).
        (_seg(kind=6), "SEGKIND S2 6"),
        # SEGRANGE: LAST, then FIRST, disagreeing with FPLfSGI.
        (_seg(last=4), "SEGRANGE S2 2-4"),
        (_seg(first=3), "SEGRANGE S2 3-5"),
        # SEGENT/SEGEXT: entry and exit idents that are not the first and
        # last slot's.
        (_seg(ent="MID1"), "SEGENT S2 MID1"),
        (_seg(ext="DEST"), "SEGEXT S2 DEST"),
        # SEGID: an airway without an ident, a waypoint row with one, and an
        # ident past 24 chars.
        (_seg(id=""), "SEGID S2 -"),
        (lambda r, s: setattr(s[0], "id", "V1"), "SEGID S1 V1"),
        (_seg(id="X" * 25), "SEGID S2 " + "X" * 25),
    ],
)
def test_integrity_rule_rejects_the_whole_commit_with_its_reason(mutate, reason):
    route, segments = _broken(mutate)
    assert engine.segment_integrity_reason(route, segments) == reason
    e = engine.Engine()
    with pytest.raises(engine.RouteRejected) as exc:
        load(e, route, "BAD", 1, segments)
    assert exc.value.reason == reason
    assert e.count == 0 and e.segments == []


def test_airway_needs_at_least_two_legs():
    route = [
        engine.Waypoint("DEP", 0.0, 0.0, sgi=1),
        engine.Waypoint("ONLY", 0.0, nm_to_deg_lon(30.0), sgi=2),
    ]
    segments = [engine.Segment(WPT, "", "DEP", "DEP", 1, 1), engine.Segment(AWY, "V1", "ONLY", "ONLY", 2, 2)]
    with pytest.raises(engine.RouteRejected) as exc:
        load(engine.Engine(), route, "BAD", 1, segments)
    assert exc.value.reason == "SEGLEN S2 V1 1"


def test_empty_route_with_a_table_is_rejected():
    with pytest.raises(engine.RouteRejected) as exc:
        load(engine.Engine(), [], "BAD", 1, [engine.Segment(WPT, "", "", "", 1, 1)])
    assert exc.value.reason == "SEGCNT 1"


def test_a_bad_table_leaves_the_previous_route_and_activation_untouched():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "GOOD", 1, segments)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))

    bad_route, bad_segments = _broken(_seg(last=4))
    with pytest.raises(engine.RouteRejected):
        load(e, bad_route, "BAD", 2, bad_segments)

    assert e.route_name == "GOOD" and e.seq == 1
    assert e.mode == "LEG" and e.act_leg == 3
    assert e.segments[1].last == 5


def test_leg_rejection_still_wins_over_a_segment_rejection():
    # Both gates run on one commit; the leg gate is checked first, so a
    # route bad on both reports the leg (the pre-RS1 message, unchanged).
    route, segments = _broken(_seg(last=4))
    route[2].pt = "FC"
    with pytest.raises(engine.RouteRejected) as exc:
        load(engine.Engine(), route, "BAD", 1, segments)
    assert exc.value.reason == "UNSUPP FC MID1"


# ---------------------------------------------------------------------------
# FPLACTSEG / FPLSEGREM
# ---------------------------------------------------------------------------


def _fly(e, lon_nm, gs=120.0, now=1000.0):
    return e.update(0.0, nm_to_deg_lon(lon_nm), gs, 0.0, True, None, None, now)


def test_actseg_and_segrem_inside_the_airway():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))  # ENTRY -> MID1

    out = _fly(e, 45.0)  # halfway along ENTRY->MID1
    assert out["act_seg"] == 2
    # to MID1, then MID1 -> MID2 -> EXIT
    assert out["seg_rem"] == pytest.approx(out["wp_dis"] + leg_nm(route, 3, 5), abs=1e-6)
    assert out["seg_rem"] == pytest.approx(75.0, abs=0.1)


def test_actseg_reads_the_segment_of_the_to_slot_flying_to_the_entry_fix():
    # FPLACTSEG is the segment of the active TO slot. The entry fix is the
    # airway's first slot, so the leg into it already reads the airway.
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    e.handle_command("1 ACT 2", (0.0, 0.0, True))  # DEP -> ENTRY

    out = _fly(e, 10.0)
    assert out["act_seg"] == 2
    assert out["seg_rem"] == pytest.approx(20.0 + 90.0, abs=0.1)


def test_actseg_follows_sequencing_out_of_the_airway():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    e.handle_command("1 ACT 5", (0.0, 0.0, True))  # MID2 -> EXIT

    out = _fly(e, 100.0, now=1000.0)
    assert out["act_seg"] == 2
    assert out["seg_rem"] == pytest.approx(out["wp_dis"], abs=1e-6)  # EXIT is the segment's last slot

    out = _fly(e, 120.5, now=1001.0)  # past EXIT: sequences onto DEST
    assert e.act_leg == 6
    assert out["act_seg"] == 3
    assert out["seg_rem"] == pytest.approx(out["wp_dis"], abs=1e-6)


def test_actseg_zero_with_no_plan_active_and_for_an_off_route_direct_to():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    out = _fly(e, 10.0)
    assert out["act_seg"] == 0 and out["seg_rem"] == 0.0

    e.handle_command("1 DTO", (0.0, 0.0, True), ("ELSEWHERE", 1.0, 1.0, engine.TYPE_USER))
    out = _fly(e, 10.0, now=1001.0)
    assert e.mode == "DIRECT" and e.act_leg == 0
    assert out["act_seg"] == 0 and out["seg_rem"] == 0.0


def test_fail_outputs_keep_the_segment_identity_but_no_distance():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    _fly(e, 45.0, now=1000.0)
    out = e.update(0.0, 0.0, 120.0, 0.0, False, None, None, 1010.0)  # position stale > 5 s
    assert out["fail"] is True
    assert out["act_seg"] == 2 and out["seg_rem"] == 0.0


# ---------------------------------------------------------------------------
# Direct-to a fix inside an airway keeps the rest of it as one segment
# ---------------------------------------------------------------------------


def test_direct_to_a_fix_inside_an_airway_keeps_the_remainder_as_the_same_segment():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    before = [s.to_dict() for s in e.segments]

    ac_lat, ac_lon = 0.2, nm_to_deg_lon(50.0)  # off the airway, abeam MID1/MID2
    ack, msg = e.handle_command("7 DTO 4", (ac_lat, ac_lon, True))  # direct MID2
    assert ack == 7
    out = e.update(ac_lat, ac_lon, 120.0, 0.0, True, None, None, 1000.0)

    assert e.mode == "DIRECT" and e.act_leg == 4
    assert out["act_seg"] == 2  # still V1
    assert [s.to_dict() for s in e.segments] == before  # the table is the editor's; untouched
    direct_nm = geo.distance_nm(ac_lat, ac_lon, route[3].lat, route[3].lon)
    assert out["seg_rem"] == pytest.approx(direct_nm + leg_nm(route, 4, 5), abs=1e-6)

    out = e.update(0.0, nm_to_deg_lon(90.5), 120.0, 0.0, True, None, None, 1001.0)  # past MID2
    assert e.act_leg == 5 and out["act_seg"] == 2  # sequenced along the airway, same segment


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persisted_state_carries_the_segment_table():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    e.handle_command("1 ACT 3", (0.0, 0.0, True))
    snapshot = e.to_persisted_dict()

    e2 = engine.Engine()
    e2.restore_from_dict(snapshot)
    assert [s.to_dict() for s in e2.segments] == [s.to_dict() for s in segments]
    assert [w.sgi for w in e2.route] == [1, 2, 2, 2, 2, 3]
    assert e2.act_leg == 3
    assert _fly(e2, 45.0)["act_seg"] == 2


def test_restore_rejects_a_state_file_whose_table_does_not_describe_its_slots():
    e = engine.Engine()
    route, segments = airway_route()
    load(e, route, "AWY", 1, segments)
    snapshot = e.to_persisted_dict()
    snapshot["segments"][1]["last"] = 4

    e2 = engine.Engine()
    with pytest.raises(engine.RouteRejected) as exc:
        e2.restore_from_dict(snapshot)
    assert exc.value.reason == "SEGRANGE S2 2-4"
    assert e2.count == 0 and e2.mode == "NONE"


def test_restore_migrates_a_pre_rs1_state_file_to_one_waypoint_row_per_slot():
    # A state file written before RS1: per-slot "seg" (PA3's kind) and the
    # block-level procedure idents, no segment table.
    legacy = {
        "route": [
            {"id": "KSBA", "lat": 34.42621, "lon": -119.84037, "seg": 0},
            {"id": "GVO", "lat": 34.53142, "lon": -120.09106, "seg": 0},
            {"id": "KSMX", "lat": 34.89892, "lon": -120.45758, "seg": 0},
        ],
        "name": "OLD", "seq": 9, "dpid": "", "starid": "", "aprid": "",
        "mode": "LEG", "act_leg": 2,
        "from_point": {"lat": 34.42621, "lon": -119.84037, "id": "KSBA"},
        "to_point": {"lat": 34.53142, "lon": -120.09106, "id": "GVO"},
    }
    e = engine.Engine()
    e.restore_from_dict(legacy)
    assert e.route_name == "OLD" and e.act_leg == 2
    assert [w.sgi for w in e.route] == [1, 2, 3]
    assert [s.to_dict() for s in e.segments] == [
        {"kind": WPT, "id": "", "ent": ident, "ext": ident, "first": i, "last": i}
        for i, ident in enumerate(["KSBA", "GVO", "KSMX"], start=1)
    ]
    assert "segments" in e.to_persisted_dict()
