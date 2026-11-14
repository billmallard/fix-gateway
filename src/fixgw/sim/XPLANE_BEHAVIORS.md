# X-Plane behavior register

Every place the X-Plane simulator differs from real avionics, from our
navigation engine (`fixgw.plugins.flightplan`), or from the synthetic
reference (`fixgw/sim/refnav.py`). Each entry is classified and carries its
evidence. Until now each of these was worked out once, on one issue, and
remembered only there. The register is where they stop being folklore.

## The rule

**A disagreement between X-Plane and our synthetic reference or engine is
entered here as `unknown`, with the test that would settle it, before
anything is tuned.** No check, tolerance, skip or engine change is made to
fit X-Plane until the entry exists.

**Moving an entry out of `unknown` goes to Bill as a card, with the
evidence,** whenever the new class would change engine code or a test
allowance. A reclassification that changes neither can be made in a PR,
with the evidence in the entry.

Every X-Plane allowance in `tests/sim/` cites its `XPB-NNN` in a comment, and
every entry below names the code or test that relies on it. When you add an
allowance, add the citation in both places. This register is public (the
fix-gateway fork): nothing from the proprietary side goes in it.

## Classes

| Class | Meaning | What we do |
|---|---|---|
| `sim-quirk` | X-Plane differs from real avionics. | We do not model it; the X-Plane comparison allows for it. |
| `real` | X-Plane reproduces something real units do. | We match it. |
| `unknown` | Open. | The discriminating test is named; nothing is tuned until it runs. |
| `interface` | How X-Plane exposes data, not avionics semantics. | Handled in the plugin, the driver, or the comparison's plumbing. |

"Reference" in each entry is the real-avionics behavior, with a citation: a
standard, a manufacturer's manual, or "none found". A citation is never
inferred. Where one is quoted second-hand, the entry says where it was
quoted.

## Setup the evidence comes from

Unless an entry says otherwise:

- **X-Plane 12, Personal Edition**, on Bill's workstation (AER-4072, comment
  7bedd544, 2026-10-28). The exact build is not recorded.
- **Avionics:** the G1000. The bench aircraft are the Cirrus SR22 (G1000)
  by default and the Cessna 172 SP (G1000) on request (AER-4072, comment
  2e4803cc). No GNS 530 or default-FMS aircraft has been flown on the bench.
- **Nav data:** through 2026-10-30, X-Plane's default data, cycle 2406. From
  2026-10-31, a different provider's cycle 2611 in `Custom Data` (XPB-021).
  Our profiles pin FAA CIFP 2611 (XPB-015).
- **Sessions:**
  - `cbbeafe57646`: the FP8 session 2 recording, 2026-10-29 05:32Z, SR22, pilotless sim bench. The trace and the bench bus log are committed in `tests/sim/fixtures/` (README there).
  - AER-809 session 1: 2026-10-28, flown by Bill at the sim. No session id. The aircraft is not recorded.

## Index

| Id | Behavior | Class |
|---|---|---|
| [XPB-001](#xpb-001) | Switches to the next waypoint about 0.2 nm early, even at collinear fixes | `sim-quirk` |
| [XPB-002](#xpb-002) | Course reads about 0.11 deg above the WGS-84 reference on straight legs | `unknown` |
| [XPB-003](#xpb-003) | G1000 SVS terrain horizon tilts less than the attitude horizon in a bank | `unknown` |
| [XPB-004](#xpb-004) | Hold: shows the hold course through the entry and turns, FROM 0.2 nm before the hold fix | `unknown` |
| [XPB-005](#xpb-005) | The bench's pilot commands (SUSP/RESUME) reach our engine only | `interface` |
| [XPB-006](#xpb-006) | No cross-track or distance from X-Plane's navigator in the trace or on the bus | `interface` |
| [XPB-007](#xpb-007) | No GPS fix type or horizontal accuracy | `interface` |
| [XPB-008](#xpb-008) | `EXTTF` was not mapped when FP8 was recorded (G38); mapped since fix-gateway#84 | `interface` |
| [XPB-009](#xpb-009) | RREF requests need the 400-byte dataref field and the legacy port 49000 | `interface` |
| [XPB-010](#xpb-010) | RREF subscriptions after an X-Plane restart (G47): re-sent every 5 s | `interface` |
| [XPB-011](#xpb-011) | Data Output (DATA) rows never reached the bench while RREF worked | `interface` |
| [XPB-012](#xpb-012) | A not-received nav deflection arrives as a float32 subnormal | `interface` |
| [XPB-013](#xpb-013) | Disabled DATA slots carry -999.0 | `interface` |
| [XPB-014](#xpb-014) | Web API client's 10 s timeout ended a KDED session | `interface` |
| [XPB-015](#xpb-015) | X-Plane's nav cycle (2406) must contain the profile's procedure | `interface` |
| [XPB-016](#xpb-016) | Starting a flight through the web API deadlocks X-Plane | `interface` |
| [XPB-017](#xpb-017) | No API path loads the FMS on a G1000 aircraft | `interface` |
| [XPB-018](#xpb-018) | Autopilot command semantics: heading-bug writes, `alt_vs`, servo drops | `interface` |
| [XPB-019](#xpb-019) | Pause and unpause lag after launch, and the start flips a preference | `interface` |
| [XPB-020](#xpb-020) | LAT holds its value for seconds while the stream is alive | `interface` |
| [XPB-021](#xpb-021) | X-Plane's nav data provider changed on 2026-10-31 | `interface` |
| [XPB-022](#xpb-022) | On V27 GVO-GOLET, course reads 1.4 deg below ours: when the plan turns 154 deg at GVO, X-Plane's next leg is not the published one, whether or not the turn is flown | `sim-quirk` |
| [XPB-023](#xpb-023) | Fly-by turn: CDI from an arc until the bisector, then from the next leg's straight line | `sim-quirk` |

---

## Avionics semantics

### XPB-001

**X-Plane switches to the next waypoint about 0.2 nm early, even at collinear fixes.**

- **Class:** `sim-quirk` (Bill, AER-4286, card `bc4e2e7d`, 2026-11-02). The quirk is the fixed ~0.2 nm floor: X-Plane switches about 0.2 nm before a fix even when there is no turn. No standard or Garmin guide sets a minimum lead, so we do not model it, and the X-Plane comparison allows for it (0.3 nm, below). The rest of the shape, the course changing at turn start and the waypoint at the bisector, matches the Garmin guides. Whether our engine should follow that is a separate question, AER-4671. It is not part of this classification.
- **Setup:** X-Plane 12, SR22 G1000, cycle 2406, session `cbbeafe57646`.
- **Observed** (AER-4286, comment 4d4661bb, 2026-10-31, except where a row says otherwise). The lead is the along-track distance before the fix where X-Plane's active waypoint (or, for HANNU's first row, its course) changed. Time is lead / groundspeed.

  | Fix | Session | Fix type, turn | GS kt | Lead nm | Time s |
  |---|---|---|---:|---:|---:|
  | PAMJI -> CIMSO | `cbbeafe57646` | fly-by, 0 deg | 111.7 | 0.195 | 6.3 |
  | CIMSO -> ANIBY | `cbbeafe57646` | FAF, fly-by, 0 deg | 115.0 | 0.195 | 6.1 |
  | ANIBY -> RW30 | `cbbeafe57646` | fly-by, 0 deg | 110.0 | 0.199 | 6.5 |
  | HANNU, course change | `cbbeafe57646` | fly-by, 22 deg | 95.0 | 0.225 | 8.5 |
  | HANNU -> WINCH, waypoint | `cbbeafe57646` | fly-by, 22 deg | 94.2 | 0.00 (abeam) | 0 |
  | RW30 -> HANNU | `cbbeafe57646` | MAP, fly-over | 101.4 | 0.029 | 1.0 |
  | ELZUS | AER-809 session 1 | fly-by, 16 deg | not recorded | 0.254 | not recorded |
  | PAMJI -> CIMSO | `ba51cf5e0d3b` / `d4dee8e34bdf` | fly-by, 0 deg | 111.2 / 111.7 | 0.205 / 0.204 | 6.6 |
  | CIMSO -> ANIBY | `ba51cf5e0d3b` / `d4dee8e34bdf` | FAF, fly-by, 0 deg | 113.5 / 113.4 | 0.203 / 0.201 | 6.4 |
  | ANIBY -> RW30 | `ba51cf5e0d3b` / `d4dee8e34bdf` | fly-by, 0 deg | 107.2 / 107.0 | 0.203 / 0.202 | 6.8 |
  | HANNU, course change | `ba51cf5e0d3b` / `d4dee8e34bdf` | fly-by, 22 deg | 99.2 / 99.2 | 0.243 / 0.242 | 8.8 |
  | PAMJI -> CIMSO | `3a2caebb1de7` (C172) | fly-by, 0 deg | 105.1 | 0.200 | 6.9 |
  | CIMSO -> ANIBY | `3a2caebb1de7` (C172) | FAF, fly-by, 0 deg | 103.4 | 0.205 | 7.1 |
  | **ANIBY -> RW30** | `3a2caebb1de7` (C172) | fly-by, 0 deg | **86.7** | **0.203** | **8.4** |
  | HANNU, course change | `3a2caebb1de7` (C172) | fly-by, 22 deg | 78.0 | 0.216 | 10.0 |
  | GEARE, course change | QA1 KSSF R28 (AER-4219) | fly-by, 90 deg | 121 | 0.81 | |
  | XEHMO, course change | QA1 KDED R30 (AER-4219) | fly-by, 85 deg | 101 | 0.63 | |

  - Rows from `ba51cf5e0d3b`, `d4dee8e34bdf` and `3a2caebb1de7` (AER-4286, 2026-11-01 and 2026-11-02) are the distance to the fix (`gps_dme_nm`) in the last sample before the switch; cross-track was 0.000 there. The first two were queued as 90 and 140 KTAS, but the driver takes its speeds from its aircraft table, not the profile's `aircraft.tas_kt`, so both flew the SR22's ~110 kt. They are repeats. `3a2caebb1de7` flew the C172 (100 KIAS cruise, 85 KIAS from the FAF), which is what put ANIBY at 86.7 kt.
  - GEARE and XEHMO are straight-line distances from the aircraft to the fix on `tests/sim/fixtures/qa1_{kssf_r28,kded_r30}_xplane_2026-11-01.csv.gz` (AER-4286, comment 0ee62d0f). At both, the waypoint switched later, at the turn bisector: 0.33 nm before GEARE and 0.25 nm before XEHMO.

  - At HANNU, the one real fly-by turn on the recording, X-Plane changed the course 0.225 nm early but the waypoint only abeam the fix.
  - At the MAP, a fly-over, there is no 0.2 nm lead. The behavior is bounded to fly-by fixes so far.
  - **ELZUS does not fit.** AER-809, comment 5687f7c2, gives "ELZUS against X-Plane's own sequence: 0.083 vs 0.254 nm": our engine's lead against X-Plane's, both measured from the fix. The "0.17 nm before we did" in the same comment is the difference between them. 0.254 nm is above the ~0.2 nm floor in the current hypothesis, and a 16 deg turn's R tan(delta/2) is only about 0.06 to 0.11 nm at 120 kt and 15 to 25 deg of bank (QA, AER-4375).
  - PAMJI is collinear: both legs bear 330.6 T in the CIFP. It is not the 12 deg turn the issue's first table gave.
  - The times are recomputed from the committed trace as lead / groundspeed. 4d4661bb's table gave 6.8, 6.3 and 6.8 s for the three collinear fixes. Time from the switch to closest approach in the trace is 6.6, 6.1 and 6.5 s.
- **Reference.** The sources below are quoted on AER-4286, comment 22584c01.
  - ICAO Doc 9613 Vol I Att. A §5.2, citing DO-236 §3.2.5.4.1: "The RNAV and RNP system transitions from one leg to the next at the bisector of the turn." At a collinear fix the bisector passes through the fix, so the expected lead there is about zero.
  - The GNS 430 guide (p.194), the G1000 TBM guide (p.709) and the GPS 150XL guide (p.78) describe two events: the desired track changes at turn start, and the TO/FROM changes at the turn midpoint. None covers a zero-degree turn.
  - No standard or Garmin guide found sets a minimum sequencing distance or a constant lead.
- **Hypotheses ruled out** (comment 4d4661bb):
  - Y = V^2/g with groundspeed, from DO-236C's bank rule (proposed in comment 22584c01). ANIBY is the slowest collinear fix but has the largest lead, and V^2/g predicts 0.132 nm at HANNU.
  - A fixed time: 6.1 to 6.5 s at the approach fixes, but 8.5 s at HANNU.
  - Both again, decisively, at ANIBY on `3a2caebb1de7`: 86.7 kt, lead 0.203 nm. V^2/g predicts 0.110 nm and the 6.5 s fixed time 0.157 nm.
- **Current hypothesis.** The course switches at turn start with a floor of about 0.2 nm: `max(~0.2 nm, R tan(delta/2))`. At collinear fixes the lead is 0.195 to 0.205 nm from 86.7 to 115 kt on all four sessions, so the floor is a fixed distance, not a speed or time term. At GEARE and XEHMO the turn term is the larger one. R tan(delta/2) at 15 deg of bank gives 0.80 nm at GEARE and 0.51 nm at XEHMO against 0.81 and 0.63 measured; that is one bank fitted to two points. The waypoint switches at the bisector where there is a turn. At a collinear fix it switches with the course, at the floor.
- **Discriminating test** (AER-4286):
  - Speed: done (above). A faster run is not needed to separate the floor from V^2/g or a fixed time. It would need a per-session speed setting in xplane-bench, which the driver does not have.
  - Turn size: GEARE and XEHMO supply the large-turn case. A third turn would test the bank assumed in the turn term. That is not needed to classify the floor.
- **Our side, for contrast:**
  - Until AER-4675 the engine changed course and waypoint together at turn start, with a 0.1 nm floor on the turn anticipation (comment 4d4661bb, step 4). Bill ruled on AER-4671 (2026-11-02): the course changes at turn start, the waypoint at the turn bisector, and there is no floor.
  - Since AER-4675 the engine and X-Plane change the waypoint in the same 0.2 s row at GEARE, XEHMO and HENER, and 0.2 s apart at CUGAK. Until TURN1 X-Plane turned its course 4.6 to 5.1 s before the engine at those fixes, the engine sizing turns at standard rate.
  - Since TURN1 (AER-4759) the engine sizes fly-by turns on a nominal 15 deg bank, from the Garmin guides, not from X-Plane. X-Plane now turns its course 0.4 s before the engine at GEARE (121 kt), 2.3 s at HENER (121 kt), 4.0 s at XEHMO (102 kt) and 4.3 s at CUGAK (116 kt). So 15 deg fits X-Plane at GEARE only; at the slower turns X-Plane starts about 0.1 nm earlier than either rule. Its flown turns averaged 15 deg of bank at GEARE and XEHMO and 17 deg at HENER (track rate x groundspeed). At straight-through fixes the engine now switches at the fix, so the whole ~0.2 nm lead (5.9 to 6.8 s) is X-Plane's.
  - X-Plane's CDI in a fly-by turn is its own entry, [XPB-023](#xpb-023).
- **Relied on by:**
  - `tests/sim/test_fp8_xplane_replay.py:75`: the sequence points are within 0.3 nm of X-Plane's. 0.3 nm is the FP8 brief's bound (AER-809), not fitted to this.
  - `tests/sim/test_fp8_xplane_replay.py:79`: HANNU -> WINCH is not compared, because X-Plane switches that waypoint abeam the fix.
  - `tests/sim/test_fp8_xplane_replay.py:133`: `_shared` excludes 5 s around either system's sequence.
  - `tests/sim/fixtures/qa1_kssf_r28_xplane_2026-11-01.yaml`, `qa1_kded_r30_xplane_2026-11-01.yaml`, `sim2b_v27_xplane_2026-11-01.yaml`: `except_s_around_sequence: 6` with `around_keys` `FPLALERT` for X-Plane's earlier turn start, and `skip_when_differ: [WPNAME, gps_id]` for its early switch at straight-through fixes. The CDI through the turn is [XPB-023](#xpb-023)'s.
  - `tests/sim/fixtures/fp8_xplane_2026-10-29.yaml:24,29`: `except_s_around_sequence: 5`.
  - `tests/sim/fixtures/fp8_xplane_2026-10-29.yaml:16`: the EXTTF 2 skip, for the FROM before WINCH.
  - The synthetic tier's 5 s `WPNAME`/`REFWPT` window would not hold against X-Plane: the lead is 6.1 to 6.9 s at the SR22's speeds and 8.4 s at 86.7 kt (AER-4286, AER-4283).

### XPB-002

**X-Plane's course reads about 0.11 deg above the WGS-84 reference on straight legs.**

- **Class:** `unknown`. The likely explanation is known. What is open is which side real units are on.
- **Setup:** X-Plane 12, AER-809 session 1 (aircraft not recorded). Also session `cbbeafe57646` (SR22).
- **Observed:**
  - SYN1 (AER-4073, comment bdf38b51, 2026-10-28; fix-gateway#47): "the engine and X-Plane agree with each other to 0.02 deg. Both read 0.11 deg above the reference on every leg. That's the earth model: a great circle on a sphere versus the WGS-84 geodesic, 0.111 deg on ELZUS-PAMJI."
  - AER-4286's description (on `cbbeafe57646`): 0.11 to 0.14 deg above the reference on every straight leg.
- **Reference.** None found for whether a certified navigator computes courses on the ellipsoid or on a sphere. The reference navigator uses GeographicLib WGS-84 geodesics by design (`PROFILE_FORMAT.md`, "The rule for the reference").
- **What is inferred, not shown.** That X-Plane's own model is a sphere: its offset matches the computed sphere-minus-ellipsoid azimuth on one leg, and the engine (spherical on straight legs) agrees with it.
- **Discriminating test.** For X-Plane's model: compute the sphere-minus-WGS-84 azimuth for every straight leg of `cbbeafe57646`, and compare it with X-Plane's course minus the reference, leg by leg. Agreement to about 0.01 deg on legs of different bearing means X-Plane is spherical. For the class: a manufacturer's statement, or a real unit's course on a long leg against a geodesic.
- **Relied on by:**
  - No X-Plane comparison allows for it, because X-Plane and the engine agree.
  - The synthetic tier allows for the engine's own sphere: `src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml` (`FPLCRS`/`REFCRS` 0.5 deg; `WPDIS`/`REFDIS` `tol_rel: 0.005`, "sphere vs WGS-84 distance").
  - The QA hold profiles' 1.5 deg course tolerance names the sphere/WGS-84 difference in hold turns.
  - Hold points are built on WGS-84 since AER-4218 (`src/fixgw/plugins/flightplan/engine.py:932`).

### XPB-003

**X-Plane's G1000 SVS terrain horizon tilts less than the attitude horizon in a bank.**

- **Class:** `unknown`. No real G1000's SVT behavior in a bank has been found, so nothing shows X-Plane differing from real avionics. AC 20-167A is a certification criterion, not an observation of a real unit. Bill's direction frames it as a behavior "that may show up, in different forms, in both X-Plane and real Garmin equipment" (AER-4286, comment 22584c01). The X-Plane side is relayed and unquantified (below).
- **Setup:** X-Plane 12, G1000. The aircraft is not named in the source.
- **Observed.**
  - In X-Plane: G32 (pyEfis #304), the terrain horizon tilting less than the attitude horizon in a bank, "turned out to be visible in X-Plane's own G1000 once he knew what to look for". That is Bill's direction, written from his account on AER-4286, comment 22584c01, 2026-10-31. No first-hand comment, angle or screenshot exists.
  - pyEfis had the same defect: at 30 deg of bank the terrain horizon drew at 19.1 deg (pyEfis #304). It was fixed in pyEfis #318 (merged 2026-10-27, AER-3865), with QA's verification on AER-3984.
- **Reference:** FAA AC 20-167A §4.1.6.1.2 and §4.1.6.1.3 (cited in pyEfis #304): "Scale and align all spatially referenced symbology within each axis with the imagery so as not to present any misleading information to the pilot." This is guidance for certificated installations. A real G1000's SVT behavior in a bank: none found.
- **Discriminating test:** a screenshot of X-Plane's G1000 PFD at 30 deg of bank, with the terrain horizon angle measured against the attitude horizon, and a real G1000's SVT photographed in a bank. If both tilt less, the class is `real`; whether pyEfis should then match it, against the AC it follows now, goes to Bill as a card. If only X-Plane does, the class is `sim-quirk`.
- **Relied on by:** nothing in fix-gateway. pyEfis follows the AC, not X-Plane. Its SVS roll is tested against the attitude horizon (pyEfis #318), and no pyEfis test compares against X-Plane's display.

### XPB-004

**Hold: X-Plane shows its hold course through the entry and the turns, and goes FROM 0.2 nm before the hold fix.**

- **Class:** `unknown`.
- **Setup:** X-Plane 12, SR22 G1000, session `cbbeafe57646`, the WINCH hold.
- **Observed** (AER-4283, fix-gateway#77; written into the recording's skips):
  - X-Plane shows the hold's inbound course throughout the hold, including the entry and the turns.
  - It goes FROM from 0.2 nm before WINCH, where it already shows that course. The early FROM is likely the same lead as XPB-001, which would make it a consequence of that entry rather than a separate behavior.
  - Wherever the engine is on the inbound leg (course 121) and X-Plane is TO, the two agree within 2 deg and 0.1 CDI.
- **Our side:**
  - The engine flies the AIM 5-3-8 entry chosen by sector, and turns the course with the aircraft. That is Bill's call on AER-4076, implemented in fix-gateway#49.
  - Outbound, the needle is centred on the racetrack's outbound side, not pegged on the inbound course extended (AER-4076, comment 3392586d).
- **Reference:**
  - FAA-H-8083-15B ch. 10 and AIM 5-3-8 give the entries and the pilot technique (quoted on AER-4076, comment ac8520b5).
  - What a Garmin unit's desired track and CDI show during a hold entry and turn: none found.
- **Discriminating test:**
  - For the early FROM: the XPB-001 sessions. The test is whether the FROM lead before a hold fix follows the same rule.
  - For the display through the entry: the hold section of a Garmin pilot's guide (G1000 or GNS 530W), or a real unit flown through an entry.
- **Relied on by:**
  - `tests/sim/fixtures/fp8_xplane_2026-10-29.yaml:13,16,25,30`: the skips `FPLPHASE: HOLD` and `EXTTF: 2`.
  - `tests/sim/test_fp8_xplane_replay.py:125`: `_shared` drops the last 0.5 nm to WINCH.
  - `tests/sim/test_fp8_xplane_replay.py:156`: `test_hold_inbound_leg_matches_xplane` compares only the inbound leg.
  - `tests/sim/test_fp8_xplane_replay.py:170`: the CDI sign is checked only where both deflections exceed 0.05 of full scale. The 0.1 magnitude bound applies to every row.
  - `tests/sim/test_fp8_xplane_replay.py:175`: `_hold_inbound` picks that leg's rows on the engine's own leg phase (`INBOUND`) with X-Plane TO, not on agreement with X-Plane, so an inbound course disagreement fails the 2 deg bound instead of dropping out (fix-gateway#89). `test_hold_inbound_selection_keeps_a_course_disagreement` (line 184) pins that.

### XPB-023

**In a fly-by turn, X-Plane's CDI follows an arc until the bisector, then the next leg's straight line.**

- **Class:** `sim-quirk` (Bill, AER-4715, 2026-11-03: the CDI stays on the arc to the arc's end). This is a second departure from the Garmin guides, separate from XPB-001's early switch. We don't copy it.
- **Setup:** X-Plane 12, SR22 G1000. Sessions `acdbb0757fca` (KSSF, GEARE 90 deg), `ea108163a87d` (KDED, XEHMO 85 deg) and the SIM2b V27 recording (HENER 61 deg), replayed through the engine at fix-gateway#123.
- **Observed** (AER-4715, fix-gateway#123):
  - From X-Plane's turn start to the bisector, its deviation follows an arc. At HENER it is within 0.002 of full scale of the engine's arc cross-track, where the next leg's straight line reads up to 0.156 off. At XEHMO and GEARE it follows a wider arc than ours (XPB-001: its turn starts about 5 s earlier).
  - From the first row after the waypoint switch at the bisector, X-Plane's CDI equals the next leg's straight-line cross-track to the third decimal. That is the engine's value before #123, at HENER, GEARE and XEHMO.
  - So a turn flown on X-Plane's arc shows a CDI step at the bisector, which then decays to zero at the arc's end. In the 6 s after the switch, X-Plane's CDI and ours differ by up to 0.129 of full scale at GEARE (TERM, 1 nm), 0.094 at XEHMO and 0.028 at HENER (ENR, 2 nm), with the engine at standard rate.
  - Since TURN1 (AER-4759) the engine's arc is a 15 deg bank, longer than the standard-rate one, so the step lasts longer: at GEARE it is 0.216 at the switch and still 0.118 at 6.1 s after it, past the 6 s window.
- **Our side:** the engine and the synthetic reference measure the cross-track from the turn's arc, from turn start to the arc's end, on both sides of the bisector (fix-gateway#123; `makerplane/briefs/flight_plan_plan.md` section 3.3.1, row 2).
- **Reference:**
  - Garmin's online pilot's guide, "Flight Plan Navigation Indications": "The CDI will indicate the cross track error relative to the arc for the duration of the turn." Quoted second-hand from AER-4715, comment a6464f0d (Bill's account, 2026-11-03).
  - Garmin GNC 250XL pilot's guide, p. 84: turn anticipation gives "CDI guidance along a curved path segment", and "during the transition, the CDI display will be referenced to" that path.
- **Where the comparison is blind:** from the bisector to the end of the engine's arc the two navigators measure different paths, so the X-Plane CDI checks skip those rows (`skip_when: {eng_arc_after_bisector: 1}`, a replay flag, TURN1). Before TURN1 the 6 s window around each waypoint switch hid most of the step by coincidence; with the 15 deg arc, GEARE failed 6 rows just past it (0.118 at 125.4 s), which is what this entry predicted. The skip is that region and no wider, and the tolerance is not widened. The CDI in the second half of a fly-by turn is therefore not checked against X-Plane at all; the engine tests and the synthetic gate cover it.
- **Relied on by:**
  - `tests/sim/fixtures/qa1_kssf_r28_xplane_2026-11-01.yaml`, `qa1_kded_r30_xplane_2026-11-01.yaml`, `sim2b_v27_xplane_2026-11-01.yaml`: the CDI checks compare the first half of each turn (no `eng_turning` skip since #123) and skip the second (`eng_arc_after_bisector`, TURN1). Largest compared difference: 0.099, before the bisector at XEHMO.

## Interface

### XPB-005

**The bench's pilot commands reach our engine only.**

- **Class:** `interface`.
- **Observed.** The bench orchestrator commands our engine: `RESUME` 10 s after the MAP auto-SUSP, `SUSP` 1 nm before HANNU, and `RESUME` once the engine shows FROM. X-Plane's navigator is not sent any of these.
  - The replay re-derives these commands from engine state (`tests/sim/xplane_replay.py:9-14`).
  - While suspended, the pilot's commands decide what the engine shows, not the leg. So there is nothing on X-Plane's side to compare.
- **Reference:** not applicable; this is the comparison's setup.
- **Relied on by:** `tests/sim/fixtures/fp8_xplane_2026-10-29.yaml:11,25,30` (the skip `FPLSTATE: 3`); `tests/sim/test_fp8_xplane_replay.py:76` (RW30 -> HANNU is not compared, because the engine sequences there on the orchestrator's RESUME). The engine's own SUSP behavior is checked without X-Plane in `tests/sim/test_fp8_xplane_replay.py` (`test_no_sequence_at_the_map_auto_susp_then_from`, `test_susp_does_not_sequence_past_hannu`).

### XPB-006

**There is no cross-track or distance from X-Plane's navigator in the trace or on the bus.**

- **Class:** `interface`.
- **Observed.**
  - The bench's truth log carries X-Plane's active waypoint, course, deflection and to/from, but no cross-track and no distance to the fix (`tests/sim/fixtures/README.md`).
  - The bench's `xplane` plugin maps course, deflection and to/from only (`src/fixgw/config/connections/xplane.yaml:90-92`); nothing maps cross-track or distance.
  - X-Plane's web API does return the navigator's cross-track: SIM0 lists "active waypoint, course, cross-track, CDI, to/from" (AER-4072, comment 7bedd544). So the gap is in what we record, not in what X-Plane exposes. An X-Plane distance-to-fix source has not been checked.
- **Consequence.** The recording's checks are course, CDI and sequence only. There is no `XTK`/`DIS` check against X-Plane, and legs are labelled by X-Plane's active waypoint.
- **Relied on by:** `tests/sim/profile_gate.py:135-136`; `tests/sim/xplane_replay.py:158-164` (`bus_rows`).

### XPB-007

**X-Plane publishes no GPS fix type or horizontal accuracy.**

- **Class:** `interface`.
- **Observed.** Neither `GPS_FIX_TYPE` nor `GPS_ACCURACY_HORIZ` has an X-Plane source. The engine's integrity gate skips a fix type that has never published (AER-4217, comment d79d7d6e, fix-gateway#58).
- **Relied on by:**
  - `tests/sim/xplane_replay.py:14-16`: the integrity inputs are `None`, as on the bench.
  - The engine's "never published, gate skipped" path (AER-4217).

### XPB-008

**`EXTTF` was not mapped when FP8 was recorded (G38); it is mapped since fix-gateway#84.**

- **Class:** `interface`.
- **Observed.**
  - When `cbbeafe57646` was recorded, `connections/xplane.yaml` mapped nothing to `EXTTF`, so it reads 0 throughout that session's bench bus log (`tests/sim/fixtures/README.md`).
  - The truth trace does carry `gps_fromto` (1 TO, 2 FROM).
  - The issues are fix-gateway#41 (filed from AER-809, comment fad7fff0) and AER-4324. fix-gateway#84 fixed it: `EXTTF` now maps `sim/cockpit/radios/gps_fromto` (`src/fixgw/config/connections/xplane.yaml:92`). Its encoding is checked against the recorded trace (`test_gps_fromto_is_1_to_2_from_as_exttf_declares`), not yet against a bench bus log recorded since.
- **Relied on by:**
  - `tests/sim/xplane_replay.py:158-164`: the gate fills `EXTTF` from the trace for the FP8 recording.
  - `tests/sim/test_fp8_bench_bus.py:57-61`: `test_the_bench_never_wrote_exttf` pins the FP8 log's `EXTTF` at 0. A recording made since should compare `EXTTF` with `gps_fromto` instead.
  - `src/fixgw/sim/PROFILE_FORMAT.md`, the SYN4 key table.

### XPB-009

**RREF requests need the 400-byte dataref field and the legacy port 49000.**

- **Class:** `interface`.
- **Observed** (commit `e7bc77c`, Bill Mallard, 2026-06-17, against a live X-Plane 12 over localhost; no Paperclip issue):
  - X-Plane silently ignores an RREF request whose dataref field is not exactly 400 bytes. It still replies, but with index 0 and value 0, so a short request looks like it works.
  - The newer receive port (default 49010) does not accept the RREF protocol. Only the legacy port, default 49000, does.
- **Reference:** none found. This is from the live behavior only.
- **Relied on by:**
  - `src/fixgw/plugins/xplane/__init__.py:184-187` (`udp_out` default 49000) and `:268-276` (`<4sxii400s`).
  - `src/fixgw/config/connections/xplane.yaml:14-22`.
  - `tests/plugins/xplane/test_xplane.py:376-378` (a 413-byte packet).

### XPB-010

**RREF subscriptions after an X-Plane restart (G47): the plugin re-sends them every 5 s.**

- **Class:** `interface`.
- **Observed** (AER-4232, comment c17cb404, 2026-10-30):
  - The FP8 sessions sat with keys flagged old until a `fixgw` restart, which was read as "subscriptions lost on an X-Plane restart" (fix-gateway#51).
  - **That premise was false.** The plugin already re-sent its subscriptions about every 5 s (`7bc7a1b`). tcpdump showed 19 x 413-byte RREF requests to port 49000 every 5.0 s.
  - Each "restart fixed it" was a script that also added the RREF position block. Before that, LAT rode DATA rows, which never reached the bench (XPB-011).
  - fix-gateway#66 put the re-send on a monotonic timer (`rref_resubscribe`).
- **Not proven:** whether a real X-Plane 12 honours a re-sent subscription. The test is one sim session started without restarting `fixgw`, watching an RREF key's old flag clear.
- **Reference:** none found. "X-Plane treats a repeat subscription to the same index as a no-op" is stated in the code without a source.
- **Relied on by:**
  - `src/fixgw/plugins/xplane/__init__.py:203-208` and `:383`.
  - `tests/plugins/xplane/test_xplane_resubscribe.py:108,120,136`.
  - The `sim_follow.py` fallback described in `makerplane/processes/sim_bench.md` (maos-workspace) depends on X-Plane data appearing without a `fixgw` restart.

### XPB-011

**Data Output (DATA) rows never reached the bench while RREF worked.**

- **Class:** `interface`.
- **Observed** (AER-809, comment a2603637, 2026-10-28):
  - After Data Output was set in X-Plane, "tcpdump shows only the RREF replies, the beacon and the ForeFlight broadcast, no DATA rows on 49001."
  - Position and attitude were moved onto RREF datarefs for the session.
  - The cause was never identified.
- **Reference:** none found.
- **Relied on by:** the bench configuration, which takes position on RREF. `connections/xplane.yaml:66` still maps `LAT`/`LONG` from DATA index 20, so a bench that depends on DATA for position reads it as old (AER-4232).

### XPB-012

**A not-received nav deflection arrives as a float32 subnormal.**

- **Class:** `interface`.
- **Observed** (fix-gateway#20): with a localizer tuned but not received, `nav1_hdef_dot` and `nav1_vdef_dot` arrived as bits `0x80000004`.
- **Reference:** none found.
- **Relied on by:**
  - `src/fixgw/plugins/xplane/__init__.py:76-80` and `:299`: the plugin flushes the value to 0.0. That is cosmetic: it is not a validity signal.
  - `tests/plugins/xplane/test_xplane.py:310-333`.

### XPB-013

**Disabled DATA slots carry -999.0.**

- **Class:** `interface`.
- **Observed:** recorded in the plugin's code comment only (`src/fixgw/plugins/xplane/__init__.py:94-99`), with no issue or session. Example: MAGVAR = -999 turning HEAD into HEAD+999.
- **Reference:** none found.
- **Relied on by:** `src/fixgw/plugins/xplane/__init__.py:99` and `:235` (the value is not written to the database).

### XPB-014

**The web API client's 10 s timeout ended a KDED session.**

- **Class:** `interface` (sim bench driver).
- **Observed** (AER-4219, comment 64d2e44e, 2026-10-31, QA):
  - Session `3dab5ea3e5fb` (KDED RNAV 30) was flying at 70.5 s and ended in `error` at 93.9 s with `TimeoutError: timed out`. The source called the cause "most likely the X-Plane web API client's 10 s timeout (`xplane.py`)".
  - It has happened once. The resubmit is `ea108163a87d`.
- **Relied on by:** nothing in fix-gateway. The client is in `billmallard/xplane-bench`. Two aborts of one profile make it a driver bug, per `processes/sim_bench.md`.

### XPB-015

**X-Plane's nav cycle (2406) must contain the profile's procedure.**

- **Class:** `interface` (nav data).
- **Observed** (AER-4219, comment 64d2e44e):
  - Session `ae4d2057bd45` failed at t=0 with `KeyError: 'KSSF R28'`. The driver looks the approach up in X-Plane's own nav data, cycle 2406, which has no `R28` at KSSF. The profile pins 2611.
  - Every session reports the cycle mismatch, which is not fatal by itself (`processes/sim_bench.md`).
  - For FP8 the KSMX R30 fixes are identical in both cycles (AER-809).
  - Bill installed cycle 2611 in `Custom Data` on 2026-10-31, which has the KSSF `R28` approach (AER-4219, comment 9fb5ad1e). KSSF is resubmitted as `acdbb0757fca`. The provider changed with it (XPB-021).
- **Relied on by:**
  - `tests/sim/fixtures/README.md:10` (the FP8 recording flew cycle 2406).
  - `src/fixgw/sim/profiles/qa1-missed-coded-turn-kssf-r28.yaml`, whose tier-SIM check waits on this.

### XPB-016

**Starting a flight through the web API deadlocks X-Plane.**

- **Class:** `interface` (driver).
- **Observed** (AER-4072, comment 7bedd544, SIM0, 2026-10-28):
  - The deadlock happened in 4 of 4 attempts. The log goes silent right after the aircraft's xlua plugin loads.
  - The same flight JSON on the command line (`--new_flight_json`) works.
- **Relied on by:** nothing in fix-gateway. The driver launches X-Plane from the command line (`billmallard/xplane-bench`).

### XPB-017

**No API path loads the FMS on a G1000 aircraft.**

- **Class:** `interface` (driver).
- **Observed** (AER-4072, comment 7bedd544): the CDU datarefs are blank on the SR22. Loading a plan needs an XPLM plugin (`XPLMLoadFMSFlightPlan`).
- **Relied on by:** nothing in fix-gateway. The driver's plugin loads the `.fms` plan (AER-4072, comment 19592b57).

### XPB-018

**Autopilot command semantics: heading-bug writes, `alt_vs`, servo drops.**

- **Class:** `interface` (driver).
- **Observed** (AER-4072, comments 19592b57 and 7bedd544):
  - Writing the heading bug directly made the autopilot turn the long way round. The driver steps the bug instead.
  - `alt_vs` takes the current vertical speed, so the rate must be set after the command.
  - The servos dropped twice mid-run in SIM0. The cause is open, and the driver guards against it by pausing the sim.
- **Reference:** none found for real-autopilot behavior on a large heading-bug change.
- **Relied on by:** nothing in fix-gateway.

### XPB-019

**Pause and unpause lag after launch, and the command-line start flips a preference.**

- **Class:** `interface` (driver).
- **Observed** (AER-4072, comments 19592b57 and 7bedd544):
  - Pausing and unpausing take seconds to apply after launch. Session `bb8dad527936` failed with "sim did not unpause within 30 s" (AER-4072, comment add6a434).
  - The command-line start flips the *Use system time* preference, so each session snapshots and restores `Output/preferences`.
- **Relied on by:** nothing in fix-gateway.

### XPB-020

**LAT holds its value for seconds while the stream is alive.**

- **Class:** `interface`.
- **Observed** (AER-4217, comment ecdb3e90, from the 2026-10-28 and 10-29 bench recordings):
  - LAT changed in 3,595 of 3,603 0.5 s recorder intervals.
  - In the 4 longer gaps (4.5 to 65 s), LONG, attitude and speeds kept changing. The source does not say what the aircraft was doing in them. The committed 10-29 trace and bus log show no repeated LAT value (QA, AER-4375), so the gaps are in recordings that are not committed.
  - The plugin writes LAT on every inbound packet whether or not it changed, so these gaps cannot trip the 2 s staleness bound.
- **Relied on by:** the 2 s `LAT`/`LONG` tol (fix-gateway#73) and the engine's 2 s bound from the position write (fix-gateway#58). Both assume the plugin keeps writing unchanged values.

### XPB-021

**X-Plane's nav data provider changed on 2026-10-31.**

- **Class:** `interface` (nav data).
- **Observed** (AER-4219, comment 9fb5ad1e, 2026-10-31, written from Bill's account; AER-4344, comment 65257214):
  - Through 2026-10-30, sessions ran on X-Plane's default data: Navigraph (source Jeppesen), cycle 2406. That includes `cbbeafe57646`, the committed FP8 recording.
  - From 2026-10-31, they run on XPNavData (APPEED, source KEYVAN Aviation), cycle 2611 revision 1, installed in `Custom Data`. The driver prefers `Custom Data` and starts X-Plane fresh for each session, so every later session uses it.
  - Sessions before and after that date are on different providers, not only different cycles.
- **What it means for a disagreement.** A cross-tier disagreement at a single fix may come from how the provider processed the record, or from the watermarking the package says it may carry. It is not necessarily navigator behavior. Before such a disagreement is classified under any other entry:
  - compare X-Plane's behavior against the FAA CIFP coding of that fix or leg;
  - check which provider and cycle the session reports (`xplane_nav_cycle` in the session result).

  Where X-Plane's path differs from the FAA coding and the provider may have coded it differently, the FMS question is inconclusive until that is ruled out. QA reads KSSF that way (AER-4219, comment c3340e36).
- **Licence boundary.** The provider's data is licensed for personal flight simulation only. Its terms forbid copying it or using it as a source for another database, product or service.
  - This register, the fixtures and the issues may name the provider and cycle.
  - They must never quote or reproduce its records: no fix coordinates, no procedure legs, no file excerpts.
  - Recorded instrument outputs from a session (`truth.jsonl`, timeline, result) may be kept.
  - Where an entry needs a value, take it from the FAA CIFP, which is public domain.
- **Reference:** not applicable; this is the sim's data source.
- **Relied on by:**
  - `tests/sim/fixtures/README.md:10` (the FP8 recording, cycle 2406, default data).
  - Any recording committed from a session flown on or after 2026-10-31 must name its provider and cycle in that README.

### XPB-022

**On V27 from GVO to GOLET, X-Plane's course reads 1.4 deg below the engine's and the page's, and holds steady along the leg.**

- **Class:** `sim-quirk` (2026-11-09, XPB22U, AER-5150). It was `unknown` until then. XPB22T (2026-11-07) ruled out the data and the variation. XPB22U then showed that the planned turn angle at GVO decides it, whether or not the turn is flown. Every source that defines the leg after a fix makes it the leg between the two fixes; none moves it (Reference, below). Nothing in the engine or in a test allowance changes, so by the register's rule this reclassification is made in the PR, without a card.
- **Setup:** X-Plane 12, SR22, XPNavData cycle 2611 (XPB-021). Sessions `b90a1c257dd6` (2026-11-01 05:37Z) and `d419efeeea66` (07:44Z), slice 1 of the RS3 golden route (AER-4464). The test run was a hand run on the workstation, 2026-11-07 23:11Z, with Bill's session, run directory `20261107T231150Z-xpb22t-v27-gvo-golet`. It used slice 1's start, 3 nm before GVO inbound from KSBA, and ended once GOLET was sequenced. Hand runs get no session id.
- **Observed** (AER-4464, comment a4a33c53):
  - On the leg into GOLET, X-Plane's `gps_crs_mag` is 129.9 deg for the whole leg. The live engine's `FPLCRS` is 131.35 to 131.56, and the page's DTK (the published `FPLfCRS`) is 131.4. The difference is 1.4 deg, steady, over 1,079 rows.
  - The FAA CIFP 2611 positions give a great-circle course of 143.1 deg true for GVO-GOLET. The bench's FIX MAGVAR over that leg is -11.7 (11.7 E), so the course is 131.4 magnetic, which is what the engine and the page show.
  - Every other V27 leg flown agrees with X-Plane to 0.11 deg or better. That includes VTU-EXERT, which also starts at a VOR (session `9b6d523db79a`, `tests/sim/fixtures/sim2b_v27_xplane_2026-11-01.csv.gz`).
- **Discriminating test, as specified.**
  - (1) Compute the GVO-GOLET course from X-Plane's own installed data and compare it with the FAA CIFP's. Record only "agrees" or "differs by N deg", never the provider's coordinates (XPB-021, licence boundary).
  - (2) Compare X-Plane's magnetic variation dataref along the leg with the bench's MAGVAR.
  - If (1) differs by about 1.4 deg, it is the data. If (2) does, it is the variation model. If neither, it is navigator behavior and needs a reference.
- **Result** (2026-11-07, XPB22T):
  - (1) **Agrees.** Computed from `Custom Data`, the GVO-GOLET course differs from the FAA CIFP's by 0.000 deg, on a sphere and on WGS-84. GVO's and GOLET's positions also agree, and so does GVO's station declination. X-Plane's airway data carries the GVO-GOLET segment on V27. The positions in the FMS plan the driver loaded (`plan.fms`) agree with the FAA's at every V27 fix.
  - (2) **Agrees.** Along the leg, `sim/flightmodel/position/magnetic_variation` read -11.69 to -11.78 (east negative), 399 rows. The bench's live MAGVAR agreed to 0.0003 deg; it takes that dataref over RREF. That is the Beelink's `xplane.yaml` standing block since AER-4970, whose comment says it repeats the block the 2026-11-01 follower added, so the -11.7 that comparison used is X-Plane's own value.
  - The behavior reproduced: 129.94 deg from the waypoint switch on, rising to 130.14 by GOLET.
- **What the recordings show instead.** This is the same on 2026-11-01 (`b90a1c257dd6`) and on the test run.
  - X-Plane changes its course to 131.44 at turn start, 1.32 nm before GVO. That is the published leg with the local variation, and it is the engine's value.
  - At its waypoint switch, 0.75 nm past GVO in the turn, the course becomes 129.94.
  - From the switch to GOLET, X-Plane's CDI is centred (`gps_hdef_dot` -0.01) on a straight line that is not the published leg. On the test run, measured from the FAA GVO-GOLET line, the aircraft was 0.455 nm right 16 nm before GOLET, and the offset closed linearly to 0.017 nm at 0.6 nm. The line runs 1.45 deg left of the published leg and passes 0.48 nm right of GVO abeam. The aircraft's true track was 141.65, against the published 143.13 true.
  - X-Plane's course is that line's true course (141.73 to 141.84) with X-Plane's own variation. X-Plane's bearing to GOLET, corrected by the same dataref, matches the FAA bearing from the aircraft to 0.007 deg on average. So the variation and GOLET are consistent; only the leg is different.
  - The course change at GVO is 154 deg: 285.3 inbound from KSBA, 131.4 outbound. Every other V27 turn in the slices is 0 to 61 deg, and there X-Plane's course agrees with the published leg to 0.11 deg (above). At XPB-023's turns (61 to 90 deg), its CDI after the switch is the next leg's straight line.
- **Reference.** No source describes a leg that moves with the turn before it. The ones found (XPB22U, test c) all guide to the leg between the fixes. Where they treat a large turn specially, they change the turn, not the leg:
  - FMS-800, Army TM 1-1510-218-10 (C-12C/D) section 3C. The same text is in TM 1-1510-225-10, 3B. It anticipates "up to 30 nm prior to the waypoint, for course changes [up to] 150°. Over 150°, the waypoint will be overflown and a teardrop turn is made to intercept the next leg." Above 150 deg, "the FMS will provide guidance to turn and intercept the next course, resulting in an S-turn maneuver into the course." The online copy loses the comparison symbol; "up to" is a reading of it.
  - Apollo GX: "If the turn is greater than 120°, the Apollo GX will generate a step turn message prior to the waypoint."
  - The Garmin guides, ICAO Doc 9613 and the ACs, as quoted for the over-120 deg row in `makerplane/briefs/flight_plan_plan.md` (TURN2, AER-4760). They post a steep-turn alert or limit the fly-by transition to 120 deg. None says what the CDI references after the switch.

  The earlier question (which variation a navigator applies on a leg from a VOR) is answered by XPB22T: X-Plane applies its local variation, and that equals the bench's.
- **What is ruled out, and what is not.**
  - Ruled out: GVO's station declination (E016.0 would give near 127 deg). A general VOR-origin rule (VTU-EXERT agrees). The sphere/WGS-84 offset (XPB-002, about 0.1 deg). Provider coding (result 1). X-Plane's variation model (result 2).
  - Ruled out by XPB22U: the turn as flown (test a), where the aircraft was when the leg activated (test a), and the airport as the plan entry before GVO (test d).
  - Not shown: how X-Plane computes the line, and the turn angle at which it starts to move. Only 3, 4 and 154 deg were flown at GVO; turns of 0 to 90 deg elsewhere agree (above, and XPB-023). The line's side and size are what a turn started short of full anticipation would leave: a 154 deg left turn that starts about 1.3 nm before the fix rolls out right of the next leg. That fits, but it is not tested.
- **Discriminating tests, XPB22U** (2026-11-09 05:00-08:00Z, AER-5150). Three bench sessions, X-Plane 12, SR22, XPNavData 2611, profiles submitted inline. Cross-track is measured on a sphere from the FAA CIFP 2611 GVO-GOLET line (positive is right), and the line is fitted to the rows where X-Plane's CDI is centred (`|gps_hdef_dot|` < 0.05), more than 1 nm before GOLET.

  | Test | Session | Plan, and air start | Planned turn at GVO | X-Plane `gps_crs_mag` on GVO-GOLET | X-Plane's line at GVO abeam |
  |---|---|---|---|---|---|
  | slice 1 (XPB22T) | `b90a1c257dd6` and the XPB22T hand run | KSBA, GVO, V27; 3 nm before GVO, turn flown | 154 deg | 129.94 to 130.14 | 0.48 nm right, 1.45 deg left of the leg |
  | (a) | `90c66fb67b39` | KSBA, GVO, V27; 15 nm before GOLET (3.7 nm past GVO), no turn flown | 154 deg | 129.83 to 130.00 | 0.503 nm right, 1.52 deg left (1,579 rows, residual 0.018 nm) |
  | (b) | `eeb16a7fcbfe` | KSMX, AFOXY, GVO, V27; 6 nm before GVO, turn flown | 4 deg | 131.35 to 131.56 | 0.003 nm, 0.00 deg (2,457 rows, residual 0.001 nm) |
  | (d) | `cefebb18e984` | KSMX, GVO, V27; 15 nm before GOLET, as (a) | 3 deg | 131.39 to 131.55 | 0.003 nm, 0.00 deg |

  - (a) **No.** With no turn flown, X-Plane still reads 129.8 to 130.0, not 131.4, and its CDI is not centred on the published leg. At the air start the aircraft was 0.033 nm from the published line and the CDI read +0.93 dot (fly right). The autopilot then took it to the same offset line as slice 1, and the CDI centred there (-0.01 dot). So the offset does not come from the turn being flown.
  - (b) **Yes.** After a 4 deg turn at GVO, X-Plane's GVO-GOLET is the published leg: course 131.35 to 131.56, the engine's 131.4, and the aircraft is 0.003 nm off it. Its AFOXY-GVO leg is the published one too (131.08 to 131.14 against 131.19).
  - (d) was added to separate the two things (a) and (b) changed together: the planned turn, and the airport as the entry before GVO. With the airport kept and the turn removed, the offset is gone. So the cause is the turn angle in the plan.
  - In all three sessions the next leg, GOLET-KWANG, agrees with the published one: course 72.75 to 73.00 against 72.72 at a fixed 11.7 E, and 0.001 nm off it once established.
  - The profiles (start, route) are as in the table. Each one also sets `cifp: {cycle: "2611"}`, `alt_ft: 7000` (above the 6,000 and 6,400 ft MEAs) and `end: {after_fix: KWANG}`.
- **Relied on by:** nothing. No committed recording includes the leg. The V27 recording is HENER-DOYLE. A course check at the FP8 tolerance (2 deg) would pass it anyway. The engine flies the published leg from GVO, so nothing in the engine or in a test allowance changes.
- **For a future comparison.** Any recording whose plan turns more than 90 deg at a fix should have the next leg checked against the published line before X-Plane's course or CDI is compared there. The over-120 deg row of the guidance table (`flight_plan_plan.md`, TURN2) flies such a turn as a fly-over and measures from the published leg. X-Plane here anticipates, and measures from a moved line.

---

## Recorded elsewhere, not X-Plane behaviors

Listed so they are not re-filed as X-Plane behaviors:

- **"X-Plane's FMS replaced an en-route fix with the approach transition"** (AER-809 session 1). This was withdrawn: the route as planned was not flyable, and the pilot flew the published transition. "The fault was my plan, not X-Plane's FMS." (AER-809, comment 957202f4.)
- **The SR22 cannot hold 700 fpm at climb speed.** On session `33758e63080f` the autopilot held 700 fpm at 84-88 kt with the throttle at 1.00, and the airspeed bled to the 78 kt guard at about 628 s (AER-809, comment eaf91532). This is the flight model, not avionics. It belongs to the driver (SIM3).
- **Live weather.** X-Plane flies live weather, not the profile's calm. The wind was under 5 kt on `cbbeafe57646` (`tests/sim/fixtures/README.md`). This is the environment, not avionics.
