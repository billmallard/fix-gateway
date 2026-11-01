# Flight plan FIX keys (FP1, PA3, PA4, PA13, RS1)

Spec: [fix-gateway#22](https://github.com/billmallard/fix-gateway/issues/22)
(FP1), [fix-gateway#27](https://github.com/billmallard/fix-gateway/issues/27)
(PA3, the leg model), [fix-gateway#28](https://github.com/billmallard/fix-gateway/issues/28)
(PA4, Tier-1 leg types), [fix-gateway#29](https://github.com/billmallard/fix-gateway/issues/29)
(PA13, Tier-2 leg types), [fix-gateway#39](https://github.com/billmallard/fix-gateway/issues/39)
(RS1, the route segment table); `makerplane/briefs/procedures_and_airways_plan.md`
section 3.2 (the maos-workspace repo) -- Bill's decision 1, 2026-09-18 --
and `makerplane/briefs/route_segments_plan.md` sections 3 and 5 -- Bill's
ruling of 2026-10-24 on AER-2091 that airway and procedure segments are
first-class on the bus. PA3
landed the key contract only; PA4 is the first item that actually flies
anything other than an implicit `TF` leg, and the guardrail-1 rejection gate
that this key contract enables. PA13 adds the leg types the brief phased
out of PA4 -- arcs, altitude-terminated legs, holds and procedure turns.
RS1 adds the segment table: which slots are "V27 from GVO to AVOLS", which
are the departure, and so on, so no display has to guess.

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
| `FPL1..100 CRS` | float | 0..359.9 deg magnetic | editor | Leg course (`CF`/`CA`/`FC`/`VA`/`VM`/`VI`, and (PA13) the inbound course to the fix for `HM`/`HF`/`HA`/`PI`) |
| `FPL1..100 DST` | float | 0..999.9 nm | editor | Leg distance. Tier 1: unused by the great-circle terminators. PA13: an `RF`/`AF` arc's radius, or a hold/procedure-turn's outbound leg length -- both required, never defaulted |
| `FPL1..100 ALT` | str | packed, guide notation: `""` none, `"+3500"` at-or-above, `"-3500"` at-or-below, `"B2900,4000"` between, `"@3500"` at | editor | Altitude constraint. PA13: also the termination test for `CA`/`FA`/`HA` |
| `FPL1..100 SPD` | int | 0..400 kt, 0 = none | editor | Speed constraint |
| `FPL1..100 SGI` | int | 1..`FPLSEGCNT` for a slot in use (0 only past `FPLCOUNT`); database range 0..100 | editor | Index of the segment this slot belongs to (RS1, replaces PA3's `FPLfSEG`) |
| `FPL1..100 FLAGS` | int | bitfield: `0x01` fly-over, `0x02` IAF, `0x04` FAF, `0x08` MAP, `0x10` MAHP, `0x20` from a coded procedure | editor | Leg flags (replaces FP1's `FPLfROLE`) |
| `FPL1..100 CTRLAT` / `CTRLON` | float | +-90 / +-180 deg | editor | Arc center point (PA13, `RF`/`AF` only) |
| `FPL1..100 TURN` | str | `"L"`/`"R"`/`""` | editor | Turn direction (PA13, required for `RF`/`AF`/`HM`/`HF`/`HA`/`PI`) |
| `FPLCOUNT` | int | 0..100 | editor | Slots in use |
| `FPLNAME` | str | <=32 chars, no `;` | editor | Route name |
| `FPLSEQ` | int | >=0 | editor (engine on restore) | Commit counter, written last |
| `FPLLDSEQ` | int | >=0, 0 = none loaded | engine | `FPLSEQ` of the route the engine has loaded (PA8). Follows `FPLSEQ` on every accepted commit and on restore; a rejected commit leaves it where it was |

All of the above, and the segment table below, are `tol: 0`, written by the
editor, and republished by the engine on restore -- except `FPLLDSEQ`, which
only the engine writes.

**A rejected commit leaves the refused route on the bus.** The editor's
slots stay where it wrote them while the engine keeps flying the route it
had, so the slot block alone cannot tell a display which route is flown.
`FPLLDSEQ` can: when it differs from `FPLSEQ`, the slots hold a route the
engine refused, and a display draws none of it (procedures brief guardrail
1 on the map, pyEfis #239). A display that finds no `FPLLDSEQ` (an older
gateway) treats the slots as loaded, as before the key existed.

The rare fields a real procedure database carries (`theta`/`rho`/`rnp`/
recommended navaid) are **not** on the wire -- resolved to geometry at load
time by the editor, which has the procedures database open (PA5), rather
than shipped down a ~1 KB netfix frame a hundred times over. PA13 promoted
the arc center and turn direction off that list once Tier 2 needed real
geometry for them, not raw theta/rho.

**PA4 flew four path terminators (plus vectors); PA13 adds the rest of the
recognized set.** `IF`/`TF`/`CF`/`DF` (Tier 1 -- 70% of approach legs, 91% of
SID/STAR legs), `VA`/`VM`/`FM`/`VI` (vector legs -- a SID/STAR problem, never
flown by the box, guardrail 4), and now (PA13, `engine.PT_TIER2`) `RF`/`AF`
arcs, `CA`/`FA` altitude-terminated legs, and `HM`/`HF`/`HA`/`PI` holds and
procedure turns are the complete supported set (`engine.PT_SUPPORTED`).
`IF`/`TF`/`DF` fly the prior slot to this one, the same great circle the
engine has always flown; `CF` and (PA13) `CA` fly the leg's published
`FPLfCRS`, not a bearing derived from the previous slot. `RF`/`AF` fly a real
circular arc around `FPLfCTRLAT`/`CTRLON` at radius `FPLfDST`. `HM`/`HF`/`HA`
fly an AIM entry (direct, teardrop or parallel) and then the racetrack:
two standard-rate half circles and the outbound and inbound legs, the
outbound leg ending at `FPLfDST` along-track from the fix
(fix-gateway#45); `PI` flies an outbound/inbound pair of straight legs
around the fix (both inbound legs use the same course-anchor trick as
`CF`); `FA` flies to its fix
like `DF`, then continues on `FPLfCRS` past it like `CA`. Loading a route
with any other path terminator (still unsupported after PA13: `FC`, the `V`
records other than the four vector types, and a handful more) rejects the
**whole** route: the engine keeps whatever was previously loaded, and
`FPLMSG` gets a reason (see the command channel section below for the exact
format) -- never a silent coercion to `TF`, never a partial load
(guardrail 1). **Guardrail 1 also covers a *recognized* Tier-2 type with
incomplete data** -- an `RF` with no turn direction, an `HM` with no leg
length, a `CA` with no altitude constraint -- rejecting the whole route the
same way (`BADTURN`/`BADRADIUS`/`BADCENTER`/`BADALT`/`BADLEGLEN`) rather than
inventing a value the coded data never carried.

## Route segment table (RS1)

A route is an ordered list of segments, and each segment owns a contiguous
run of slots. The legs stay what the engine flies: one leg block, one
sequencer. The segment says where the legs came from and what they are
called. PA3's `FPLfSEG` said a slot belonged to *a* departure but not
*which* segment or where it started and ended, so the FPL page had to keep
airway membership in a display-only tag and re-attach it after every sync
by guessing. That tag was lost on restart, on a second display and on
invert, and the map never had it at all. With the table on the bus, every
reader sees the same segments.

| Key | Type | Range | Tol | Writer | Meaning |
|---|---|---|---|---|---|
| `FPLS1..100 KIND` | int | 0 waypoint, 1 airway, 2 departure, 3 arrival, 4 approach, 5 missed | 0 | editor (engine on restore) | What the segment is |
| `FPLS1..100 ID` | str | <=24 chars, no `;`. `""` for a waypoint row, required for an airway | 0 | editor (engine on restore) | `V27`, `HYDRR6.RW33L`, `RNAV33L.HENER` |
| `FPLS1..100 ENT` | str | equals `FPLfID` of slot `FIRST` | 0 | editor (engine on restore) | Entry fix ident |
| `FPLS1..100 EXT` | str | equals `FPLfID` of slot `LAST` | 0 | editor (engine on restore) | Exit fix ident |
| `FPLS1..100 FIRST` / `LAST` | int | 1..100, `FIRST <= LAST`; `FIRST == LAST` for a waypoint row | 0 | editor (engine on restore) | First and last slot of the segment. Redundant with `FPLfSGI`, so a reader never scans 100 slots, and checked against it |
| `FPLSEGCNT` | int | 0 for an empty route, else 1..`FPLCOUNT` | 0 | editor (engine on restore) | Segments in use |

**Kind 0 is "waypoint", not Direct-To.** It is one waypoint the pilot
entered, flown as a straight leg from the slot before it, exactly as every
slot always has been. A Direct-To is the `DTO` command (below) and never
touches this table. The brief called kind 0 "direct". It was renamed so
nobody reads it as Direct-To; the wire value is unchanged.

**One row per entered waypoint, and the table is as long as the slot block**
(Bill, 2026-10-26). A waypoint row holds exactly one slot. Airways and
procedures are one row per run. A route therefore never needs more rows
than it has slots, so the table is indexed by the slot block's own variable
`f` (`FPLSfKIND` and so on, `database/variables.yaml` `f: 100`). The two
always have the same length by construction: the segment table can never be
the limit, only the slot count can. `tests/test_database.py`
(`test_segment_table_is_exactly_as_long_as_the_slot_block`) fails if the
row counts, or any range that counts slots or segments (`FPLCOUNT`,
`FPLSEGCNT`, `FPLACTLEG`, `FPLACTSEG`, `FPLfSGI`, `FIRST`/`LAST`), stop
agreeing. The slot limit itself stays at 100, the GNX's limit. Raising it is
a separate decision, because a longer route could not be sent to a
certified GNX.

**Writer order.** The editor writes the slots and the table (any order),
then `FPLSEGCNT` and `FPLCOUNT`, then `FPLSEQ` last, as before. Rows past
`FPLSEGCNT` and slots past `FPLCOUNT` are ignored and may hold stale
values. The database clamps an out-of-range write (101 becomes 100) rather
than refusing it, so an out-of-range write produces a clamped value, which
then fails the checks below instead of loading.

**Integrity, checked by the engine on every `FPLSEQ` commit** (and on
restore from its state file), after the leg checks pass. Any failure
rejects the whole commit: the previously loaded plan stays loaded and
active, and `FPLMSG` gets the first failure, in this order:

| `FPLMSG` | Rule |
|---|---|
| `SEGCNT <n>` | `FPLSEGCNT` is 0 for an empty route and 1..`FPLCOUNT` otherwise |
| `SEGSGI <slot> <ident> <sgi>` | Every slot's `FPLfSGI` is 1..`FPLSEGCNT` |
| `SEGNONCONTIG S<k> <ident>` | A segment's slots are one contiguous run (named at the first slot of its second run) |
| `SEGORDER S<k> <ident>` / `SEGORDER S<k> UNUSED` | Segments appear in table order with none skipped: slot 1 is in segment 1, each slot is in the same segment as the one before it or the next one, and the last slot is in segment `FPLSEGCNT` |
| `SEGKIND S<k> <kind>` | `KIND` is 0..5 |
| `SEGRANGE S<k> <first>-<last>` | `FIRST`/`LAST` are exactly the first and last slot whose `FPLfSGI` is k |
| `SEGENT S<k> <ent>` / `SEGEXT S<k> <ext>` | `ENT`/`EXT` equal the `FPLfID` of slot `FIRST`/`LAST` |
| `SEGLEN S<k> <id> <n>` | A waypoint row is exactly 1 slot (`<id>` reads `-`). An airway spans at least 2 slots, its entry and exit fixes |
| `SEGID S<k> <id>` | `ID` is at most 24 chars, empty for a waypoint row, present for an airway |

The entry and exit rule applies to every kind, so a segment's `ENT` is its
own first slot's ident. For a missed approach that is the first missed leg,
not the MAP: the MAP is the last slot of the approach segment, and every
slot belongs to exactly one segment. A slot with an empty ident (a `CA` leg
with no fix) makes a matching `""` entry or exit.

**Example**, the route `KSBA RZS GVO V27 AVOLS KSMX`, where RZS is a
waypoint the pilot entered and V27 is expanded to five fixes (`GVO`, three
en-route fixes, `AVOLS`):

| Slots | `FPLfSGI` | Segment row |
|---|---|---|
| 1 `KSBA` | 1 | `FPLS1`: KIND 0, ID `""`, ENT/EXT `KSBA`, FIRST/LAST 1 |
| 2 `RZS` | 2 | `FPLS2`: KIND 0, ID `""`, ENT/EXT `RZS`, FIRST/LAST 2 |
| 3..7 `GVO` .. `AVOLS` | 3 | `FPLS3`: KIND 1, ID `V27`, ENT `GVO`, EXT `AVOLS`, FIRST 3, LAST 7 |
| 8 `KSMX` | 4 | `FPLS4`: KIND 0, ID `""`, ENT/EXT `KSMX`, FIRST/LAST 8 |

`FPLSEGCNT = 4`. The airway's entry fix is its first slot, so the leg
*into* `GVO` belongs to the airway segment. That is what `FPLACTSEG`
reports while flying it (below).

**Frame budget.** Every key is still one scalar per netfix line. The
largest legal commit (100 slots, each its own waypoint row, every slot
string at its limit) is 2,006 lines and 43.9 KB, of which the segment table
is 600 lines and 11.5 KB. The longest line is 46 bytes, against the
1024-byte receive buffer both netfix ends use. Bill measured the cost of the
100-row table on an x86 workstation with this branch's `database.py` and
netfix server: about 2,000 keys and 1.1 MB, with a full commit over netfix
taking 12 ms. That compares with 1,580 keys, 0.9 MB and 9 ms for a 30-row
table. The Pi 5 is expected to be a few times slower; it hasn't been
measured. FPL keys are not mapped to CAN-FIX, so CAN identifier space is
not a constraint. `tests/plugins/flightplan/test_full_commit_netfix.py`
sends exactly that commit through a real netfix server and checks that the
engine loads all of it.

## Loaded-procedure provenance (block level)

One fact per plan, not copied onto every slot. The procedure idents PA3
also kept here (`FPLDPID`, `FPLSTARID`, `FPLAPRID`) moved into the segment
table in RS1, where each one sits on the segment it names.

| Key | Type | Writer | Meaning |
|---|---|---|---|
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

**`DTOX` with no plan loaded** cancels a bare `DTO` flown with no plan
(state `DIRECT`): there is no leg to rejoin, so navigation drops to `NONE`
and the command is acked. With nothing active it is rejected `NO PLAN`.

**`RESUME` off a vector leg (PA4)** is not the MAP's "sequence to the
MAHP" -- there is no track to rejoin, since the box never flew one. It
activates the *next* slot direct from wherever the aircraft actually is at
the moment of the command (like a `DTO`), posting `"RESUME NAV -> <ident>"`.
With no next slot to join, it posts `"NO NEXT LEG"` and stays suspended --
unlike the MAP's no-MAHP case, there is no extended course to keep flying.

**`RESUME` on a flying `HM` hold (PA13)** means "exit at the next fix
passage", not "un-suspend" -- the hold is actively flown, with live
course/cross-track guidance the whole time (`FPLSTATE` stays `LEG`, not
`SUSP`), so there is nothing to resume into. It posts `"HOLD EXIT ARMED"`
and the aircraft completes the current circuit before sequencing onward.
`HF` and `PI` never need this -- they exit automatically after one
circuit/turn; `HA` exits automatically once the coded altitude is reached.

| Key | Type | Writer | Meaning |
|---|---|---|---|
| `FPLCMD` | str | editor | The command string |
| `FPLCMDACK` | int | engine | `= seq` on success, `-seq` on rejection |
| `FPLMSG` | str | engine | Last message / rejection reason. Commands: `NO PLAN`, `BAD SLOT`, `NO POSITION`, `PARSE`, a sequence trace like `SEQ GVO -> RZS` (or, PA13, `SEQ ALT -> <ident>` when a `CA`/`FA` leg terminates on altitude with no fix of its own), or (PA4, off a vector leg) `RESUME NAV -> <ident>` / `NO NEXT LEG`, or (PA13, on a flying `HM` hold) `HOLD EXIT ARMED`. Route loads (PA4/PA13, guardrail 1): `"UNSUPP <PT> <ident>"` for an unrecognized terminator, or `"BADTURN <PT> <ident>"` / `"BADRADIUS <PT> <ident>"` / `"BADCENTER <PT> <ident>"` / `"BADALT <PT> <ident>"` / `"BADLEGLEN <PT> <ident>"` for a recognized Tier-2 type missing the fields its geometry needs, or (RS1) one of the `SEG...` reasons in the segment table section -- either way the whole route is rejected, not just that leg |

## Engine outputs (FP2 writes; `tol: 5000`)

| Key | Type | Range | Meaning |
|---|---|---|---|
| `FPLSTATE` | int | 0 NONE, 1 LEG, 2 DIRECT, 3 SUSP | Engine state |
| `FPLSUSPMAP` | int | 0/1 | Suspended at the MAP (G49): 1 only while `FPLSTATE = 3` is the MAP auto-suspend, where `FPLCMD RESUME` flies the missed approach (`FPLAPR` -> 3). 0 for any other state, including a pilot `SUSP` past the MAP (held from before the MAP, or after a missed with no MAHP leg), where RESUME only un-suspends. A display shows "Fly the missed" on 1 and "Resume" otherwise; with the key absent (older gateway) it shows "Resume" |
| `FPLACTLEG` | int | 0..100 | Slot of the TO waypoint, 0 = none |
| `FPLPHASE` | str | `ENR` / `TERM` / `LNAV` / `LOI` / `VECTORS` / `HOLD` / `PTURN` / `0.30 NM` / `1.00 NM` / `""` | Flight-phase annunciation. `VECTORS` (PA4, guardrail 4) wins over everything else while the active leg is `VA`/`VM`/`FM`/`VI` -- `FPLSTATE` is also forced to `SUSP` and `FPLCRS`/`FPLXTK`/`FPLCDI`/`WPDIS`/`WPETE`/`FPLREMDIS`/`FPLREMETE` all read 0 (the box invents no heading, and no distance to one either). `HOLD` (PA13) shows while flying a published `HM`/`HF`/`HA` circuit, `PTURN` while flying a `PI` procedure turn -- both keep live course/cross-track guidance, unlike `VECTORS` |
| `FPLAPR` | int | 0 none, 1 armed, 2 active, 3 missed | Approach state |
| `FPLINTEG` | bool | | Integrity gate satisfied for the current phase |
| `CDISCALE` | float | 0.3..2.0 nm | Full-scale deflection (ramps 1.0 -> 0.3 nm over the 2 nm before the FAF) |
| `FPLCRS` | float | 0..359.9 deg magnetic | Desired track (DTK) |
| `FPLXTK` | float | +-100 nm, positive = right of track | Cross-track error, same sign convention as `XTRACK` |
| `FPLCDI` | float | -1..+1 | `-FPLXTK / CDISCALE`, clamped -- the sign convention `garmin_gnx375/__init__.py:167-169` uses, so the HSI needle behaves identically for the internal plan and an external navigator |
| `FPLTF` | int | 0/1/2 | TO/FROM |
| `FPLFRLAT` / `FPLFRLON` | float | deg | FROM point of the active leg (previous waypoint, or the direct-to activation point). Flagged `fail` whenever there is no FROM point (NULLWP1): no plan active, and during failed guidance, where the engine blanks it to 0.0 |
| `WPFROM` / `WPNEXT` | str | <=6 chars | FROM and NEXT waypoint idents |
| `WPDIS` | float | nm | Distance to the TO waypoint |
| `WPETE` | int | s, `bad` when GS < 30 kt | Time to the TO waypoint |
| `FPLREMDIS` / `FPLREMETE` | float / int | nm / s | Remaining distance/time to the end of the plan |
| `FPLACTSEG` | int | 0..100 | Active segment (RS1): `FPLfSGI` of the active TO slot `FPLACTLEG`. 0 with no plan active or on an off-route direct-to. Kept during failed guidance (it is identification, like `FPLACTLEG`). A `DTO` to a fix inside an airway keeps the airway: the engine never rewrites the table, and the remaining slots are still that segment |
| `FPLSEGREM` | float | 0..9999.9 nm | Distance remaining in the active segment (RS1): to the TO fix, then along the plan to the segment's `LAST` slot. 0 with no active segment, on a vector leg, and during failed guidance (flagged `fail`/`bad` with the other distance outputs) |
| `FPLALERT` | bool | | Waypoint sequencing alert (10 s or 1 nm before sequencing, whichever is longer) |

`WPLAT`/`WPLON`/`WPNAME` (`database/mavlink.yaml`) are pre-existing keys; the
engine becomes their writer when the `flightplan` plugin is enabled (FP2).
`ifly`/`navigator_adapter`/`mavlink` must stay disabled alongside it -- one
key, one writer. This item only fixes `WPLON`'s range: it was clamped to
+-90 (a latent bug for longitudes past 90 deg), now +-180, matching
longitude's actual domain.

`WPLAT`/`WPLON` are flagged `fail` whenever there is no active TO waypoint
(NULLWP1): no plan active, a vector leg with no TO point, and during failed
guidance, where the engine blanks them to 0.0. The flag says whether there
IS a waypoint; it is not the ownship-position `fail` the distance outputs
carry, since a fix's location does not depend on the GPS. With no plan the
published 0.0/0.0 is a placeholder, and before this flag the compute chain
(`bearing` -> `GPSBRGT` -> `GPSBRG` -> `BRG1`) turned it into a valid bearing
to 0N 0E. `WPNAME`/`WPFROM`/`WPNEXT` read `""` with no waypoint and carry no
flag. Value and flags land in one database write, so a subscriber never sees
the placeholder under the previous cycle's valid flag.

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
| `EXTXTK` | float | +-100 nm, positive = right of track | external navigator plugin | External navigator cross-track error |
| `GPSCRS` / `GPSCDI` / `GPSTF` | (existing) | | compute `select` on `GPSSRC` | `{FPLCRS,EXTCRS}`, `{FPLCDI,EXTCDI}`, `{FPLTF,EXTTF}` |
| `XTRACK` | (existing) | | compute `select` on `GPSSRC` | `{FPLXTK,EXTXTK}` |

On the bench, `xplane.yaml`'s nav RREF rows now write `EXTCRS`/`EXTCDI`
(previously `GPSCRS`/`GPSCDI` directly) so X-Plane's FMS is the external
navigator. `garmin_gnx375` and `navigator_adapter` write `EXTCRS` (APB
field 8, the leg's desired track, magnetic), `EXTXTK` (RMB cross-track),
`EXTCDI` (`-EXTXTK / cdi_full_scale_nm`) and `EXTTF` (APB perpendicular
passed) too (AER-4473). Before that they wrote `COURSE`, `CDI` and `XTRACK` directly,
racing compute's `NAVSRC` selects and `xte` for the same keys, and their
`COURSE` was the bearing from the aircraft to the waypoint, not the desired
track.

`XTRACK` is a `GPSSRC` select of `FPLXTK` and `EXTXTK`, exactly like
`GPSCDI`, so the cross-track always comes from the same source as the needle
(AER-4473, accepted by Bill 2026-11-01). It replaced the `xte` compute, which
measured against `WPLAT`/`WPLON` -- the internal plan's waypoint -- even when
`GPSSRC` = 1, and with `GPSSRC` = 0 was a second computation of what the
engine already publishes as `FPLXTK`. X-Plane's rows map no `EXTXTK`, so on
the bench `XTRACK` reads 0 with `GPSSRC` = 1. `GPSBRGT`/`GPSBRG` still use
`WPLAT`/`WPLON`, so with `GPSSRC` = 1 the GPS bearing pointer aims at the
internal plan's waypoint; that is a noted follow-on.

## Integrity key for FP2

`FPLINTEG`'s gate (brief section 3.3) reads `GPS_FIX_TYPE` (below a 3-D fix
-> guidance `fail`) and a horizontal-accuracy/protection key (above the
phase's horizontal alert limit -> `FPLINTEG = false`). The default
`integrity_key` for FP2's `connections/flightplan.yaml` is
**`GPS_ACCURACY_HORIZ`** (`database/ahrs.yaml`, units **feet** -- FP2 must
convert to nm to compare against the nm-denominated HAL table
`{enr: 2.0, term: 1.0, lnav: 0.3}`). Its `max` is 100,000 ft (about
16.5 nm), above every HAL, so a degraded HPL/HFOM -- or one past the max,
which clamps to it -- still raises LOI. It was 200 ft (0.033 nm) until
fix-gateway#52, which clamped every value below every HAL.

Plugins confirmed to write it: `mavlink` (`Mav.py`, mm -> ft) and `gpsd`
(`__init__.py`, m -> ft). **Not written by `xplane`** -- the bench's X-Plane
FMS publishes neither `GPS_ACCURACY_HORIZ` nor `GPS_FIX_TYPE`, so on the
bench `FPLINTEG` stays `true` and `FPLMSG` reads `NO INTEGRITY DATA` once per
plan activation, per the brief's "no-accuracy source" rule -- this is
expected bench behaviour, not a defect.

**Altitude key for PA13.** `CA`/`FA` legs and `HA` holds terminate on a live
altitude reading rather than a distance -- never on a guess: with no data,
they simply never end (the same "no data, no invented output" treatment
`FPLINTEG` already gives an unpublished accuracy key). The default
`altitude_key` is **`ALT`** (`database/ahrs.yaml`, Indicated Altitude, feet
-- no unit conversion needed, unlike `GPS_ACCURACY_HORIZ`). X-Plane's FMS
publishes `ALT` on the bench, so this one has a real bench source where
`integrity_key` does not.

**`GPS_FIX_TYPE` scale across sources.** Every writer now uses the key's
documented 0..5 scale (0-1 no fix, 2 2-D, 3 3-D, 4 3-D+DGPS, 5 3-D+RTK), so
the engine's "3 or more" gate means the same thing for each. `gpsd` and
`mavlink` write it natively. The two NMEA writers (`garmin_gnx375` and the
`navigator_adapter` Garmin parser) used to write the raw GGA quality code
(0/1/2), which failed the gate on every valid GNX fix; since AER-4224 they map
GGA quality plus GSA mode (or the GGA satellite count when no GSA is sent)
through `fixgw.nmea_fix.gps_fix_type`. A WAAS fix reads 4.

## Divergence from upstream

This fork leads the flight-plan contract (ORG-011, Bill, 2026-10-24:
upstream MakerPlane / CAN-FIX coordination is not a gate). Upstream
`makerplane/FIX-Gateway` has no flight-plan block at all: no
`database/flightplan.yaml`, no `FPL*`/`DTO*`/`GPSSRC`/`EXT*` keys, and no
`f` index variable. Everything in this document is fork-only. The
`WPLAT`/`WPLON`/`WPNAME` keys exist upstream (`database/mavlink.yaml`); the
fork widens `WPLON` to +-180 (above) and makes the engine their writer when
the plugin is enabled.

The specific points a merge with upstream would have to reconcile:

- The `f: 100` index variable in `database/variables.yaml`, which now
  indexes both the slots and the segment table. Upstream could define `f`
  for something else.
- The route block and segment table: `FPLf*` (14 fields), `FPLS1..100` (6
  fields), `FPLSEGCNT`, `FPLCOUNT`, `FPLNAME`, `FPLSEQ`, `FPLLDSEQ`.
- PA3 and RS1 were both breaking changes inside the fork (Bill lifted the
  compatibility constraint, procedures brief section 3.2 and route
  segments brief section 3). There is no upstream version to stay
  compatible with.

Nothing here is relicensed or closed. It is public in the fork under the
existing license and documented in this file.

## Roadmap design record

A "Flight plan" block for `MAOS-DESIGN/docs/AVIONICS_STACK_ROADMAP.md`
section 9.1 is included in this PR's description per the issue's own
allowance ("PR it to the MAOS workspace repo, or list the block text in the
PR for Bill") -- fix-gateway's write boundary does not include the
maos-workspace ops repo.
