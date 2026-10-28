#  SPDX-License-Identifier: GPL-2.0-or-later
#
#  Route block encoder for the synthetic flight tier (SYN1).
#
"""Turns a scenario's route into the FIX route block (``FPLf*``, the RS1
segment table, ``FPLCOUNT``/``FPLSEGCNT``/``FPLNAME``/``FPLAPRTYPE``/
``FPLDBCYC``, then ``FPLSEQ`` last) exactly as an editor would publish it
(``doc/flightplan_keys.md``). It stands in for the pilot loading the
procedure on the display; nothing here reads or imports the engine.

Assembling a coded procedure into slots is not specified anywhere yet
(PA7/PA9 own it in pyEfis). The choices made here, so a reviewer can see
them:

- the transition's last fix and the final's ``IF`` (the same fix, coded
  twice) are one slot;
- a ``CA`` leg has no fix, so its slot has an empty ident and a position on
  its coded course from the previous fix, ``ca_point_nm`` along (the
  engine flies a ``CA`` along its course through that point, so any point
  on the course line gives the same leg);
- ``FPLfFLAGS``: fly-over from the description code's second character,
  IAF/FAF/MAP from its fourth, MAHP on the slots at the missed approach's
  hold fix, and ``0x20`` (from a coded procedure) on every slot;
- segment rows: the transition plus final as one approach row (kind 4,
  ``RNAV<rwy>.<transition>``), the missed approach as one row (kind 5).
"""

from fixgw.sim import refnav

FLAG_FLYOVER = 0x01
FLAG_IAF = 0x02
FLAG_FAF = 0x04
FLAG_MAP = 0x08
FLAG_MAHP = 0x10
FLAG_FROM_PROCEDURE = 0x20

TYPE_BY_KIND = {"airport": 1, "vor": 2, "waypoint": 4, "runway": 4}

SEGKIND_APPROACH = 4
SEGKIND_MISSED = 5


def _alt_field(desc, alt1, alt2):
    if not alt1:
        return ""
    a1 = str(int(alt1))
    if desc == "+":
        return "+" + a1
    if desc == "-":
        return "-" + a1
    if desc == "B" and alt2:
        return f"B{int(alt2)},{a1}"
    return "@" + a1


def procedure_slots(cifp, airport, approach, transition, magvar_deg, ca_point_nm=2.0):
    """(slots, segments) for ``approach`` via ``transition``. Each slot is a
    dict of the 14 ``FPLf*`` fields; each segment a dict of the six
    ``FPLS<k>*`` fields."""
    raw = cifp.approach_legs(airport, approach)
    trans = [r for r in raw if r.route_type == "A" and r.transition == transition]
    final = [r for r in raw if r.route_type != "A"]
    slots = []
    kinds = []
    in_missed = False
    hold_fix = None
    for r in final:
        if r.path_term == "HM":
            hold_fix = r.fix
    for part, records in (("A", trans), ("F", final)):
        for r in records:
            if r.path_term == "IF" and slots and slots[-1]["ID"] == r.fix:
                continue
            if r.first_missed:
                in_missed = True
            slot = {"ID": r.fix, "LAT": 0.0, "LON": 0.0, "TYPE": 4, "PT": r.path_term,
                    "CRS": 0.0, "DST": 0.0, "ALT": _alt_field(r.alt_desc, r.alt1, r.alt2),
                    "SPD": 0, "SGI": 0, "FLAGS": FLAG_FROM_PROCEDURE,
                    "CTRLAT": 0.0, "CTRLON": 0.0, "TURN": r.turn}
            if r.fix:
                f = cifp.fix(r.fix, airport=airport if r.fix_section[0] == "P" else None,
                             section=r.fix_section if r.fix_section.strip() else None)
                slot["LAT"], slot["LON"], slot["TYPE"] = f.lat, f.lon, TYPE_BY_KIND[f.kind]
            if r.course_mag is not None:
                slot["CRS"] = r.course_mag
            if r.path_term == "CA":
                prev = next(s for s in reversed(slots) if s["ID"])
                true_course = refnav.wrap360(r.course_mag - magvar_deg)
                slot["LAT"], slot["LON"] = refnav.direct(prev["LAT"], prev["LON"], true_course, ca_point_nm)
            if r.path_term == "HM":
                if r.dist_or_time.startswith("T"):
                    raise ValueError("time-coded hold: leg length needs a speed, not encoded yet")
                slot["DST"] = int(r.dist_or_time) / 10.0
            if r.flyover:
                slot["FLAGS"] |= FLAG_FLYOVER
            if r.is_iaf and part == "A":
                slot["FLAGS"] |= FLAG_IAF
            if r.is_faf:
                slot["FLAGS"] |= FLAG_FAF
            if r.is_map:
                slot["FLAGS"] |= FLAG_MAP
            if in_missed and r.fix and r.fix == hold_fix:
                slot["FLAGS"] |= FLAG_MAHP
            slots.append(slot)
            kinds.append(SEGKIND_MISSED if in_missed else SEGKIND_APPROACH)
    segments = []
    for kind in (SEGKIND_APPROACH, SEGKIND_MISSED):
        idx = [i for i, k in enumerate(kinds, start=1) if k == kind]
        if not idx:
            continue
        first, last = idx[0], idx[-1]
        rwy = approach[1:] if approach[0] == "R" else approach
        ident = f"RNAV{rwy}.{transition}" if kind == SEGKIND_APPROACH else f"RNAV{rwy}"
        segments.append({"KIND": kind, "ID": ident, "ENT": slots[first - 1]["ID"],
                         "EXT": slots[last - 1]["ID"], "FIRST": first, "LAST": last})
        for i in idx:
            slots[i - 1]["SGI"] = len(segments)
    return slots, segments


def route_block_writes(slots, segments, name, aprtype, cycle, seq):
    """The ordered (key, value) writes an editor makes: every slot and
    segment row, then the counts, the name and provenance, ``FPLSEQ``
    last (the commit)."""
    writes = []
    for i, slot in enumerate(slots, start=1):
        for field in ("ID", "LAT", "LON", "TYPE", "PT", "CRS", "DST", "ALT", "SPD", "SGI",
                      "FLAGS", "CTRLAT", "CTRLON", "TURN"):
            writes.append((f"FPL{i}{field}", slot[field]))
    for k, seg in enumerate(segments, start=1):
        for field in ("KIND", "ID", "ENT", "EXT", "FIRST", "LAST"):
            writes.append((f"FPLS{k}{field}", seg[field]))
    writes += [("FPLSEGCNT", len(segments)), ("FPLCOUNT", len(slots)), ("FPLNAME", name),
               ("FPLAPRTYPE", aprtype), ("FPLDBCYC", cycle), ("FPLSEQ", seq)]
    return writes
