# Recorded flights

## `fp8_xplane_2026-10-29.csv.gz`

FP8 session 2 (AER-809, billmallard/pyEfis#190): X-Plane 12 on the pilotless
sim bench, session `cbbeafe57646`, 2026-10-29 05:32Z, profile
`fp8-ksmx-rnav30-missed` (`src/fixgw/sim/profiles/`), aircraft SR22, outcome
`complete`. The KSMX RNAV (GPS) RWY 30 from 2 nm before PAMJI, the missed
approach, +1 / -1 nm offsets on HANNU-WINCH, five minutes in the WINCH hold.
X-Plane flew its own nav data (cycle 2406) and live weather (wind under 5 kt).

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

Replayed by `tests/sim/test_fp8_xplane_replay.py` through `tests/sim/xplane_replay.py`.

## `fp8_bench_bus_2026-10-29.csv.gz`

The recorder output for the same flight: the Beelink gateway's bus at 1 Hz,
logged by `fp8_logger2.py` (AER-809 bench scratch) during session 2. The live
engine (fix-gateway 2666bc4) is in `FPL*`/`WP*`, and X-Plane's navigator as the
bench's `xplane` plugin wrote it is in `EXTCRS`/`EXTCDI`. `t` is the Beelink's
epoch. Trace time is `t - 1793251953.66` (`BUS_EPOCH` in `xplane_replay.py`),
found by position (0.0015 nm median, 0.006 nm worst). The rows are trimmed to
the trace's span, 1229 rows, with the columns as logged. `EXTTF` is 0
throughout: the bench does not map it.

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
