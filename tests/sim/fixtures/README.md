# Recorded flights

## `fp8_xplane_2026-10-29.csv.gz`

FP8 session 2 (AER-809, billmallard/pyEfis#190): X-Plane 12 on the pilotless
sim bench, session `cbbeafe57646`, 2026-10-29 05:32Z, profile
`fp8-ksmx-rnav30-missed` (`src/fixgw/sim/profiles/`), aircraft SR22, outcome
`complete`. The KSMX RNAV (GPS) RWY 30 from 2 nm before PAMJI, the missed
approach, +1 / -1 nm offsets on HANNU-WINCH, five minutes in the WINCH hold.
X-Plane flew its own nav data (cycle 2406) and live weather (wind under 5 kt).
That was X-Plane's default data provider; sessions from 2026-10-31 use another
(XPB-021 in `src/fixgw/sim/XPLANE_BEHAVIORS.md`), so name the provider and cycle
for every recording added here.

Columns, 5 Hz, X-Plane's own state (the bench's `truth.jsonl`):
`t` (s), `lat`, `lon`, `alt_ft`, `gs_kt`, `trk` (true), and X-Plane's GPS
navigator: `xp_wpt` (active waypoint), `xp_crs` (deg mag), `xp_hdef_dot`
(2.5 dots = full scale), `xp_fromto` (1 TO, 2 FROM).

Regenerate (sim bench contract: `makerplane/processes/sim_bench.md`):

```sh
curl -s -H "Authorization: Bearer $XPBENCH_TOKEN" \
  http://10.110.10.167:8770/sessions/cbbeafe57646/truth > truth.jsonl
python3 - <<'PY'
import csv, gzip, json
T = [json.loads(l) for l in open("truth.jsonl")]
with gzip.open("fp8_xplane_2026-10-29.csv.gz", "wt", newline="") as f:
    w = csv.writer(f)
    w.writerow(["t", "lat", "lon", "alt_ft", "gs_kt", "trk", "xp_wpt", "xp_crs", "xp_hdef_dot", "xp_fromto"])
    for r in T:
        w.writerow(["%.2f" % r["t"], "%.7f" % r["lat"], "%.7f" % r["lon"], "%.1f" % r["alt_ft"],
                    "%.1f" % r["gs_kt"], "%.2f" % r["trk"], r["gps_id"], "%.3f" % r["gps_crs_mag"],
                    "%.4f" % r["gps_hdef_dot"], r["gps_fromto"]])
PY
```

Replayed by `tests/sim/test_fp8_xplane_replay.py` through `tests/sim/xplane_replay.py`,
and by the simulated-flight gate (the "Simulated flights" CI job) through
`fp8_xplane_2026-10-29.yaml`. That file names the trace and the profile and
carries the X-Plane tier's checks: `FPLCRS` against `EXTCRS` and `FPLCDI`
against `EXTCDI`, from the FP8 brief's acceptance, with each comparison skip
and its reason in the file.

## `fp8_bench_bus_2026-10-29.csv.gz`

The recorder output for the same flight: the Beelink gateway's bus at 1 Hz,
logged by `fp8_logger2.py` (AER-809 bench scratch) during session 2. The live
engine (fix-gateway 2666bc4) is in `FPL*`/`WP*`, and X-Plane's navigator as the
bench's `xplane` plugin wrote it is in `EXTCRS`/`EXTCDI`. `t` is the Beelink's
epoch. Trace time is `t - 1793251953.66` (`BUS_EPOCH` in `xplane_replay.py`),
found by position (0.0015 nm median, 0.006 nm worst). The rows are trimmed to
the trace's span, 1229 rows, with the columns as logged. `EXTTF` is 0
throughout: the bench did not map it until fix-gateway#41, after this flight.

`tests/sim/test_fp8_bench_bus.py` uses it to show that the trace's navigator
columns are the bus keys. On the aligned rows the bus `EXTCRS` matches
`gps_crs_mag` to 0.0006 deg, and `EXTCDI` matches 0.4 x `gps_hdef_dot` to
0.005. `test_fp8_xplane_replay.py` reads the live engine's sequence points
from it.

Regenerate (the raw log stays on the Beelink):

```sh
scp pyefis@10.110.10.241:bench-scratch/aer809/fp8_s2.csv .
python3 - <<'PY'
import csv, gzip, io
OFF = 1793251953.66
rows = list(csv.reader(open("fp8_s2.csv")))
keep = [r for r in rows[1:] if 55.69 + OFF <= float(r[0]) <= 1283.79 + OFF]
s = io.StringIO()
w = csv.writer(s, lineterminator="\n")
w.writerow(rows[0])
w.writerows(keep)
with gzip.GzipFile("fp8_bench_bus_2026-10-29.csv.gz", "wb", mtime=0) as g:
    g.write(s.getvalue().encode())
PY
```

## `sim2b_v27_xplane_2026-11-01.csv.gz`

The first recorded flight on an airway: V27 from 3 nm before HENER through
VTU and EXERT to DOYLE, slice 2 of the RS3 golden route KSBA GVO V27 AVOLS
KSMX (SIM2b, AER-4464, billmallard/pyEfis#324). Pilotless sim bench session
`9b6d523db79a`, 2026-11-01 10:32Z, X-Plane 12, SR22, 7,000 ft, outcome
`complete`. X-Plane flew its own nav data: the session reports cycle 2611,
which from 2026-10-31 is XPNavData in `Custom Data` (XPB-021); the route
block is from the FAA CIFP 2611 pack. The profile was submitted inline (an en-route profile is X-Plane
tier only until fix-gateway's loader reads en-route items, SIM3b):

```yaml
name: sim2b-v27-s2-hener-doyle
departure: KSBA
destination: KSMX
cifp: {cycle: "2611"}
aircraft: {tas_kt: 110}
start: {fix: HENER, before_nm: 3.0, alt_ft: 7000, heading: on_course}
route:
  - waypoint: GVO
  - airway: {id: V27, exit: AVOLS}
end: {after_fix: DOYLE, max_s: 2100}
```

Same columns as the FP8 trace. Regenerated the same way, from session
`9b6d523db79a`, then trimmed at `t < 1442.8`: the row after it is X-Plane
switching to PAROL 0.2 nm before DOYLE (XPB-001) as the session ends, which
the engine, on the same position, has not yet done.

## `v27_route_block.json`

The route block the bench loaded for that flight: KSBA, V27 GVO..AVOLS as
one airway segment (slots 2-12), KSMX, written by pyEfis's own
`FixBridge.publish` (pyEfis dev `79b9e5c`, the bench's AIRAC 2611
procedures pack) into a dict instead of the bus, with MAGVAR -11.45. A
recording names it with `route_block:` and the activated slot with `act:`
instead of a `profile:`; `xplane_replay.fly_route` flies it with no pilot
commands. Regenerate on the Beelink with the AER-4464 bench follower's
`publish_golden` against a recording client (agent scratch
`scratch/sim2b/v27_follow.py`; the commands are on AER-4464).

`sim2b_v27_xplane_2026-11-01.yaml` carries the X-Plane tier's checks,
`FPLCRS`/`EXTCRS` within 2 deg and `FPLCDI`/`EXTCDI` within 0.1 of the same
sign, plus the sequence HENER VTU EXERT DOYLE. Measured: course within 0.3
deg and CDI within 0.011 outside 5 s of each sequence. Moving VTU 1.8 nm in
the route block fails both pair checks; renaming EXERT fails the sequence.
