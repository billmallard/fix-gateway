# Scenario profiles (simulated flights, SYN1)

A scenario profile is one YAML file that says what to fly and what the run
must show. Two tiers read the same file:

- **Synthetic (SYN, this package).** `fixgw.sim` turns the profile plus
  CIFP into a FIX bus stream: a simple aircraft flies the procedure, and a
  reference navigator, written independently of the engine, publishes the
  truth (`REF*` keys).
- **X-Plane (SIM, a separate driver).** The driver flies the same profile in
  X-Plane with X-Plane's own FMS and autopilot. X-Plane's course and
  deviation (`EXTCRS`/`EXTCDI`) are the truth there.

So nothing in a profile is specific to either tier: no X-Plane datarefs or
aircraft files, no generator tuning. A profile describes the flight a pilot
would be asked to fly, the pilot's inputs, and the checks.

Profiles live in `src/fixgw/sim/profiles/`. The first one is
`fp8-ksmx-rnav30-missed.yaml` (FP8 session 2, AER-809).

## The rule for the reference

`REF*` keys come from `fixgw/sim/refnav.py`, which imports nothing from the
navigation engine (`fixgw.plugins.flightplan`) or `fixgw.geo`: WGS-84
geodesics from GeographicLib, its own leg sequencing, CDI scaling written
from DO-229. If the two sides shared code, a bug there would make both
wrong in the same way and the comparison would prove nothing.
`tests/sim/test_generator.py::test_the_synthetic_tier_imports_nothing_from_the_engine`
fails if an import creeps in.

What the tier still shares, and so cannot catch: the CIFP decoding
(`fixgw/sim/cifp.py`) feeds both the reference and the route block the
engine is loaded with. A misread record would be flown wrong by both. The
X-Plane tier, which reads X-Plane's own navigation data, is the check on
that.

## Format

```yaml
name: fp8-ksmx-rnav30-missed          # required; also the run's name in reports
description: free text                # optional

cifp:                                 # required
  cycle: "2611"                       # required: the AIRAC cycle, pinned
  file: cifp/FAACIFP18-2611-KSMX-R30  # relative to the profile; a full FAACIFP18 also works

aircraft:                             # optional, these are the defaults
  tas_kt: 110
  climb_fpm: 700
  descent_fpm: 600
  bank_max_deg: 25
  turn_rate_deg_s: 3.0

environment:
  magvar_deg: -11.9                   # required: FIX MAGVAR, positive WEST (MAG = TRUE + MAGVAR)
  wind: {from_deg: 0, kt: 0}          # optional, steady wind; default calm

start: {fix: PAMJI, before_nm: 2.0, alt_ft: 2500, heading: on_course}

route:                                # what the box is loaded with
  - procedure: {airport: KSMX, approach: R30, transition: GVO, missed: true}

pilot:
  missed_after_map_s: 5               # go missed (RESUME) this long after the MAP

events:
  - id: right
    at: {leg: HANNU-WINCH, after_s: 5}
    do: {offset_nm: 1.0, hold_s: 30}
  - id: left
    at: {after: right}
    do: {offset_nm: -1.0, hold_s: 30}
  - id: hold
    at: {hold_at: WINCH}
    do: {hold_min: 5}

end: {after_event: hold, max_s: 3600}

checks:
  - {key_pair: [FPLCRS, REFCRS], tol_deg: 0.5, except_s_around_sequence: 5}
  - {key_pair: [FPLCDI, REFCDI], tol: 0.05, same_sign: true, except_s_around_sequence: 5}
  - {key_pair: [WPDIS, REFDIS], tol: 0.015, tol_rel: 0.005, skip_when: {REFWPT: ""}}
  - {sequence: [PAMJI, CIMSO, ANIBY, RW30, HANNU, WINCH], keys: [WPNAME, REFWPT]}
```

Unknown keys anywhere are an error, so a typo never silently means "not
set".

### `cifp`

`cycle` is required and must match the file's `HDR01` cycle; the generator
refuses a mismatch. Pinning keeps a fixture from drifting when the cycle
rolls. A trimmed excerpt (the header record plus the records the procedure
needs) can be committed beside the profile, as `profiles/cifp/` does; a
full `FAACIFP18` can be passed instead with `--cifp`.

### `start`

The aircraft starts on the leg into `fix`, `before_nm` short of it, at
`alt_ft`, on course. The box is loaded with the route and the pilot
activates that leg (`FPLCMD ACT <slot>`). `heading` only accepts
`on_course` so far.

### `route`

One `procedure` item so far: `airport`, `approach` (the CIFP procedure
ident, `R30`), `transition` (`GVO`), and `missed` (fly the missed approach;
when false the run ends at the MAP). The reference flies the transition,
the final and the missed approach as coded. The box gets the same legs as
an editor would publish them (`fixgw/sim/fplroute.py` lists the encoding
choices, since assembling a procedure into slots is not specified yet).

Leg types the reference flies: `IF TF DF CA HM`. Anything else refuses the
whole profile rather than flying a guess.

### `pilot`

What the pilot does that the scenario does not spell out as an event.
`missed_after_map_s`: the box suspends at the MAP; this long after crossing
it the pilot goes missed (`FPLCMD RESUME` in tier SYN, the missed-approach
command in tier SIM).

### `events`

Each event has an `id`, one trigger under `at`, and one action under `do`.

| `at` | Fires when |
|---|---|
| `leg: A-B, after_s: n` | `n` seconds after the reference starts the leg from fix A to fix B |
| `after: <id>` | the named (earlier) event has finished |
| `hold_at: FIX` | the reference starts flying the coded hold at FIX |

| `do` | Means | Finishes when |
|---|---|---|
| `offset_nm: d` (+ `hold_s: n`) | fly `d` nm off the desired track, positive right | established (within 0.05 nm) and held `n` s. The offset stays until another event changes it |
| `hold_min: n` | stay in the hold | `n` minutes after it fired |

### `end`

`after_event`: the run stops when that event finishes. `max_s` (default
3600): a run still going then is an error naming the events that never
finished, never a silent short run.

### `checks`

Tier-independent comparisons on the recorded bus. Each is one of:

- `key_pair: [ENGINE_KEY, REFERENCE_KEY]` with `tol_deg` (angles, wrapped),
  or `tol` (absolute) plus optional `tol_rel` (fraction of the reference
  value), or neither (values must be equal, e.g. idents). `same_sign: true`
  also fails a row where both values are beyond `tol` and of opposite sign.
  `except_s_around_sequence: s` skips `s` seconds either side of every
  change of the sequence keys (the TO fix, on either side), so a turn's
  transition is not judged. `skip_when: {KEY: value}` skips rows where a key
  has that value. `after_s` (default 1) skips the start.
- `sequence: [FIX, ...]` with `keys` (default `[WPNAME, REFWPT]`): each key's
  run of TO fixes, blanks dropped, must equal the list. The report also gives
  the time difference at which each fix became the TO fix.

## Reference keys (`database/sim.yaml`)

| Key | Meaning |
|---|---|
| `REFCRS` | desired track, magnetic, deg |
| `REFXTK` | cross-track, nm, positive right of track |
| `REFCDI` | `-REFXTK / full scale`, clamped to +-1 (the `FPLCDI` convention) |
| `REFDIS` | distance to the TO fix, nm (0 on a leg with no fix) |
| `REFWPT` | TO fix ident (`""` on a `CA` leg) |
| `REFPT` | the reference's active leg type (`TF`, `CA`, `HM`...) |

How the reference flies, briefly (details in `refnav.py`): fly-by fixes
sequence at the start of the turn (`R tan(delta/2)`); fly-over fixes and
the MAP abeam; the MAP suspends until the pilot goes missed; a `CA` ends on
altitude; an `HM` is the published racetrack with a standard entry (direct,
teardrop or parallel by the AIM sectors). CDI full scale 2.0 nm en route,
1.0 nm within 30 nm of the airport, a linear ramp to 0.3 nm over the 2 nm
before the FAF, 0.3 nm to the MAP and until the missed approach is
initiated, then 1.0 nm.

## Running it

```
python -m fixgw.sim generate src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml -o run.json --timeline run.timeline.json
python -m fixgw.sim check src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml <recorded files...>
```

`generate` writes the `data_recorder` format (a `{"frequency", "starttime"}`
header, then one row per tick), which `data_playback` replays unchanged.
To run it through a gateway, enable `data_playback` with `files:` naming
the output, `flightplan`, and `data_recorder` (the engine's and the `REF*`
keys), then `check` the recorded files. `gateway-replay.yaml` beside this
file is the exact config the first bench replay used, with the commands.

## Live mode (SYN2)

`live` flies the same profile into a gateway that is already running, over
netfix, so the display on a bench moves as if the airplane were flying it.
No simulator, no `data_playback`:

```
python -m fixgw.sim live src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml \
    --host 127.0.0.1 --port 3490 --speed 1 --log run.marks.jsonl \
    --hook 'my-screenshot.sh'
```

What it does, in order:

1. Connects, then **watches `LAT`/`LONG` for `--watch-s` (3 s)**. If either
   moves while it is not writing, another source (X-Plane, a GPS, a demo,
   a forgotten writer) is publishing a position. It refuses with exit `3`
   unless `--allow-other-writer`: two writers on one key is a flight nobody
   flew.
2. Reads `FPLSEQ` and `FPLCMDACK`. The run commits its route as `FPLSEQ`
   one past the gateway's, and numbers its commands after the last ack, so
   a box that has already loaded routes and taken commands sees a new route
   and new commands, not a repeat.
3. Names every key the run writes that the gateway's database does not
   have. netfix drops a write to an unknown key silently; this says so
   first (a gateway without `database/sim.yaml` drops the `REF*` keys, which
   nothing on the glass reads).
4. Writes the rows at the profile's times divided by `--speed`. The
   schedule is absolute, so a slow write is caught up, not accumulated;
   the closing line reports the worst lateness of any row.
5. At each timeline mark (`start`, each `sequence`, `map`, `pilot`, each
   `event`/`established`/`done`, `end`) it waits `--hook-delay-s` (3 s, for
   the glass to draw the change), reads `--readback` keys from the gateway
   (default `WPNAME,FPLCRS,FPLCDI,FPLMSG`: what the engine is doing at that
   moment), runs `--hook` if the mark is one of `--hook-on` (default
   `start,sequence,map,end`), and prints and logs one JSON line. The hook
   is a shell command with `SYN_INDEX` (01, 02, ...), `SYN_WHAT`, `SYN_FIX`,
   `SYN_T`, `SYN_WALL_S` and `SYN_PROFILE` in its environment. A hook that
   fails is counted and makes the exit `1`; it never stops the flight.

Exit codes: `0` flown, `1` flown but a hook failed, `2` no gateway at
`--host:--port`, `3` another writer, `130` stopped (Ctrl-C or `SIGTERM`
end the run between rows).

What it leaves behind: the run's route stays loaded in the box and the last
position stays on the bus until it goes old (each key's `tol`). It changes
the box's flight plan; on a shared bench that is the point, so say so on
the issue.

**`--speed` above 1 is for looking, not for checks.** The engine runs on
the wall clock (its update rate, alert timing, turn anticipation from
`GS`), so at x4 the airplane covers ground four times faster than its own
`GS` says. Sequencing still happens, a little late. Run `check` on a
recording at x1 only; above that a disagreement is the time warp, not the
engine. Writes scale with speed too (about 19 keys per row, 5 rows per
second at x1).

The run is one netfix client and writes nothing else, so it can be pointed
at another box's gateway across the LAN (`--host`). It needs `geographiclib`
and `yaml` where it runs, not on the box it drives.

### On a bench

Hold the bench lock for the whole run (`processes/bench_deploy.md`): a
live run is twenty minutes of a moving display, and a deploy restarting
`fixgw` in the middle would end it.

```
flock -o -w 900 /tmp/pyefis-bench.lock timeout 45m \
    python -m fixgw.sim live <profile> --log ~/syn2/marks.jsonl \
        --hook 'DISPLAY=:0 ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 \
                -video_size 1280x800 -i :0 -frames:v 1 ~/syn2/$SYN_INDEX-$SYN_WHAT-$SYN_FIX.png'
```

`x11grab`, not pyEfis's SIGUSR1 screenshot: GL does not render into the
SIGUSR1 grab, so its SVS panel is always blank. The Pi 5 has
no screenshot of any kind (eglfs); drive it the same way with `--host`,
and its proof is the readback log and someone looking at the glass.

## What changed from the plan's sketch (`briefs/sim_bench_plan.md` section 3)

- `cifp_cycle` became `cifp: {cycle, file}`: the cycle is checked against
  the file, and the file is named so a run is reproducible.
- `environment.magvar_deg` is new and required: both navigators read the
  same `MAGVAR`, and a coded `CA`/`HM` course is magnetic.
- `start.before_nm`: starting exactly at PAMJI would skip PAMJI's own
  sequence, which the checks list.
- `missed: true` moved into the procedure, and the pilot's RESUME timing
  into `pilot.missed_after_map_s`. A profile still says "go missed", not how
  either tier does it.
- Events got ids, `at: {after: id}` and `hold_s` (time established at the
  offset): the sketch's fixed `after_s: 10` / `after_s: 50` would have
  started the second offset before the first was established. `hold_min`
  fires on `hold_at` rather than on reaching the fix, because the coded
  hold starts at the fix.
- Checks gained `tol_rel`, `skip_when`, `keys` and equality for idents, and
  `FPLXTK`/`REFXTK` and `WPDIS`/`REFDIS` pairs. `REFXTK` and `REFPT` are
  added to the four `REF*` keys: cross-track separates a geometry
  disagreement from a scaling one, and `REFPT` tells the hold apart from the
  leg into the same fix in the per-leg report.
