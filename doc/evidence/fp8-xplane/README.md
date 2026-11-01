# FP8: the engine against X-Plane's navigator

Two plots from the recorded X-Plane 12 flight of 2026-10-29 (KSMX RNAV (GPS)
RWY 30 with the missed approach), replayed headless through the flight-plan
engine. The inputs are both committed: the trace
`tests/sim/fixtures/fp8_xplane_2026-10-29.csv.gz` and the profile
`src/fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml`. See
`tests/sim/fixtures/README.md` for the trace's provenance.

- `fp8_engine_vs_xplane.png`: the engine's course and CDI minus X-Plane's, row
  by row, with the gate's tolerances (2 deg, 0.1 full scale). Grey spans are
  not compared, for the reasons given in
  `tests/sim/fixtures/fp8_xplane_2026-10-29.yaml`. Worst case on the compared
  rows: 0.061 deg and 0.010.
- `fp8_sequence_points.png`: distance before PAMJI, CIMSO and ANIBY (all three
  collinear in the CIFP) at which each navigator changed its active waypoint.
  X-Plane and the engine on the recorded flight; the synthetic reference
  navigator (`src/fixgw/sim/refnav.py`) on its own flight of the same profile.
  Resolution is one 5 Hz sample, about 0.006 nm at 110 kt.

The script applies the gate's skip rule with its own code, so its count of
compared rows (3783) differs slightly from the gate's (3778). The worst cases
are the same.

Regenerate, from the repo root (needs `matplotlib`):

```sh
pip install -e .[sim] matplotlib
PYTHONPATH=src:. python3 doc/evidence/fp8-xplane/plot_fp8_xplane.py
```

It prints the worst cases and the three sequence distances it plotted.
