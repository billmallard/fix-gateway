"""Replay a recorded X-Plane flight through the navigation engine, headless.

FP8 (AER-809, billmallard/pyEfis#190): the playback fixture. The trace is
X-Plane 12's own state at 5 Hz from the pilotless sim bench (session
``cbbeafe57646``, 2026-10-29, profile ``fp8-ksmx-rnav30-missed``): position,
altitude, ground speed, track, and X-Plane's GPS navigator (active waypoint,
course, deviation, to/from) as the independent reference.

The engine is flown exactly as the live bench session flew it: the
profile's procedure route published by ``fixgw.sim.fplroute`` (the same
plan the bench loaded), ``ACT`` at the start fix, and the pilot commands the
bench orchestrator issued, re-derived from the engine's own state rather
than from wall-clock times: ``RESUME`` 10 s after the MAP auto-SUSP, ``SUSP``
1 nm before HANNU, ``RESUME`` once the engine shows FROM past it. X-Plane publishes neither
``GPS_FIX_TYPE`` nor ``GPS_ACCURACY_HORIZ``, so the integrity inputs are
``None``, as on the bench.

The bench's own 1 Hz bus log of the same flight (``fp8_bench_bus_*``) is the
recorder output: the live engine's keys beside the ``EXTCRS``/``EXTCDI`` the
gateway's ``xplane`` plugin wrote from the same X-Plane navigator. It pins
two things the trace alone cannot: that the trace's navigator columns are
those bus keys (``test_fp8_bench_bus.py``), and where the live engine
sequenced.
"""

import csv
import gzip
import math
from pathlib import Path

from fixgw.plugins.flightplan.engine import Engine
from fixgw.sim import fplroute
from fixgw.sim.cifp import Cifp
from fixgw.sim.profile import load_profile

from .engine_replay import ENGINE_CONFIG, _route

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "fp8_xplane_2026-10-29.csv.gz"
BENCH_BUS = FIXTURES / "fp8_bench_bus_2026-10-29.csv.gz"
# Trace time is X-Plane network time, the bus log's is the Beelink's epoch:
# trace t = bus t - BUS_EPOCH. Found by position, 0.002 nm mean error.
BUS_EPOCH = 1793251953.66
PROFILE = ROOT / "src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml"
# FIX MAGVAR on the bench bus throughout the flight (median of the 1 Hz log).
MAGVAR = -11.912


def nm(a_lat, a_lon, b_lat, b_lon):
    """Flat-earth distance, nm: fine at the sub-mile scale it is used for."""
    return math.hypot((a_lat - b_lat) * 60.0,
                      (a_lon - b_lon) * 60.0 * math.cos(math.radians(a_lat)))


def load_trace(path=FIXTURE):
    with gzip.open(path, "rt", newline="") as f:
        rows = []
        for r in csv.DictReader(f):
            for k in ("t", "lat", "lon", "alt_ft", "gs_kt", "trk", "xp_crs", "xp_hdef_dot"):
                r[k] = float(r[k])
            r["xp_fromto"] = int(r["xp_fromto"])
            rows.append(r)
        return rows


def load_bench_bus(path=BENCH_BUS):
    """The bench recorder's rows, with ``t`` on the trace's clock. Values are
    as logged: strings, ``""`` for a key never written."""
    with gzip.open(path, "rt", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["t"] = float(r["t"]) - BUS_EPOCH
    return rows


def build_route():
    prof = load_profile(str(PROFILE))
    cifp = Cifp(prof.cifp_file)
    p = prof.procedure
    slots, segs = fplroute.procedure_slots(cifp, p["airport"], p["approach"], p["transition"], prof.magvar_deg)
    db = dict(fplroute.route_block_writes(slots, segs, "KSMX R30 GVO", "RNAV", cifp.cycle, 1))
    wps, segments = _route(db)
    act = next(i for i, s in enumerate(slots, start=1) if s["ID"] == prof.start["fix"] and i > 1)
    return wps, segments, act, cifp.cycle


def fly(rows=None):
    """-> (samples, events). samples: one dict per trace row with the engine
    outputs beside X-Plane's; events: (t, what, detail, lat, lon)."""
    rows = rows if rows is not None else load_trace()
    wps, segs, act, cycle = build_route()
    by_id = {w.id: w for w in wps if w.id}
    e = Engine(dict(ENGINE_CONFIG))
    e.load_route(wps, "KSMX R30 GVO", 1, segs, aprtype="RNAV", dbcyc=cycle)
    cmd = [0]
    events = []

    def command(verb, r):
        cmd[0] += 1
        ack, msg = e.handle_command(f"{cmd[0]} {verb}", (r["lat"], r["lon"], True), None)
        events.append((r["t"], "cmd", f"{verb} -> {msg or ack}", r["lat"], r["lon"]))

    def after_command(r, before):
        out = e.update(r["lat"], r["lon"], r["gs_kt"], MAGVAR, True, None, None, r["t"], r["alt_ft"])
        if before is not None and out["act_leg"] != before["act_leg"]:
            events.append((r["t"], "sequence", f"{before['wp_name']} -> {out['wp_name']}", r["lat"], r["lon"]))
        return out

    hannu = by_id["HANNU"]
    t_map = t_susp_h = None
    done = set()
    prev = None
    samples = []
    for i, r in enumerate(rows):
        if i == 0:
            command(f"ACT {act}", r)
        out = e.update(r["lat"], r["lon"], r["gs_kt"], MAGVAR, True, None, None, r["t"], r["alt_ft"])
        if prev is not None:
            if out["act_leg"] != prev["act_leg"]:
                events.append((r["t"], "sequence", f"{prev['wp_name']} -> {out['wp_name']}", r["lat"], r["lon"]))
            for k in ("state", "phase", "apr"):
                if out[k] != prev[k]:
                    events.append((r["t"], k, f"{prev[k]} -> {out[k]}", r["lat"], r["lon"]))
        # the bench orchestrator's pilot actions, re-derived from engine state
        if out["state"] == 3 and e._map_suspended and t_map is None:
            t_map = r["t"]
        if t_map is not None and "resume_map" not in done and r["t"] - t_map >= 10.0:
            done.add("resume_map")
            command("RESUME", r)
            out = after_command(r, out)
        d_h = nm(r["lat"], r["lon"], hannu.lat, hannu.lon)
        if "resume_map" in done and "susp_h" not in done and out["wp_name"] == "HANNU" and d_h < 1.0:
            done.add("susp_h")
            command("SUSP", r)
            t_susp_h = r["t"]
        if t_susp_h is not None and "resume_h" not in done and (
                out["tf"] == 2 or r["t"] - t_susp_h > 90):
            done.add("resume_h")
            events.append((r["t"], "past HANNU suspended", f"leg {out['act_leg']} tf {out['tf']} wp {out['wp_name']}",
                           r["lat"], r["lon"]))
            command("RESUME", r)
            out = after_command(r, out)
        s = dict(out)
        s.update(t=r["t"], lat=r["lat"], lon=r["lon"], xp_wpt=r["xp_wpt"], xp_crs=r["xp_crs"],
                 xp_hdef_dot=r["xp_hdef_dot"], xp_fromto=r["xp_fromto"])
        samples.append(s)
        prev = out
    return samples, events


def ext_cdi(hdef_dot):
    """EXTCDI as the bench writes it: ``gps_hdef_dot`` x 0.4
    (``connections/xplane.yaml``), held to the key's -1..1 range."""
    return max(-1.0, min(1.0, 0.4 * hdef_dot))


def xplane_sequences(rows):
    """(t, from, to, lat, lon) wherever X-Plane's active waypoint changed."""
    seq = []
    for a, b in zip(rows, rows[1:]):
        if b["xp_wpt"] != a["xp_wpt"]:
            seq.append((b["t"], a["xp_wpt"], b["xp_wpt"], b["lat"], b["lon"]))
    return seq


if __name__ == "__main__":
    rows = load_trace()
    samples, events = fly(rows)
    for ev in events:
        print("%8.2f %-22s %-40s %.5f %.5f" % ev)
    print("-- X-Plane")
    for s in xplane_sequences(rows):
        print("%8.2f %s -> %s %.5f %.5f" % s)
