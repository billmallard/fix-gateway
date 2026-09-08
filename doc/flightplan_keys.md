# Flight plan FIX keys (FP1)

Spec: [fix-gateway#22](https://github.com/billmallard/fix-gateway/issues/22);
`makerplane/briefs/flight_plan_plan.md` section 3.2 + Appendix A/C (the
maos-workspace repo). This item lands the key contract only -- the
`flightplan` engine plugin that computes the guidance outputs is FP2.

One key, one writer, with a single documented exception: after a gateway
restart the engine (FP2) republishes the route block and bumps `FPLSEQ`
itself, mirroring `state_persist` restoring `NAVSRC`.

## Route block

The route crosses the bus as an indexed key block (`f: 50` in
`database/variables.yaml`), the idiom the database already expands (`EGTec`,
`BTNb`). The editor (pyEfis) writes every slot, then `FPLCOUNT`, then bumps
`FPLSEQ` last. Consumers act only on `FPLSEQ` change -- netfix processes one
connection's writes in order, so the block is complete when the counter
lands. `tol: 0` (no staleness auto-flag; these are edited, not streamed).

| Key | Type | Range | Writer | Meaning |
|---|---|---|---|---|
| `FPL1..50 ID` | str | <=6 chars, uppercase, `""` = empty | editor | Slot ident |
| `FPL1..50 LAT` / `LON` | float | +-90 / +-180 deg | editor | Slot position |
| `FPL1..50 TYPE` | int | 0..6 (0 unknown, 1 airport, 2 VOR, 3 NDB, 4 fix, 5 user, 6 map point) | editor | Slot type |
| `FPL1..50 ROLE` | int | 0..4 (0 none, 1 IAF, 2 FAF, 3 MAP, 4 MAHP) | editor | Pilot-marked approach role; drives approach scaling |
| `FPLCOUNT` | int | 0..50 | editor | Slots in use |
| `FPLNAME` | str | <=32 chars, no `;` | editor | Route name |
| `FPLSEQ` | int | >=0 | editor (engine on restore) | Commit counter, written last |

## Direct-To staging

| Key | Type | Writer | Meaning |
|---|---|---|---|
| `DTOID` | str | editor | Staged target ident |
| `DTOLAT` / `DTOLON` | float | editor | Staged target position |
| `DTOTYPE` | int | editor | Staged target type (same 0..6 as `FPLfTYPE`) |

## Command channel

`FPLCMD` grammar: `"<seq> VERB [arg]"` (Appendix C). `seq` is a positive,
strictly-increasing integer per editor session -- a repeated identical
command is still a new event, and it gives the editor an ack to wait on.
Verbs: `ACT <slot>`, `DTO` (no arg: use the staged `DTO*` keys; with a slot
arg, direct-to that plan waypoint), `DTOX`, `SUSP`, `RESUME`,
`SCALE <0.3|1.0|2.0|AUTO>`.

| Key | Type | Writer | Meaning |
|---|---|---|---|
| `FPLCMD` | str | editor | The command string |
| `FPLCMDACK` | int | engine | `= seq` on success, `-seq` on rejection |
| `FPLMSG` | str | engine | Last message / rejection reason (`NO PLAN`, `BAD SLOT`, `NO POSITION`, `PARSE`, or a sequence trace like `SEQ GVO -> RZS`) |

## Engine outputs (FP2 writes; `tol: 5000`)

| Key | Type | Range | Meaning |
|---|---|---|---|
| `FPLSTATE` | int | 0 NONE, 1 LEG, 2 DIRECT, 3 SUSP | Engine state |
| `FPLACTLEG` | int | 0..50 | Slot of the TO waypoint, 0 = none |
| `FPLPHASE` | str | `ENR` / `TERM` / `LNAV` / `LOI` / `0.30 NM` / `1.00 NM` / `""` | Flight-phase annunciation |
| `FPLAPR` | int | 0 none, 1 armed, 2 active, 3 missed | Approach state |
| `FPLINTEG` | bool | | Integrity gate satisfied for the current phase |
| `CDISCALE` | float | 0.3..2.0 nm | Full-scale deflection (ramps 1.0 -> 0.3 nm over the 2 nm before the FAF) |
| `FPLCRS` | float | 0..359.9 deg magnetic | Desired track (DTK) |
| `FPLXTK` | float | +-100 nm, positive = right of track | Cross-track error, same sign convention as `XTRACK` |
| `FPLCDI` | float | -1..+1 | `-FPLXTK / CDISCALE`, clamped -- the sign convention `garmin_gnx375/__init__.py:167-169` uses, so the HSI needle behaves identically for the internal plan and an external navigator |
| `FPLTF` | int | 0/1/2 | TO/FROM |
| `FPLFRLAT` / `FPLFRLON` | float | deg | FROM point of the active leg (previous waypoint, or the direct-to activation point) |
| `WPFROM` / `WPNEXT` | str | <=6 chars | FROM and NEXT waypoint idents |
| `WPDIS` | float | nm | Distance to the TO waypoint |
| `WPETE` | int | s, `bad` when GS < 30 kt | Time to the TO waypoint |
| `FPLREMDIS` / `FPLREMETE` | float / int | nm / s | Remaining distance/time to the end of the plan |
| `FPLALERT` | bool | | Waypoint sequencing alert (10 s or 1 nm before sequencing, whichever is longer) |

`WPLAT`/`WPLON`/`WPNAME` (`database/mavlink.yaml`) are pre-existing keys; the
engine becomes their writer when the `flightplan` plugin is enabled (FP2).
`ifly`/`navigator_adapter`/`mavlink` must stay disabled alongside it -- one
key, one writer. This item only fixes `WPLON`'s range: it was clamped to
+-90 (a latent bug for longitudes past 90 deg), now +-180, matching
longitude's actual domain.

## GPS guidance source selection

`GPSSRC` (persisted by `state_persist`, the `NAVSRC`/`BRGnSRC` precedent)
lets the internal flight plan and an external navigator be interchangeable
sources for the canonical `GPSCRS`/`GPSCDI`/`GPSTF` that `NAVSRC` in turn
selects into `COURSE`/`CDI`/`TOFROM` for the HSI:

| Key | Type | Range | Writer | Meaning |
|---|---|---|---|---|
| `GPSSRC` | int | 0 = internal plan, 1 = external navigator | pilot (persisted) | GPS guidance source |
| `EXTCRS` | float | 0..359.9 deg magnetic | external navigator plugin | External navigator course |
| `EXTCDI` | float | -1..+1 | external navigator plugin | External navigator course deviation |
| `EXTTF` | int | 0/1/2 | external navigator plugin | External navigator TO/FROM |
| `GPSCRS` / `GPSCDI` / `GPSTF` | (existing) | | compute `select` on `GPSSRC` | `{FPLCRS,EXTCRS}`, `{FPLCDI,EXTCDI}`, `{FPLTF,EXTTF}` |

On the bench, `xplane.yaml`'s nav RREF rows now write `EXTCRS`/`EXTCDI`
(previously `GPSCRS`/`GPSCDI` directly) so X-Plane's FMS is the external
navigator. `garmin_gnx375` and `navigator_adapter` are **not** touched by
this item -- they write `COURSE`/`CDI` directly today (a pre-select legacy
path predating `NAVSRC`, see the code audit in the flight-plan brief section
1.2), not `GPSCRS`/`GPSCDI`, so there is no collision with the new selects.
Migrating them onto the `EXTCRS`/`EXTCDI`/`GPSSRC` path is a noted follow-on,
not in this epic.

The `xte` compute (`connections/compute.yaml`) is re-fed `GPSCRS` instead of
`COURSE`, so `XTRACK`/`GPSBRG` track the active plan/external navigator
correctly even while `NAVSRC` has the HSI showing a VOR.

## Integrity key for FP2

`FPLINTEG`'s gate (brief section 3.3) reads `GPS_FIX_TYPE` (below a 3-D fix
-> guidance `fail`) and a horizontal-accuracy/protection key (above the
phase's horizontal alert limit -> `FPLINTEG = false`). The default
`integrity_key` for FP2's `connections/flightplan.yaml` is
**`GPS_ACCURACY_HORIZ`** (`database/ahrs.yaml`, units **feet** -- FP2 must
convert to nm to compare against the nm-denominated HAL table
`{enr: 2.0, term: 1.0, lnav: 0.3}`).

Plugins confirmed to write it: `mavlink` (`Mav.py`, mm -> ft) and `gpsd`
(`__init__.py`, m -> ft). **Not written by `xplane`** -- the bench's X-Plane
FMS publishes neither `GPS_ACCURACY_HORIZ` nor `GPS_FIX_TYPE`, so on the
bench `FPLINTEG` stays `true` and `FPLMSG` reads `NO INTEGRITY DATA` once per
plan activation, per the brief's "no-accuracy source" rule -- this is
expected bench behaviour, not a defect.

**Caveat for FP2, not fixed by this item:** `garmin_gnx375` writes
`GPS_FIX_TYPE` from its own NMEA GGA `qual` field on a 0/1/2 scale (0=none,
1=GPS, 2=DGPS/WAAS -- see the plugin's own header comment), not the
`GPS_FIX_TYPE` key's documented 0..5 scale (0-1 no fix, 2 2-D, 3 3-D, 4 3-D
+DGPS, 5 3-D+RTK). A gate written as "`GPS_FIX_TYPE` below 3" would never
pass against a real GNX on this plugin as it stands today; FP2 should account
for this (either a remap in the garmin_gnx375 connection, or a gate written
against what the plugin actually emits) rather than assume the two scales
agree.

## Roadmap design record

A "Flight plan" block for `MAOS-DESIGN/docs/AVIONICS_STACK_ROADMAP.md`
section 9.1 is included in this PR's description per the issue's own
allowance ("PR it to the MAOS workspace repo, or list the block text in the
PR for Bill") -- fix-gateway's write boundary does not include the
maos-workspace ops repo.
