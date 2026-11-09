# Flight Plan Engine

The `flightplan` plugin is FIX-Gateway's GPS navigator. It reads a route from
the database, works out where the aircraft is against it, and writes the
guidance a display or autopilot flies: desired track, cross-track, CDI
deflection and scale, TO/FROM, the active waypoint, distance and time to go. It
reads keys and writes keys like any other plugin, and talks to no other plugin
directly.

It is on in the shipped config (`FLIGHTPLAN: true` in `preferences.yaml`).
Key-by-key reference:
[`doc/flightplan_keys.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/flightplan_keys.md).
Plugin reference:
[`doc/plugins/flightplan.md`](https://github.com/billmallard/fix-gateway/blob/dev/doc/plugins/flightplan.md).

```
 route editor ──FPL1..100, FPLCOUNT, FPLSEQ──▶ ┌────────────┐ ──FPLCRS, FPLXTK, FPLCDI,──▶ compute select
 (pyEfis)     ──FPLCMD, DTO*─────────────────▶ │ flightplan │    FPLTF, CDISCALE, WP*      (GPSSRC = 0)
 GPS source   ──LAT, LONG, GS, MAGVAR────────▶ └────────────┘ ──FPLMSG, FPLCMDACK───────▶ the editor
```

## Settings

`connections/flightplan.yaml`. The shipped values are the code defaults.

| Option | Default | Meaning |
|---|---|---|
| `state_file` | `flightplan_state.json` in the config directory | where the active plan is saved; `{CONFIG}` expands to the config directory |
| `restore_delay` | `2.0` s | delay before the saved plan is restored at start |
| `rate_hz` | `5` | cap on position-driven guidance updates; `0` or less means no cap |
| `turn_bank_deg` | `15.0` | bank angle that sizes fly-by turns |
| `turn_rate_deg_s` | `3.0` | turn rate for hold turns (capped at 25 deg of bank) |
| `terminal_nm` | `30` | ENR/TERM boundary: distance from the departure or destination airport, and the approach arming distance to the MAP |
| `approach_ramp_nm` | `2.0` | the CDI scale ramps from 1.0 to 0.3 nm over this distance before the FAF |
| `alert_s` | `10` | waypoint alert lead time (or 1 nm, whichever is longer) |
| `integrity_key` | `GPS_ACCURACY_HORIZ` | horizontal accuracy key, in feet, checked against the HAL; `""` turns the check off |
| `hal_nm` | `{enr: 2.0, term: 1.0, lnav: 0.3}` | horizontal alert limit per phase |
| `altitude_key` | `ALT` | altitude, in feet, that ends CA, FA and HA legs; `""` means those legs never end |

## The route

A route is up to 100 slots. Each slot `FPL<n>` (n = 1..100) is a set of keys,
not one string:

| Key | Type | Meaning |
|---|---|---|
| `FPL<n>ID` | str | waypoint ident |
| `FPL<n>LAT`, `FPL<n>LON` | float | position, degrees |
| `FPL<n>TYPE` | int 0..6 | unknown, airport, VOR, NDB, fix, user, map point |
| `FPL<n>PT` | str | path terminator (leg type), default `TF` |
| `FPL<n>CRS` | float | published course, degrees magnetic (CF, CA, holds, PI) |
| `FPL<n>DST` | float | arc radius or hold/PI leg length, nm |
| `FPL<n>ALT` | str | altitude constraint: `+N` at or above, `-N` at or below, `@N` at, `BLo,Hi` between |
| `FPL<n>SPD` | int | speed constraint, kt |
| `FPL<n>SGI` | int | segment index (see below) |
| `FPL<n>FLAGS` | int | bits: 1 fly-over, 2 IAF, 4 FAF, 8 MAP, 16 MAHP, 32 from a coded procedure |
| `FPL<n>CTRLAT`, `FPL<n>CTRLON` | float | arc centre (RF, AF) |
| `FPL<n>TURN` | str | `L`, `R` or `""`: arc, hold or PI turn direction |

`FPLCOUNT` is the number of slots in use and `FPLNAME` the plan's name. A
segment table (`FPLSEGCNT`, then `FPLS<n>KIND`, `ID`, `ENT`, `EXT`, `FIRST`,
`LAST`) groups slots into airways and procedures.

**The engine reads the route only when `FPLSEQ` changes.** An editor writes
every slot, then increments `FPLSEQ` to commit. The engine checks the whole
route; if it is valid, it loads it and echoes the number in `FPLLDSEQ`. If not,
it keeps the plan it had and writes the reason to `FPLMSG`, for example
`UNSUPP PI KAUS` or `BADRADIUS RF FIX12`. Loading a new route clears any
active leg.

### Leg types

| Path terminators | How they are flown |
|---|---|
| `IF`, `TF`, `DF` | great circle from the previous point |
| `CF` | the published `CRS` into the fix |
| `RF`, `AF` | constant-radius arc about `CTRLAT`/`CTRLON`, radius `DST`, direction `TURN` (`R` is clockwise) |
| `CA` | the published course until `altitude_key` reaches the constraint |
| `FA` | to the fix, then its course until the altitude is reached |
| `HM`, `HF`, `HA` | holding pattern at the fix: `HM` until the pilot exits, `HF` one circuit, `HA` until the altitude is reached |
| `PI` | procedure turn: 45 degrees off the reciprocal of `CRS` for `DST`, then back inbound |
| `VA`, `VM`, `FM`, `VI` | not flown: the engine suspends and shows phase `VECTORS` |

Any other path terminator rejects the route. An arc needs a turn direction, a
radius above zero and a centre; a hold or PI needs a direction and a leg length;
CA, FA and HA need a readable altitude.

## Commands

The editor drives the engine through `FPLCMD`, a string of the form
`<seq> VERB [arg]`. The verb is case-insensitive. The engine writes `FPLMSG`,
then acknowledges in `FPLCMDACK`: `seq` if the command was accepted, `-seq` if
it was rejected, `-1` if it could not be parsed.

| Command | Effect | Rejected with |
|---|---|---|
| `ACT <k>` | make the leg into slot k active; for k = 1 the leg starts at the present position | `NO PLAN`, `BAD SLOT`, `NO POSITION` |
| `DTO <k>` | direct to slot k from the present position | `NO PLAN`, `BAD SLOT`, `NO POSITION` |
| `DTO` | direct to the target staged in `DTOID`, `DTOLAT`, `DTOLON`, `DTOTYPE`. If it matches a slot (same ident, within 0.001 deg) the plan continues from there; otherwise the engine flies direct off-route and keeps the plan loaded | `NO POSITION` |
| `DTOX` | cancel Direct-To and rejoin the plan on the leg nearest the aircraft | `NO PLAN`, `NO POSITION` |
| `SUSP` | stop sequencing and hold the current TO waypoint; cancels a turn in progress | `NO PLAN` |
| `RESUME` | in order: start the missed approach after the MAP; leave a vector leg direct to the next slot; arm the exit from an `HM` hold; otherwise stop suspending | `NO PLAN` |
| `SCALE 0.3`, `1.0`, `2.0`, `AUTO` | set a manual ceiling on the CDI scale, or clear it | `PARSE` |

## Sequencing

A leg ends when the aircraft passes abeam its fix, or, in a fly-by turn, when it
crosses the bisector of the turn. On each change the engine writes
`SEQ <from> -> <to>` to `FPLMSG`.

The engine never sequences past the last slot or past a slot flagged MAP. When
the aircraft passes the MAP the engine suspends (`FPLSTATE` = 3,
`FPLSUSPMAP` = 1) and waits for `RESUME` to fly the missed approach. A hold in
the last slot keeps circling.

Hold entries follow AIM 5-3-8: teardrop, parallel or direct by the arrival
track, with a 60-second entry leg. Hold turns use `turn_rate_deg_s`, but never
more than 25 degrees of bank, so the radius grows at higher ground speed.

## Fly-by turns

At a fly-by waypoint the engine starts the turn early, so the aircraft rolls out
on the next leg instead of overshooting it.

- Turn radius `R = GS² / (g · tan(turn_bank_deg))`.
- Turn starts `d = R · tan(Δ/2)` before the fix, where Δ is the course change,
  capped at 5 nm.
- From the turn start, the desired track and cross-track come from the arc
  joining the two legs, and switch to the next leg at the end of the arc.

There is no anticipation, and the engine sequences abeam the fix instead, when
the waypoint is flagged fly-over, the turn is more than 120 degrees, there is no
next leg, or either leg is an arc, CA/FA, hold or PI.

`FPLALERT` goes true before each waypoint: within the turn distance plus
`alert_s` seconds of flight (at least 1 nm).

## Flight phase and CDI scale

`CDISCALE` is the CDI full-scale deflection in nm. `FPLCDI` is
`-FPLXTK / CDISCALE`, clamped to -1..+1, and `FPLXTK` is positive right of
track.

| `FPLPHASE` | When | `CDISCALE` |
|---|---|---|
| `ENR` | en route | 2.0 nm |
| `TERM` | within `terminal_nm` of the departure or destination airport | 1.0 nm |
| `TERM`, approach armed (`FPLAPR` = 1) | an approach is loaded (FAF before MAP), within `terminal_nm` of the MAP, at or before the FAF leg | 1.0 nm |
| `LNAV`, approach active (`FPLAPR` = 2) | on the FAF leg, within `approach_ramp_nm` of the FAF | ramps 1.0 to 0.3 nm |
| `LNAV`, approach active | after the FAF, up to the MAP | 0.3 nm |
| missed approach (`FPLAPR` = 3) | after `RESUME` at the MAP | 2.0 or 1.0 nm, as ENR or TERM |
| `HOLD`, `PTURN`, `VECTORS` | in a hold, a procedure turn, or on a vector leg | — |

A Direct-To to a slot between the FAF and the MAP does not activate approach
scaling. A manual `SCALE` can only tighten the scale, never widen it.

## Integrity and loss of position

Guidance fails, and its outputs are flagged `fail`, when:

- there has never been a position, or `LAT`/`LONG` are more than 2 s old (there
  is no dead reckoning);
- `GPS_FIX_TYPE` is below 3 or flagged fail, or has gone stale after being
  written. A source that never writes it is not checked.

When the `integrity_key` value exceeds the phase's `hal_nm`, `FPLINTEG` goes
false, the guidance is flagged `bad`, and an active approach drops back to
armed at 1.0 nm. When the key is missing, stale or turned off, `FPLINTEG` stays
true and `FPLMSG` reads `NO INTEGRITY DATA` once per activation. The X-Plane
plugin does not write it.

## Outputs

| Key | Meaning |
|---|---|
| `FPLSTATE` | 0..3: no plan active, on a leg, direct-to, suspended |
| `FPLACTLEG`, `FPLACTSEG` | active slot and segment |
| `FPLPHASE`, `FPLAPR`, `FPLINTEG`, `CDISCALE` | phase, approach state, integrity, CDI scale |
| `FPLCRS` | desired track, degrees magnetic |
| `FPLXTK` | cross-track, nm, positive right |
| `FPLCDI` | CDI deflection, -1..+1 |
| `FPLTF` | TO/FROM |
| `WPNAME`, `WPLAT`, `WPLON`, `WPFROM`, `WPNEXT` | TO, FROM and next waypoints |
| `WPDIS`, `WPETE` | distance (nm) and time (s) to the TO waypoint; `WPETE` is bad below 30 kt |
| `FPLREMDIS`, `FPLREMETE`, `FPLSEGREM` | distance and time to the end of the plan, distance to the end of the segment |
| `FPLALERT` | waypoint alert |
| `FPLMSG`, `FPLCMDACK`, `FPLLDSEQ` | messages, command acknowledgement, loaded route number |

## Internal plan or external navigator

`GPSSRC` chooses where GPS guidance comes from: `0` the internal plan, `1` an
external navigator (the [[Navigator Adapter]], the `garmin_gnx375` plugin, or
the X-Plane GPS on the bench). The shipped `compute` config selects:

| Output | `GPSSRC` = 0 | `GPSSRC` = 1 |
|---|---|---|
| `GPSCRS` | `FPLCRS` | `EXTCRS` |
| `GPSCDI` | `FPLCDI` | `EXTCDI` |
| `GPSTF` | `FPLTF` | `EXTTF` |
| `XTRACK` | `FPLXTK` | `EXTXTK` |

`NAVSRC` = 2 then routes the GPS keys into `COURSE`, `CDI` and `TOFROM`, which
the HSI reads. `GPSBRG`, the GPS bearing pointer, always points at the internal
plan's `WPLAT`/`WPLON`. `state_persist` saves `GPSSRC` across restarts.

## Persistence

The engine saves the plan and its state (active leg, Direct-To, suspend,
manual scale, approach, hold and turn state) to `state_file` whenever they
change, and restores them `restore_delay` seconds after start. A saved plan that
no longer passes the route checks is not loaded; the reason goes to `FPLMSG`.
