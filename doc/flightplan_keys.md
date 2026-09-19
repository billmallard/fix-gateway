# Flight plan FIX keys (FP1, PA3, PA4)

Spec: [fix-gateway#22](https://github.com/billmallard/fix-gateway/issues/22)
(FP1), [fix-gateway#27](https://github.com/billmallard/fix-gateway/issues/27)
(PA3, the leg model), [fix-gateway#28](https://github.com/billmallard/fix-gateway/issues/28)
(PA4, Tier-1 leg types); `makerplane/briefs/procedures_and_airways_plan.md`
section 3.2 (the maos-workspace repo) -- Bill's decision 1, 2026-09-18. PA3
landed the key contract only; PA4 is the first item that actually flies
anything other than an implicit `TF` leg, and the guardrail-1 rejection gate
that this key contract enables.

One key, one writer, with a single documented exception: after a gateway
restart the engine (FP2) republishes the route block and bumps `FPLSEQ`
itself, mirroring `state_persist` restoring `NAVSRC`.

## Route block

The route crosses the bus as an indexed key block (`f: 100` in
`database/variables.yaml`), the idiom the database already expands (`EGTec`,
`BTNb`). The editor (pyEfis) writes every slot, then `FPLCOUNT`, then bumps
`FPLSEQ` last. Consumers act only on `FPLSEQ` change -- netfix processes one
connection's writes in order, so the block is complete when the counter
lands. `tol: 0` (no staleness auto-flag; these are edited, not streamed).

**PA3 widened the slot from a point to a leg** (a SID/STAR/approach is a
sequence of legs, not a flat list of points): `FPLfROLE` is gone, replaced by
`FPLfFLAGS` plus six new fields. This is a breaking rewrite of FP1's key
spellings, not an addition to them -- Bill lifted the compatibility
constraint (decision 1, 2026-09-18; guardrail 6 struck) because the feature
has no consumers outside dev yet. An ordinary point-to-point route is still
just every slot's `FPLfPT = "TF"` (the default) with the other leg fields at
their zero value -- nothing changes for that case.

| Key | Type | Range | Writer | Meaning |
|---|---|---|---|---|
| `FPL1..100 ID` | str | <=6 chars, uppercase, `""` = empty | editor | Slot ident |
| `FPL1..100 LAT` / `LON` | float | +-90 / +-180 deg | editor | Slot position |
| `FPL1..100 TYPE` | int | 0..6 (0 unknown, 1 airport, 2 VOR, 3 NDB, 4 fix, 5 user, 6 map point) | editor | Slot type |
| `FPL1..100 PT` | str | ARINC 424 2-char path terminator (`TF`/`IF`/`CF`/`DF`/`CA`/`RF`/`AF`/`HM`/...); `"TF"` initial | editor | Path terminator -- geometry this leg flies |
| `FPL1..100 CRS` | float | 0..359.9 deg magnetic | editor | Leg course (`CF`/`CA`/`FC`/`VA`/`VM`/`VI`) |
| `FPL1..100 DST` | float | 0..999.9 nm (minutes for a time-terminated leg, Tier 2) | editor | Leg distance |
| `FPL1..100 ALT` | str | packed, guide notation: `""` none, `"+3500"` at-or-above, `"-3500"` at-or-below, `"B2900,4000"` between, `"@3500"` at | editor | Altitude constraint |
| `FPL1..100 SPD` | int | 0..400 kt, 0 = none | editor | Speed constraint |
| `FPL1..100 SEG` | int | 0..4 (0 enroute, 1 departure, 2 arrival, 3 approach, 4 missed) | editor | Which procedure segment this slot belongs to |
| `FPL1..100 FLAGS` | int | bitfield: `0x01` fly-over, `0x02` IAF, `0x04` FAF, `0x08` MAP, `0x10` MAHP, `0x20` from a coded procedure | editor | Leg flags (replaces FP1's `FPLfROLE`) |
| `FPLCOUNT` | int | 0..100 | editor | Slots in use |
| `FPLNAME` | str | <=32 chars, no `;` | editor | Route name |
| `FPLSEQ` | int | >=0 | editor (engine on restore) | Commit counter, written last |

The rare fields a real procedure database carries (`theta`/`rho`/`rnp`/turn
direction/recommended navaid) are **not** on the wire -- resolved to geometry
at load time by the editor, which has the procedures database open (PA5),
rather than shipped down a ~1 KB netfix frame a hundred times over.

**PA4 flies four path terminators and rejects everything else outright.**
`IF`/`TF`/`CF`/`DF` (Tier 1 -- 70% of approach legs, 91% of SID/STAR legs)
and `VA`/`VM`/`FM`/`VI` (vector legs -- a SID/STAR problem, never flown by
the box, guardrail 4) are the complete supported set
(`engine.PT_SUPPORTED`). `IF`/`TF`/`DF` fly the prior slot to this one, the
same great circle the engine has always flown; `CF` flies the leg's
published `FPLfCRS`, not a bearing derived from the previous slot. Loading a
route with any other path terminator (`RF`/`AF` arcs, `CA`/`FA`
altitude-terminated legs, `HM`/`HF`/`HA` holds, `PI` procedure turns -- Tier
2, PA13) rejects the **whole** route: the engine keeps whatever was
previously loaded, and `FPLMSG` gets a reason (see the command channel
section below for the exact format) -- never a silent coercion to `TF`,
never a partial load (guardrail 1).

## Loaded-procedure provenance (block level)

One fact per procedure, not copied onto every slot (the first draft of this
design put a provenance string on all 100 slots -- up to 100 copies of the
same string for one fact). `FPLfSEG` says which of these, if any, a given
slot belongs to.

| Key | Type | Writer | Meaning |
|---|---|---|---|
| `FPLDPID` | str | editor | Loaded departure (SID) ident, e.g. `"HYDRR6"` (`""` = none) |
| `FPLSTARID` | str | editor | Loaded arrival (STAR) ident (`""` = none) |
| `FPLAPRID` | str | editor | Loaded approach ident, e.g. `"I33L"` (`""` = none) |
| `FPLAPRTYPE` | str | editor | Loaded approach type (ILS/LOC/RNAV/VOR/NDB/GPS...) |
| `FPLDBCYC` | str | editor | Nav database AIRAC cycle the loaded procedure(s) came from, e.g. `"2609"` -- PA9 annunciates when it has expired (guardrail 3: annunciate, never refuse the load) |

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
`SCALE <0.3|1.0|2.0|AUTO>`. PA3 reserves `PROC`, `PROCX` and `AWY` for
procedure/airway loading (section 3.2) -- this engine does not dispatch them
yet; unknown verbs are rejected (`PARSE`), never ignored.

**`RESUME` off a vector leg (PA4)** is not the MAP's "sequence to the
MAHP" -- there is no track to rejoin, since the box never flew one. It
activates the *next* slot direct from wherever the aircraft actually is at
the moment of the command (like a `DTO`), posting `"RESUME NAV -> <ident>"`.
With no next slot to join, it posts `"NO NEXT LEG"` and stays suspended --
unlike the MAP's no-MAHP case, there is no extended course to keep flying.

| Key | Type | Writer | Meaning |
|---|---|---|---|
| `FPLCMD` | str | editor | The command string |
| `FPLCMDACK` | int | engine | `= seq` on success, `-seq` on rejection |
| `FPLMSG` | str | engine | Last message / rejection reason. Commands: `NO PLAN`, `BAD SLOT`, `NO POSITION`, `PARSE`, a sequence trace like `SEQ GVO -> RZS`, or (PA4, off a vector leg) `RESUME NAV -> <ident>` / `NO NEXT LEG`. Route loads (PA4, guardrail 1): `"UNSUPP <PT> <ident>"`, e.g. `"UNSUPP RF ARCFIX"`, when a leg's path terminator is not one this engine can fly -- the whole route is rejected, not just that leg |

## Engine outputs (FP2 writes; `tol: 5000`)

| Key | Type | Range | Meaning |
|---|---|---|---|
| `FPLSTATE` | int | 0 NONE, 1 LEG, 2 DIRECT, 3 SUSP | Engine state |
| `FPLACTLEG` | int | 0..100 | Slot of the TO waypoint, 0 = none |
| `FPLPHASE` | str | `ENR` / `TERM` / `LNAV` / `LOI` / `VECTORS` / `0.30 NM` / `1.00 NM` / `""` | Flight-phase annunciation. `VECTORS` (PA4, guardrail 4) wins over everything else while the active leg is `VA`/`VM`/`FM`/`VI` -- `FPLSTATE` is also forced to `SUSP` and `FPLCRS`/`FPLXTK`/`FPLCDI`/`WPDIS`/`WPETE`/`FPLREMDIS`/`FPLREMETE` all read 0 (the box invents no heading, and no distance to one either) |
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
