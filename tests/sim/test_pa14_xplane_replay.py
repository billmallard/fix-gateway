"""PA14 (AER-1613, billmallard/pyEfis#242): the procedures-and-airways join,
replayed from X-Plane.

One IFR route, built on the Beelink's PROC page and airway picker and read
back off the bus (``fixtures/pa14_route_block.json``, AIRAC 2611): KSBA,
FLOUT5 via RW15B and the VTU transition, V485 VTU-OHIGH, FERN7 via OHIGH,
KSMO RNAV (GPS) RWY 21 with vectors to final, the missed approach to the
SADDE hold. X-Plane flew it in three pilotless sim bench sessions on
2026-11-15 (``fixtures/README.md``); each trace is replayed here through the
engine with the pilot commands the bench sent.

What it pins, in the order the brief's PA14 scope lists it:

- the SID's vector leg suspends, annunciated VECTORS, with nothing invented,
  and RESUME flies on to FLOUT;
- the airway expansion on the bus is V485's published fix order;
- the STAR sequences into the approach, and the approach arms;
- the CDI ramps linearly from 1.0 nm to 0.3 nm over the 2 nm before the FAF
  and holds 0.3 nm to the MAP;
- the engine does not sequence past the MAP, and flies the missed only on
  RESUME;
- course agrees with X-Plane's navigator on every straight leg X-Plane flies
  as published.

Two findings are recorded rather than fixed (PA14 validates, it does not
repair): the route block carries VTU twice (billmallard/pyEfis#474), which
strands the engine at VTU, and FERN7 lost its common route in the pack
(AER-5611), so the box flies FIM-DARTS where X-Plane flies FIM-VNY-DARTS.
"""

import csv
import gzip
import json
from pathlib import Path

import pytest

from fixgw.plugins.flightplan.engine import Engine
from fixgw.sim.cifp import Cifp

from .engine_replay import ENGINE_CONFIG, _route

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BLOCK = FIXTURES / "pa14_route_block.json"
TRACE = {k: FIXTURES / f"pa14_{k}_xplane_2026-11-15.csv.gz" for k in "abc"}
# FIX MAGVAR on the bench's bus during each session (median, X-Plane's own
# variation written over RREF): the engine converts course with it.
MAGVAR = {"a": -11.56, "b": -11.51, "c": -11.34}
SLOT_FIELDS = ("ID", "LAT", "LON", "TYPE", "PT", "CRS", "DST", "ALT", "SPD", "SGI", "FLAGS",
               "CTRLAT", "CTRLON", "TURN")


def load_block():
    return json.loads(BLOCK.read_text())


def load_trace(k):
    with gzip.open(TRACE[k], "rt", newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            for x in ("t", "lat", "lon", "alt_ft", "gs_kt", "trk", "xp_crs", "xp_hdef_dot"):
                r[x] = float(r[x])
            r["xp_fromto"] = int(r["xp_fromto"])
            rows.append(r)
        return rows


def without_duplicate_vtu(block):
    """The block with slot 5 (V485's entry, a second VTU) removed: the airway
    then starts at HENER, the shape pyEfis's ``insert_airway`` builds for an
    entry fix that ends another segment."""
    assert block["FPL4ID"] == block["FPL5ID"] == "VTU"
    b = {k: v for k, v in block.items() if not (k.startswith("FPL") and k[3:4].isdigit())}
    j = 0
    for i in range(1, block["FPLCOUNT"] + 1):
        if i == 5:
            continue
        j += 1
        for f in SLOT_FIELDS:
            b[f"FPL{j}{f}"] = block[f"FPL{i}{f}"]
    b["FPLCOUNT"] = block["FPLCOUNT"] - 1
    for k in range(1, block["FPLSEGCNT"] + 1):
        for f in ("FIRST", "LAST"):
            if block[f"FPLS{k}{f}"] > 5:
                b[f"FPLS{k}{f}"] = block[f"FPLS{k}{f}"] - 1
    b["FPLS3ENT"] = b["FPL5ID"]
    return b


def fly(k, act, pilot=(), block=None):
    """-> (samples, events). ``pilot``: ``("resume_after_s", S)`` (S s after
    the first row) or ``("resume_after_map_s", S)`` (S s after the MAP
    auto-suspend), taken in order."""
    block = block or load_block()
    wps, segs = _route(block)
    e = Engine(dict(ENGINE_CONFIG))
    e.load_route(wps, block["FPLNAME"], 1, segs, aprtype=block["FPLAPRTYPE"], dbcyc=block["FPLDBCYC"])
    rows = load_trace(k)
    events, samples, pending = [], [], list(pilot)
    n, t_map, prev = 0, None, None

    def command(verb, r):
        nonlocal n
        n += 1
        ack, msg = e.handle_command(f"{n} {verb}", (r["lat"], r["lon"], True), None)
        events.append((r["t"], "cmd", verb, msg or ack))

    for i, r in enumerate(rows):
        if i == 0:
            command(f"ACT {act}", r)
        out = e.update(r["lat"], r["lon"], r["gs_kt"], MAGVAR[k], True, None, None, r["t"], r["alt_ft"])
        if prev is not None and out["act_leg"] != prev["act_leg"]:
            events.append((r["t"], "sequence", prev["wp_name"], out["wp_name"]))
        if out["state"] == 3 and e._map_suspended and t_map is None:
            t_map = r["t"]
        if pending:
            what, s = pending[0]
            if (what == "resume_after_s" and r["t"] - rows[0]["t"] >= s) or \
                    (what == "resume_after_map_s" and t_map is not None and r["t"] - t_map >= s):
                command("RESUME", r)
                pending.pop(0)
                before = out
                out = e.update(r["lat"], r["lon"], r["gs_kt"], MAGVAR[k], True, None, None, r["t"], r["alt_ft"])
                if out["act_leg"] != before["act_leg"]:
                    events.append((r["t"], "sequence", before["wp_name"], out["wp_name"]))
        s = dict(out)
        s.update(t=r["t"], xp_wpt=r["xp_wpt"], xp_crs=r["xp_crs"], xp_hdef_dot=r["xp_hdef_dot"])
        samples.append(s)
        prev = out
    return samples, events


def xplane_switches(k):
    rows = load_trace(k)
    return [(b["t"], b["xp_wpt"]) for a, b in zip(rows, rows[1:]) if a["xp_wpt"] != b["xp_wpt"]]


def engine_sequences(events):
    return [(t, to) for t, kind, _frm, to in (e for e in events if e[1] == "sequence")]


# -- the route as the PROC page published it ---------------------------------
def test_route_block_is_the_proc_page_route():
    b = load_block()
    segs = [(b[f"FPLS{k}KIND"], b[f"FPLS{k}ID"]) for k in range(1, b["FPLSEGCNT"] + 1)]
    assert segs == [(0, ""), (2, "FLOUT5.RW15B.VTU"), (1, "V485"), (3, "FERN7.OHIGH"),
                    (4, "RNAV21.VECTORS"), (5, "RNAV21"), (0, "")]
    assert b["FPLDBCYC"] == "2611" and b["FPLAPRTYPE"] == "RNAV"
    assert [b[f"FPL{i}PT"] for i in (2, 3, 4)] == ["VA", "CF", "TF"]


def test_airway_expansion_is_the_published_order():
    """V485 on the bus is the CIFP's ER sequence VTU-HENER-OHIGH, read by the
    reference's own decoder, not pyEfis's."""
    b = load_block()
    k = next(k for k in range(1, b["FPLSEGCNT"] + 1) if b[f"FPLS{k}ID"] == "V485")
    bus = [b[f"FPL{i}ID"] for i in range(b[f"FPLS{k}FIRST"], b[f"FPLS{k}LAST"] + 1)]
    cifp = [f[0] for f in Cifp(str(FIXTURES / "FAACIFP18-2611-V485")).airway("V485", "VTU", "OHIGH")]
    assert bus == cifp == ["VTU", "HENER", "OHIGH"]


# -- slice A: the SID ----------------------------------------------------------
def test_sid_vector_leg_suspends_and_resume_flies_on_to_flout():
    samples, events = fly("a", act=2, pilot=[("resume_after_s", 8)])
    on_va = [s for s in samples if s["t"] - samples[0]["t"] < 7]
    assert on_va and all(s["state"] == 3 and s["phase"] == "VECTORS" and s["act_leg"] == 2 for s in on_va)
    assert all(s["crs"] == 0 and s["xtk"] == 0 and s["wp_dis"] == 0 for s in on_va)   # nothing invented
    resume = next(e for e in events if e[2] == "RESUME")
    assert resume[3] == "RESUME NAV -> FLOUT"
    after = next(s for s in samples if s["t"] > resume[0] + 1)
    assert after["state"] == 1 and after["wp_name"] == "FLOUT" and after["phase"] != "VECTORS"
    assert abs(after["crs"] - 197.0) < 0.5    # the CF's coded course (fix-gateway#174)


def test_published_block_strands_the_engine_at_vtu_pyefis_474():
    """The block carries VTU twice. On it the engine never sequences VTU,
    though X-Plane did; with the duplicate removed it sequences within 10 s
    of X-Plane. Regenerate the block when pyEfis#474 is fixed: this test then
    has to go, and the HENER sequence joins the one below."""
    xp_hener = next(t for t, w in xplane_switches("a") if w == "HENER")
    _, published = fly("a", act=2, pilot=[("resume_after_s", 8)])
    assert [w for _, w in engine_sequences(published)] == ["FLOUT", "VTU"]
    _, fixed = fly("a", act=2, pilot=[("resume_after_s", 8)], block=without_duplicate_vtu(load_block()))
    t = next(t for t, w in engine_sequences(fixed) if w == "HENER")
    assert 0 <= t - xp_hener <= 10


def test_flout_vtu_sequences_with_xplane():
    _, events = fly("a", act=2, pilot=[("resume_after_s", 8)])
    xp_vtu = next(t for t, w in xplane_switches("a") if w == "VTU")
    t = next(t for t, w in engine_sequences(events) if w == "VTU")
    assert abs(t - xp_vtu) <= 10


# -- slice B: the airway into the STAR, the STAR into the approach -------------
def test_airway_and_star_sequence_with_xplane():
    """The engine sequences about 5 s after X-Plane at each fix (X-Plane
    switches ~0.2 nm early, XPB-001). At FIM the box goes to DARTS, the
    approach's IF, where X-Plane goes to VNY (FERN7's dropped common route,
    AER-5611)."""
    _, events = fly("b", act=6)
    xp = xplane_switches("b")
    eng = engine_sequences(events)
    assert [w for _, w in eng] == ["OHIGH", "CANYN", "FIM", "DARTS"]
    for (te, we), (tx, wx) in zip(eng, xp):
        assert (we, wx) in (("OHIGH", "OHIGH"), ("CANYN", "CANYN"), ("FIM", "FIM"), ("DARTS", "VNY"))
        assert 0 <= te - tx <= 10


def test_approach_arms_before_the_star_ends():
    samples, events = fly("b", act=6)
    t_darts = next(t for t, w in engine_sequences(events) if w == "DARTS")
    armed = next(s["t"] for s in samples if s["apr"] == 1)
    assert armed < t_darts


def test_truncated_star_offset_is_bounded():
    """FIM-DARTS against X-Plane's FIM-VNY-DARTS: the box reads up to 0.74 nm
    off the path X-Plane flies. Recorded for AER-5611; when the pack carries
    FERN7's common route this leg changes and the fixture is re-flown."""
    xtk = [abs(s["xtk"]) for k, a in (("b", 6), ("c", 10)) for s in fly(k, act=a)[0] if s["act_leg"] == 10]
    assert 0.6 < max(xtk) < 0.8


# -- slice C: the approach, the MAP, the missed --------------------------------
@pytest.fixture(scope="module")
def slice_c():
    return fly("c", act=10, pilot=[("resume_after_map_s", 8)])


def test_cdi_ramps_over_the_two_nm_before_the_faf(slice_c):
    samples, _ = slice_c
    to_faf = [s for s in samples if s["wp_name"] == "MOVVE" and s["state"] == 1]
    assert all(s["cdiscale"] == pytest.approx(1.0) for s in to_faf if s["wp_dis"] > 2.05)
    ramp = [s for s in to_faf if 0.05 < s["wp_dis"] < 1.95]
    assert len(ramp) > 50
    for s in ramp:
        assert s["cdiscale"] == pytest.approx(0.3 + 0.35 * s["wp_dis"], abs=0.01)
        assert s["apr"] == 2 and s["phase"] == "LNAV"
    final = [s for s in samples if s["wp_name"] in ("MIPTE", "RW21") and s["apr"] == 2]
    assert final and all(s["cdiscale"] == pytest.approx(0.3) for s in final)


def test_no_auto_sequence_past_the_map(slice_c):
    """Suspended at the MAP until RESUME, and with no RESUME, for good."""
    samples, events = slice_c
    t_susp = next(s["t"] for s in samples if s["state"] == 3 and s["wp_name"] == "RW21")
    t_resume = next(t for t, kind, verb, _ in events if verb == "RESUME")
    held = [s for s in samples if t_susp <= s["t"] < t_resume]
    assert t_resume - t_susp >= 8 and all(s["act_leg"] == 13 and s["state"] == 3 for s in held)
    alone, _ = fly("c", act=10)
    after = [s for s in alone if s["t"] >= t_susp]
    assert after and all(s["act_leg"] == 13 and s["state"] == 3 for s in after)


def test_missed_approach_on_resume_then_the_hold(slice_c):
    samples, events = slice_c
    t_resume = next(t for t, kind, verb, _ in events if verb == "RESUME")
    after = next(s for s in samples if s["t"] > t_resume)
    assert after["apr"] == 3 and after["wp_name"] == "CAPOB" and after["cdiscale"] == pytest.approx(1.0)
    xp = dict((w, t) for t, w in xplane_switches("c"))
    assert abs(t_resume - xp["CAPOB"]) <= 10       # the driver went missed 8 s after the MAP too
    assert [w for _, w in engine_sequences(events)][:5] == ["MOVVE", "MIPTE", "RW21", "CAPOB", "SADDE"]
    assert any(s["phase"] == "HOLD" and s["wp_name"] == "SADDE" for s in samples)


# -- course against X-Plane's navigator ----------------------------------------
# Legs X-Plane does not fly as published (XPB-022: after a plan turn of about
# 135 deg or more, X-Plane's next leg is not the published one). VTU-HENER in
# slice A (135 deg at VTU), and in slice B, whose X-Plane plan turns 149 deg
# at VTU (KSBA-VTU-HENER); OHIGH-CANYN (136 deg at OHIGH).
XPB022 = {("a", "HENER"), ("b", "HENER"), ("b", "CANYN")}


@pytest.mark.parametrize("k,act,pilot", [("a", 2, [("resume_after_s", 8)]), ("b", 6, []),
                                         ("c", 10, [("resume_after_map_s", 8)])], ids=["slice-a", "slice-b", "slice-c"])
def test_course_agrees_with_xplane_on_straight_legs(k, act, pilot):
    """Same TO fix, more than 2 nm from it, not inside a turn or the hold,
    and not within 60 s after either navigator switched. FIM-DARTS is out:
    X-Plane is flying a different leg there (AER-5611)."""
    block = without_duplicate_vtu(load_block()) if k == "a" else None
    samples, _ = fly(k, act, pilot, block=block)
    switches = [samples[i]["t"] for i in range(1, len(samples))
                if samples[i]["act_leg"] != samples[i - 1]["act_leg"] or samples[i]["xp_wpt"] != samples[i - 1]["xp_wpt"]]
    checked = {}
    for s in samples:
        if (s["state"] != 1 or s["wp_name"] != s["xp_wpt"] or s["wp_name"] == "DARTS" or s["wp_dis"] <= 2.0
                or s["phase"] == "HOLD" or any(-5 <= s["t"] - t <= 60 for t in switches)):
            continue
        d = abs((s["crs"] - s["xp_crs"] + 180) % 360 - 180)
        checked.setdefault(s["wp_name"], []).append(d)
    assert checked
    for leg, diffs in checked.items():
        if (k, leg) in XPB022:
            continue
        assert max(diffs) < 0.5, (leg, max(diffs))
