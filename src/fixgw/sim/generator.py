#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Synthetic flight generator (SYN1).
#
"""Profile + CIFP -> a FIX bus stream.

:func:`generate` flies the scenario and returns a :class:`Run`: one row per
tick of the keys a GPS/AHRS would publish (``LAT LONG ALT GS TRACK HEAD TAS
VS PITCH ROLL MAGVAR GPS_FIX_TYPE GPS_ACCURACY_HORIZ``), the reference keys
(``REFCRS REFXTK REFCDI REFDIS REFWPT REFPT``), and the pilot's inputs to the box
under test: the route block once at the start, ``FPLCMD`` to activate the
leg into the start fix, and ``FPLCMD RESUME`` when the pilot goes missed.

:func:`write_recorder_file` writes a run in the ``data_recorder`` JSON-lines
format (``{"frequency", "starttime"}`` header, then one key-to-value row per
tick, values as ``[value, annunciate, old, bad, fail, secfail]``), which
``data_playback`` replays unchanged. :mod:`fixgw.sim.live` (SYN2) streams
``Run.rows`` into a running gateway over netfix instead.
"""

import datetime
import json
from dataclasses import dataclass, field

from fixgw.sim import fplroute, refnav
from fixgw.sim.aircraft import Aircraft, Performance, State
from fixgw.sim.cifp import Cifp
from fixgw.sim.profile import load_profile


class GeneratorError(Exception):
    pass


REF_KEYS = ("REFCRS", "REFXTK", "REFCDI", "REFDIS", "REFWPT", "REFPT")
SENSOR_KEYS = ("MAGVAR", "GPS_FIX_TYPE", "GPS_ACCURACY_HORIZ", "ALT", "GS", "TAS", "VS",
               "TRACK", "HEAD", "PITCH", "ROLL", "LAT", "LONG")
ESTABLISHED_NM = 0.05


@dataclass
class Run:
    profile: object
    frequency_ms: int
    rows: list                      # [(t_s, {key: value, ...}), ...] in write order
    timeline: list = field(default_factory=list)
    sequences: list = field(default_factory=list)


def _alt_constraints(cifp, proc):
    out = {}
    for r in cifp.approach_legs(proc["airport"], proc["approach"]):
        if r.fix and r.alt1 and r.path_term != "HM":
            out.setdefault(r.fix, float(r.alt1))
    return out


def generate(profile, frequency_ms=200, cifp_path=None, fplseq=1, gps_accuracy_ft=30.0, cmd_seq_base=0):
    if isinstance(profile, str):
        profile = load_profile(profile, cifp_override=cifp_path)
    cifp = Cifp(cifp_path or profile.cifp_file)
    if cifp.cycle != profile.cifp_cycle:
        raise GeneratorError(f"profile pins CIFP cycle {profile.cifp_cycle}, file {cifp.path} is {cifp.cycle}")

    proc = profile.procedure
    mv = profile.magvar_deg
    legs = refnav.legs_from_cifp(cifp, proc["airport"], proc["approach"], proc["transition"], mv)
    slots, segments = fplroute.procedure_slots(cifp, proc["airport"], proc["approach"], proc["transition"], mv)
    apt = cifp.fix(proc["airport"], section="PA")

    start_fix = profile.start["fix"]
    try:
        k = next(i for i, lg in enumerate(legs) if lg.ident == start_fix and i > 0)
        act_slot = next(i for i, s in enumerate(slots, start=1) if s["ID"] == start_fix and i > 1)
    except StopIteration:
        raise GeneratorError(f"start fix {start_fix} is not a fix after the first in the procedure")
    prev = legs[k - 1]
    to = legs[k]
    line = refnav.GeoLine.between(prev.lat, prev.lon, to.lat, to.lon)
    before = float(profile.start.get("before_nm", 0.0))
    lat, lon, azi = line.position(max(0.0, line.length_nm - before))

    a = profile.aircraft
    perf = Performance(tas_kt=a.get("tas_kt", 110.0), climb_fpm=a.get("climb_fpm", 700.0),
                       descent_fpm=a.get("descent_fpm", 600.0), bank_max_deg=a.get("bank_max_deg", 25.0),
                       turn_rate_deg_s=a.get("turn_rate_deg_s", 3.0))
    ac = Aircraft(perf, State(lat, lon, float(profile.start["alt_ft"]), azi),
                  profile.wind_from_deg, profile.wind_kt)
    ref = refnav.ReferenceNavigator(legs, mv, (apt.lat, apt.lon), k, perf.tas_kt,
                                    perf.turn_rate_deg_s, perf.bank_max_deg)
    alts = _alt_constraints(cifp, proc)
    missed_top = max([alts.get(lg.ident, 0.0) for lg in legs if lg.first_missed or ref_in_missed(legs, lg)] + [0.0])

    dt = frequency_ms / 1000.0
    rows = []
    timeline = []
    cmd_seq = cmd_seq_base          # a live gateway has already acked earlier commands
    name = f"{proc['airport']} {proc['approach']} {proc['transition']}"[:32]
    route = fplroute.route_block_writes(slots, segments, name, "RNAV", cifp.cycle, fplseq)

    events = {e.id: {"e": e, "state": "pending", "t0": None, "est": None} for e in profile.events}
    leg_start_t = {k: 0.0}
    t = 0.0
    t_map = None
    resume_after = float(profile.pilot.get("missed_after_map_s", 5.0))
    max_s = float(profile.end.get("max_s", 3600.0))
    end_event = profile.end.get("after_event")
    out = None
    tick = 0
    while True:
        s = ac.s
        n_seq = len(ref.sequences)
        out = ref.update(t, s.lat, s.lon, s.alt_ft, s.gs_kt, s.track_true)
        for sq in ref.sequences[n_seq:]:
            leg_start_t[sq.leg_index] = t
            timeline.append({"t": round(t, 3), "what": "sequence", "from": sq.from_ident, "to": sq.to_ident})

        row = {}
        cmds = []
        if tick == 1:
            cmd_seq += 1
            cmds.append(f"{cmd_seq} ACT {act_slot}")
            timeline.append({"t": round(t, 3), "what": "pilot", "cmd": cmds[-1]})
        if ref.map_suspended and t_map is None:
            t_map = t
            timeline.append({"t": round(t, 3), "what": "map", "fix": out.wpt})
        if t_map is not None and ref.map_suspended and t >= t_map + resume_after:
            if not proc.get("missed", False):
                break
            ref.go_missed(t, (s.lat, s.lon))
            leg_start_t[ref.i] = t
            cmd_seq += 1
            cmds.append(f"{cmd_seq} RESUME")
            timeline.append({"t": round(t, 3), "what": "pilot", "cmd": cmds[-1]})
            out = ref.update(t, s.lat, s.lon, s.alt_ft, s.gs_kt, s.track_true)

        done_now = _run_events(events, ref, out, t, leg_start_t, ac, timeline)

        # sensor keys first, LAT/LONG last: the engine computes on a position write
        row["MAGVAR"] = mv
        row["GPS_FIX_TYPE"] = 3
        row["GPS_ACCURACY_HORIZ"] = gps_accuracy_ft
        row["ALT"] = round(s.alt_ft, 2)
        row["GS"] = round(s.gs_kt, 3)
        row["TAS"] = round(perf.tas_kt, 3)
        row["VS"] = round(s.vs_fpm, 1)
        row["TRACK"] = round(refnav.wrap360(s.track_true), 3)
        row["HEAD"] = round(refnav.wrap360(s.heading_true + mv), 3)
        row["PITCH"] = round(s.pitch_deg, 2)
        row["ROLL"] = round(s.roll_deg, 2)
        row["LAT"] = round(s.lat, 8)
        row["LONG"] = round(s.lon, 8)
        row["REFCRS"] = round(out.crs_mag, 3)
        row["REFXTK"] = round(out.xtk_nm, 5)
        row["REFCDI"] = round(out.cdi, 5)
        row["REFDIS"] = round(out.dis_nm, 4)
        row["REFWPT"] = out.wpt
        row["REFPT"] = out.pt
        if tick == 0:
            for key, value in route:
                row[key] = value
        for c in cmds:
            row["FPLCMD"] = c
        rows.append((round(t, 3), row))

        if end_event and end_event in done_now:
            break
        if t > max_s:
            pending = [i for i, ev in events.items() if ev["state"] != "done"]
            raise GeneratorError(f"{profile.name}: still running at {max_s:.0f} s; events not done: {pending}")

        # vertical: descend to the TO fix's constraint on the approach, climb to the top of the missed
        if ref.missed:
            target = missed_top
            vs_lim = None
        elif ref.map_suspended:
            target = s.alt_ft
            vs_lim = None
        else:
            target = min(s.alt_ft, alts.get(out.wpt, s.alt_ft))
            vs_lim = perf.descent_fpm
            if out.dis_nm > 0 and s.gs_kt > 0 and s.alt_ft > target:
                need = (s.alt_ft - target) / (out.dis_nm / s.gs_kt * 60.0)
                vs_lim = min(perf.max_descent_fpm, max(perf.descent_fpm, need))
        ac.step(dt, out.dtk_true, out.xtk_nm, target, out.turn_hint, vs_lim)
        t += dt
        tick += 1

    pending = [i for i, ev in events.items() if ev["state"] != "done"]
    if pending and end_event:
        raise GeneratorError(f"{profile.name}: run ended with events not done: {pending}")
    return Run(profile, frequency_ms, rows, timeline, [s.__dict__ for s in ref.sequences])


def ref_in_missed(legs, leg):
    seen = False
    for lg in legs:
        if lg.first_missed:
            seen = True
        if lg is leg:
            return seen
    return False


def _run_events(events, ref, out, t, leg_start_t, ac, timeline):
    done_now = set()
    for eid, ev in events.items():
        e = ev["e"]
        if ev["state"] == "pending":
            at = e.at
            fire = False
            if "leg" in at:
                frm, _, to = at["leg"].partition("-")
                i = ref.i
                prev = ref._prev_fix(i)
                if ref.legs[i].ident == to and prev is not None and prev.ident == frm and ref._hold is None:
                    fire = t - leg_start_t.get(i, t) >= float(at.get("after_s", 0.0))
            elif "after" in at:
                fire = events[at["after"]]["state"] == "done"
            elif "hold_at" in at:
                fire = ref._hold is not None and ref.legs[ref.i].ident == at["hold_at"]
            if fire:
                ev["state"], ev["t0"] = "active", t
                if "offset_nm" in e.do:
                    ac.offset_nm = float(e.do["offset_nm"])
                timeline.append({"t": round(t, 3), "what": "event", "id": eid, "do": e.do})
        if ev["state"] == "active":
            finished = False
            if "offset_nm" in e.do:
                if ev["est"] is None and abs(out.xtk_nm - ac.offset_nm) < ESTABLISHED_NM:
                    ev["est"] = t
                    timeline.append({"t": round(t, 3), "what": "established", "id": eid,
                                     "xtk_nm": round(out.xtk_nm, 4)})
                if ev["est"] is not None and t - ev["est"] >= float(e.do.get("hold_s", 0.0)):
                    finished = True
            elif "hold_min" in e.do:
                if ref._hold is None:
                    raise GeneratorError(f"event {eid}: hold_min with no hold being flown")
                finished = t - ev["t0"] >= float(e.do["hold_min"]) * 60.0
            if finished:
                ev["state"] = "done"
                done_now.add(eid)
                timeline.append({"t": round(t, 3), "what": "done", "id": eid})
    return done_now


def write_recorder_file(run, path, starttime=None):
    """The data_recorder format: header, then every row as
    ``key: [value, annunciate, old, bad, fail, secfail]``."""
    starttime = starttime or datetime.datetime(2026, 1, 1, 0, 0, 0)
    with open(path, "w") as f:
        f.write(json.dumps({"frequency": f"{run.frequency_ms}", "starttime": starttime.isoformat()},
                           separators=(",", ":")) + "\n")
        for _, row in run.rows:
            f.write(json.dumps({k: [v, 0, 0, 0, 0, 0] for k, v in row.items()}, separators=(",", ":")) + "\n")
