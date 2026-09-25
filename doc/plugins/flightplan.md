# Flight Plan Navigation Engine (`fixgw.plugins.flightplan`)

FP2 (fix-gateway#23; `makerplane/briefs/flight_plan_plan.md` section 3.3 in
the maos-workspace repo). This is the *engine* half of the flight-plan
epic — a compute-only plugin that turns the route block FP1 defined
(`doc/flightplan_keys.md`) into leg guidance, sequencing, Direct-To, CDI
scaling and the full DO-229 lateral flight-phase behaviour, including the
approach. The editor (pyEfis) writes the route and commands; this plugin
never needs a nav database, only coordinates.

**PA3** (fix-gateway#27; `makerplane/briefs/procedures_and_airways_plan.md`
section 3.2) widened the route slot from a point to a leg (path terminator,
course, distance, altitude/speed, procedure segment, flags) and raised the
block to 100 slots, so a procedure can round-trip through it — see
`doc/flightplan_keys.md` for the key contract.

**PA4** (fix-gateway#28; section 2/9 of the same brief) is the first item
that actually flies something other than an implicit `TF` leg: Tier-1 path
terminators (`IF`/`TF`/`CF`/`DF` — 70% of approach legs, 91% of SID/STAR
legs, all great-circle or course-to-a-point geometry this engine already
had), vector legs (`VA`/`VM`/`FM`/`VI` — a SUSP annunciated `VECTORS`,
guardrail 4), and the guardrail-1 safety gate: loading a route with any
other path terminator rejects the **whole** route, with a reason on
`FPLMSG`, leaving whatever was previously loaded untouched.

**PA13** (fix-gateway#29; section 2/6/9 of the same brief) flies the Tier-2
leg types the brief phased out of PA4: `RF`/`AF` constant-radius arcs (real
circular geometry — `engine.Engine._arc_geometry`, not a straight-line
approximation), `CA`/`FA` altitude-terminated legs (course held until a
live altitude input crosses the coded constraint, never a distance guess),
and `HM`/`HF`/`HA` holds plus `PI` procedure turns (flown as an
outbound/inbound pair of straight legs around the fix — the inbound leg
reuses `CF`'s course-anchor trick). `VA` stays a vector leg, unchanged —
see engine.py's module docstring for why moving it to Tier 2 would soften
guardrail 4 rather than shrink the rejected set. Guardrail 1 extends to a
*recognized* Tier-2 type with incomplete data (no turn direction, no arc
center, no altitude constraint): that rejects the whole route exactly like
an unrecognized terminator does, never a defaulted value.

**Ruling (Bill, 2026-09-08):** CDI scaling is the full DO-229 lateral
behaviour *including the approach phase*. There is no VFR-advisory mode —
that framing was rescinded before this was built.

## Where the logic lives

- `fixgw/geo.py` — shared great-circle geodesy (bearing, distance,
  cross-track, along-track, destination point). `fixgw.plugins.compute`'s
  `xte`/`bearing` functions and this engine both import it; no duplicated
  trig.
- `fixgw/plugins/flightplan/engine.py` — the `Engine` class: pure Python,
  no `fixgw.database` dependency. All the navigation logic (legs,
  sequencing, Direct-To, SUSP/RESUME, CDI scaling/phase, the integrity
  gate) lives here and is unit tested directly (`tests/plugins/flightplan/
  test_engine.py`).
- `fixgw/plugins/flightplan/__init__.py` — the `Plugin`/`MainThread`
  wiring: FIX database callbacks in, key writes out, disk persistence.
  Precedented by `plugins/compute.py` (callbacks), `plugins/state_persist`
  (atomic JSON, restore-after-delay) and `plugins/skel.py` (lifecycle).

## Configuration

`config/connections/flightplan.yaml`:

```yaml
flightplan:
  load: FLIGHTPLAN          # preferences.yaml enabled: FLIGHTPLAN: true
  module: fixgw.plugins.flightplan
  # state_file: "{CONFIG}/flightplan_state.json"   # optional; default shown
  restore_delay: 2.0        # seconds before restoring the persisted plan
  rate_hz: 5                # LAT/LONG-driven guidance update rate cap
  turn_rate_deg_s: 3.0      # standard-rate turn, for turn-anticipation d_ta
  terminal_nm: 30           # ENR/TERM threshold, distance from dep/dest
  approach_ramp_nm: 2.0     # LNAV ramp starts this far before the FAF
  alert_s: 10               # FPLALERT lead time (or 1 nm, whichever is longer)
  integrity_key: GPS_ACCURACY_HORIZ
  altitude_key: ALT         # PA13: CA/FA/HA termination input; "" disables Tier-2 altitude termination
  hal_nm:
    enr: 2.0
    term: 1.0
    lnav: 0.3
```

Enabled by default (`FLIGHTPLAN: true` in `preferences.yaml`). Keys are
listed in full, with types/ranges/writers, in `doc/flightplan_keys.md`
(FP1) — this document covers behaviour, not the key contract.

## Behaviour summary

- **Legs**: great-circle on a sphere, R = 3440.065 nm. DTK (`FPLCRS`) is
  the bearing from the aircraft's along-track projection on the leg to the
  TO waypoint (not the constant leg bearing), true, then `wrap360(+
  MAGVAR)`. Cross-track (`FPLXTK`, positive = right) and the CDI
  (`FPLCDI = clamp(-FPLXTK / CDISCALE, -1, 1)`) use the same sign
  convention as `garmin_gnx375`.
- **Path terminators (PA4/PA13)**: `IF`/`TF`/`DF` fly the prior slot to this
  one — the same great circle the engine has always flown. `CF` and (PA13)
  `CA` fly the leg's published `FPLfCRS` (magnetic, converted with the
  current `MAGVAR`) instead of a bearing derived from the previous slot: a
  synthetic FROM point 50 nm behind the fix, on the reciprocal of that
  course, feeds the same cross-track/along-track math unchanged — `CA` has
  no real endpoint fix, so it never sequences on distance, only once a live
  altitude input crosses `FPLfALT`'s constraint. A route containing any
  other path terminator is **rejected in full** at load —
  `engine.PT_SUPPORTED` is the complete list — with
  `FPLMSG = "UNSUPP <PT> <ident>"`, or (PA13) `"BADTURN"`/`"BADRADIUS"`/
  `"BADCENTER"`/`"BADALT"`/`"BADLEGLEN" <PT> <ident>` when a *recognized*
  Tier-2 type is missing the fields its geometry needs; either way the
  previously loaded route, if any, is left exactly as it was (guardrail 1).
  A vector leg (`VA`/`VM`/`FM`/`VI`) is accepted at load but never flown:
  reaching one (by `ACT`/`DTO` or mid-flight sequencing) forces `FPLSTATE =
  SUSP` and `FPLPHASE = "VECTORS"`, with `FPLCRS`/`FPLXTK`/`FPLCDI`/`WPDIS`/
  `WPETE`/`FPLREMDIS`/`FPLREMETE` all reading 0 — the box invents no
  heading, and no distance to one either (guardrail 4). `RESUME` off a
  vector leg activates the next slot direct from the aircraft's actual
  position (like a `DTO`, not a resumed track), or posts `"NO NEXT LEG"`
  and stays suspended if there is nothing after it.
- **Arcs (PA13)**: `RF`/`AF` fly a real constant-radius circle around
  `FPLfCTRLAT`/`FPLfCTRLON` at radius `FPLfDST`, direction `FPLfTURN` — not
  a straight-line approximation. `FPLXTK` is distance-from-center minus
  radius (sign flipped by turn direction), `FPLCRS` is the tangent bearing
  at the aircraft's own radial, and `WPDIS`/sequencing are driven by the
  sweep angle (`geo.arc_sweep_deg`) to the exit fix's radial in the coded
  direction, converted to arc length by the radius — the same role
  along-track distance plays for a straight leg.
- **Holds and procedure turns (PA13)**: `HM`/`HF`/`HA` and `PI` fly to the
  fix like any other leg, then an outbound leg (`FPLfDST` nm on the
  reciprocal of `FPLfCRS`, or reciprocal ±45° for `PI`) followed by an
  inbound leg back into the fix (the same course-anchor trick `CF`/`CA`
  use). `FPLPHASE` shows `HOLD` (`HM`/`HF`/`HA`) or `PTURN` (`PI`) for as
  long as the aircraft is in the pattern — live guidance throughout, unlike
  `VECTORS`. `HF` and `PI` exit automatically after one circuit/turn; `HA`
  exits once a live altitude input crosses `FPLfALT`'s constraint (checked
  each time the inbound leg reaches the fix, never mid-circuit); `HM` repeats
  indefinitely until `RESUME` posts `"HOLD EXIT ARMED"`, then exits at the
  next fix passage. A hold/PT coded as the very last slot, with nothing to
  sequence onto, keeps flying the pattern rather than freezing on arrival —
  the same "no next leg" backstop a vector leg has.
- **Altitude-terminated legs (PA13)**: `CA` (no fix, course held from
  activation) and `FA` (flies to its fix like `DF`, then continues on
  `FPLfCRS` past it) both end only when a live altitude input crosses
  `FPLfALT`'s constraint — never on distance, and never at all with no
  altitude data (guardrail 1's "never invent" extended to altitude). The
  new FROM point is the aircraft's own position at that moment (no fix to
  hand off to), so the sequencing message reads `"SEQ ALT -> <next ident>"`.
- **Sequencing**: fly-by turn anticipation, `d_ta = R_turn * tan(delta/2)`,
  `R_turn = GS_kt / 188.5` nm, `delta` capped at 120 deg, `d_ta` floored at
  0.1 nm and capped at 5 nm — or the abeam backstop (along-track distance
  goes negative). The last waypoint, and any waypoint marked MAP, never
  auto-sequences. The FAF and every fix between the FAF and the MAP are
  fly-over: abeam-only, no turn anticipation (`d_ta = 0`) — and (PA13) so is
  every Tier-2 leg type, in both directions: an arc's exit tangent, a
  hold/PT's outbound/inbound pair and a `CA`/`FA`'s open-ended course are
  not the simple bearing-delta case the anticipation formula was built for,
  so those transitions are abeam-triggered only, never anticipated early.
- **Commands** (`FPLCMD`): `ACT <k>`, `DTO` / `DTO <k>`, `DTOX`, `SUSP`,
  `RESUME`, `SCALE <0.3|1.0|2.0|AUTO>`. See Appendix C of the brief for the
  full grammar. Ack on `FPLCMDACK` (`seq` success, `-seq` rejection),
  reason on `FPLMSG` (`NO PLAN`, `BAD SLOT`, `NO POSITION`, `PARSE`, or —
  off a vector leg, PA4 — `"RESUME NAV -> <ident>"` / `"NO NEXT LEG"`, or —
  on a flying `HM` hold, PA13 — `"HOLD EXIT ARMED"`).
  Unknown verbs are rejected, never ignored.
- **CDI scaling / flight phase (DO-229, full)**:
  - `ENR` (2.0 nm) beyond 30 nm of both the departure and destination
    (only counted when that slot is typed `airport`); `TERM` (1.0 nm)
    within 30 nm of either.
  - An approach is *marked* when the plan carries a FAF and a later MAP.
    It *arms* (`FPLAPR=1`) within 30 nm of the MAP with the active leg at
    or before the FAF, and *activates* (`FPLAPR=2`, `FPLPHASE=LNAV`) from
    2 nm before the FAF: full-scale deflection ramps **linearly** from
    1.0 nm to 0.3 nm over that 2 nm (0.65 nm at 1 nm out), then holds
    0.3 nm from the FAF to the MAP.
  - At the MAP: no auto-sequencing. TO flips to FROM, the engine
    auto-suspends (`FPLSTATE=3`), `CDISCALE` stays 0.3 nm, guidance
    continues along the extended final-approach course. Missed approach
    is **pilot action**: `RESUME` sets `FPLAPR=3`, returns `CDISCALE` to
    TERM (1.0 nm), and sequences to the next slot (the MAHP, if marked) —
    or, with nothing after the MAP, leaves guidance FROM the MAP and posts
    `FPLMSG = "NO MISSED APPROACH LEGS"`.
  - An off-route direct-to, or `ACT` to a slot before the FAF, drops the
    approach back to armed/TERM. A direct-to between the FAF and the MAP
    does **not** activate approach scaling (guide 3-47).
  - Manual `SCALE` is a ceiling: `CDISCALE = min(phase scale, manual)`;
    `AUTO` clears it. `FPLPHASE` shows `"0.30 NM"` / `"1.00 NM"` while the
    manual value is the binding one — except during `LOI`, which always
    shows regardless of a manual selection.
  - **Integrity gate**: `GPS_FIX_TYPE` below a 3-D fix fails all guidance
    outputs outright. The configured `integrity_key` (default
    `GPS_ACCURACY_HORIZ`, feet — converted to nm) above the phase's HAL
    (`hal_nm`) sets `FPLINTEG=false`, flags the guidance outputs `bad`,
    `FPLPHASE=LOI`, and reverts an active approach to armed/TERM. A source
    that never actively publishes either key (detected via the FIX
    quality `old` flag — the item has gone stale past its `tol` with no
    write) is treated as "no data": `FPLINTEG` stays `true` and
    `FPLMSG="NO INTEGRITY DATA"` posts once per plan activation. This is
    the expected, documented behaviour on the bench, where X-Plane's FMS
    publishes neither key (`doc/flightplan_keys.md` "Integrity key for
    FP2"). Because a freshly-initialized key hasn't gone `old` yet until
    its `tol` elapses (2 s for `GPS_FIX_TYPE`/`GPS_ACCURACY_HORIZ`), a
    real gateway boot can show a few seconds of `fail`/`bad` guidance
    before a source starts publishing or the key goes stale — the same
    settling window `state_persist`'s `restore_delay` already assumes
    elsewhere in the stack.
- **Cycle**: on `LAT`/`LONG` change, rate-limited to `rate_hz` (default
  5 Hz); a 1 Hz timer handles staleness. Position old/bad/fail for more
  than 5 s fails all guidance outputs. No dead reckoning.
- **Persistence**: `{CONFIG}/flightplan_state.json`, atomic (temp +
  `os.replace`), holding the route, `FPLSEQ`, name, activation state
  (mode, active leg, FROM/TO points), the missed-approach/DTO-suppression
  flags, the manual scale selection, and (PA13) the Tier-2 sub-leg phase
  (which leg of an outbound/inbound pair, or the FA past-the-fix course
  phase) and whether an `HM` hold's exit is armed. Restored `restore_delay` (2 s)
  after start, at which point the engine re-publishes the route block and
  `FPLSEQ` itself — the one documented exception to "the editor writes the
  block" (mirrors `state_persist` restoring `NAVSRC`). The plugin's own
  `FPLSEQ` callback is suppressed during that republish so it doesn't
  immediately reset the very activation state it just restored.

## Driving it by hand

With the gateway running and `netfix` enabled (default port 3490):

```python
from fixgw.netfix import Client

c = Client("127.0.0.1", 3490)
c.connect()

# Write a 3-slot route block (KSBA -> GVO -> KSMX), then commit it.
c.write("FPL1ID", "KSBA"); c.write("FPL1LAT", 34.42621); c.write("FPL1LON", -119.84037); c.write("FPL1TYPE", 1)
c.write("FPL2ID", "GVO");  c.write("FPL2LAT", 34.53142); c.write("FPL2LON", -120.09106); c.write("FPL2TYPE", 2)
c.write("FPL3ID", "KSMX"); c.write("FPL3LAT", 34.89892); c.write("FPL3LON", -120.45758); c.write("FPL3TYPE", 1)
c.write("FPLCOUNT", 3)
c.write("FPLNAME", "KSBA-KSMX")
c.write("FPLSEQ", 1)          # commit -- consumers (incl. this engine) act now

c.write("FPLCMD", "1 ACT 2")  # direct to GVO's leg (FROM=KSBA, TO=GVO)

print(c.read("FPLCMDACK"))    # 1 on success
print(c.read("FPLCRS"), c.read("FPLCDI"), c.read("WPDIS"))
```

As X-Plane (or the bench GPS source) moves `LAT`/`LONG`/`GS`, watch
`FPLCRS`/`FPLXTK`/`FPLCDI`/`WPDIS`/`FPLTF` track the leg, and `FPLSTATE`/
`FPLACTLEG`/`FPLMSG` advance as it sequences.

## Tests

- `tests/test_geo.py` — the five reference geodesy fixtures (bearing/
  distance to 1e-3 nm / 0.01 deg), cross-track/along-track sign and
  magnitude, and (PA13) `arc_sweep_deg` across ordinary, wraparound and
  coincident-radial cases in both rotational directions.
- `tests/plugins/flightplan/test_engine.py` — pure `Engine` unit tests:
  turn-anticipation table, sequencing (fly-by + fly-over + abeam
  backstop), Direct-To/DTOX/SUSP/RESUME, the full DO-229 approach state
  machine (armed/active/ramp/MAP/missed), the integrity gate, WPETE
  staleness, command ack/reject, persistence round-trip (including the PA3
  leg fields and procedure provenance), the 100-slot cycle-time budget, and
  (PA4) whole-route rejection of an unsupported path terminator — the most
  important test in that item, run negative — every supported terminator
  loading without rejection, `CF`'s published-course geometry (including
  the magnetic/true conversion), and vector-leg SUSP/VECTORS/RESUME
  (activation, mid-flight sequencing onto one, resuming off one with and
  without a next leg, and the position-required rejection). PA13 adds:
  every Tier-2 type's happy-path load, rejection of a *recognized* type
  with each required field missing (`BADTURN`/`BADRADIUS`/`BADCENTER`/
  `BADALT`/`BADLEGLEN`), an arc's XTK sign against both turn directions and
  inside/outside displacement plus its exit-radial sequencing, `CA`
  holding course through a no-altitude-data cycle and terminating only on
  altitude, `FA`'s two-phase fix-arrival-then-course behaviour, the full
  `HM` outbound/inbound/repeat/RESUME-exit state machine, `HF`'s
  single-circuit auto-exit, `HA`'s repeat-until-altitude, `PI`'s ±45°
  outbound offset and single-turn auto-exit, and persistence round-trip of
  the Tier-2 sub-leg phase and hold-exit flag.
- `tests/plugins/flightplan/test_plugin.py` — the `fixgw.database` wiring:
  route commit on `FPLSEQ`, the command channel end-to-end, guidance
  writes, the 5 Hz rate limiter, persistence restore/republish, lifecycle
  (start/stop, `PluginFail` on a stuck thread, `get_status`), and (PA4) an
  unsupported-leg-type route rejection writing `FPLMSG` and leaving the
  previous route loaded, and a vector leg annunciating `VECTORS` end to end.
  PA13 adds: the arc center/turn fields round-tripping through the real
  wire, and a `CA` leg terminating on the real `ALT` key end to end.
- `tests/plugins/compute/` — unchanged behaviour after the `geo.py`
  extraction (no numeric change to `xte`/`bearing`).
