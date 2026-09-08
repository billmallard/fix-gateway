# Flight Plan Navigation Engine (`fixgw.plugins.flightplan`)

FP2 (fix-gateway#23; `makerplane/briefs/flight_plan_plan.md` section 3.3 in
the maos-workspace repo). This is the *engine* half of the flight-plan
epic — a compute-only plugin that turns the route block FP1 defined
(`doc/flightplan_keys.md`) into leg guidance, sequencing, Direct-To, CDI
scaling and the full DO-229 lateral flight-phase behaviour, including the
approach. The editor (pyEfis) writes the route and commands; this plugin
never needs a nav database, only coordinates.

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
- **Sequencing**: fly-by turn anticipation, `d_ta = R_turn * tan(delta/2)`,
  `R_turn = GS_kt / 188.5` nm, `delta` capped at 120 deg, `d_ta` floored at
  0.1 nm and capped at 5 nm — or the abeam backstop (along-track distance
  goes negative). The last waypoint, and any waypoint marked MAP, never
  auto-sequences. The FAF and every fix between the FAF and the MAP are
  fly-over: abeam-only, no turn anticipation (`d_ta = 0`).
- **Commands** (`FPLCMD`): `ACT <k>`, `DTO` / `DTO <k>`, `DTOX`, `SUSP`,
  `RESUME`, `SCALE <0.3|1.0|2.0|AUTO>`. See Appendix C of the brief for the
  full grammar. Ack on `FPLCMDACK` (`seq` success, `-seq` rejection),
  reason on `FPLMSG` (`NO PLAN`, `BAD SLOT`, `NO POSITION`, `PARSE`).
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
  flags, and the manual scale selection. Restored `restore_delay` (2 s)
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
  distance to 1e-3 nm / 0.01 deg) and cross-track/along-track sign and
  magnitude.
- `tests/plugins/flightplan/test_engine.py` — pure `Engine` unit tests:
  turn-anticipation table, sequencing (fly-by + fly-over + abeam
  backstop), Direct-To/DTOX/SUSP/RESUME, the full DO-229 approach state
  machine (armed/active/ramp/MAP/missed), the integrity gate, WPETE
  staleness, command ack/reject, persistence round-trip, and the 50-slot
  cycle-time budget.
- `tests/plugins/flightplan/test_plugin.py` — the `fixgw.database` wiring:
  route commit on `FPLSEQ`, the command channel end-to-end, guidance
  writes, the 5 Hz rate limiter, persistence restore/republish, and
  lifecycle (start/stop, `PluginFail` on a stuck thread, `get_status`).
- `tests/plugins/compute/` — unchanged behaviour after the `geo.py`
  extraction (no numeric change to `xte`/`bearing`).
