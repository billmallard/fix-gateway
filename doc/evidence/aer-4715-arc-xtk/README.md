# AER-4715 on the bench: the CDI through the fly-by turn at HENER

The live Beelink bench (fix-gateway `aer-4715/arc-xtk` c676b39, staged with
`bench-deploy.sh --ref fix-gateway=aer-4715/arc-xtk --owner AVIONICS --issue
AER-4715`), fed the same X-Plane trajectory through the 61 deg fly-by at HENER
by the same replay as AER-4675 (`../aer-4675-bisector/README.md`: trace
t = 100-200 s of `tests/sim/fixtures/sim2b_v27_xplane_2026-11-01.csv.gz`, held
at the same three points). X-Plane was not running.

`hsi_before_after_hener.png`: the HSI at each point, top row from the
AER-4675 grabs (2026-11-02, cross-track from the next leg's line), bottom row
from this branch (cross-track from the turn's arc), 2x nearest-neighbour.
`hsi_fpl_through_hener.png` and `map_through_hener.png` are this branch's
strips, made as AER-4675's were.

What the engine published at each hold (`bench/events.log`; AER-4675's from
its own run on the bench):

| Point | Trace t | `FPLCDI` AER-4675 | `FPLCDI` AER-4715 |
|---|---:|---:|---:|
| before turn start | 160.1 | -0.001 | -0.001 |
| in the turn | 168.7 | -0.115 | -0.013 |
| past the bisector | 177.2 | -0.048 | -0.030 |

Full scale is 2 nm (ENR). The trajectory is X-Plane's, whose turn is wider
than the engine's standard-rate arc (XPB-001), so the arc cross-track is not
zero: it is how far X-Plane's path is from the engine's arc. The course
(`FPLCRS` 134 from turn start) and the sequence at the bisector are unchanged
from AER-4675. `bench/hener_bus.csv` is the bus at 2 Hz through the replay.

To regenerate, on the Beelink with the SIM2b rig in `~/bench-scratch/aer4464`:

    mkdir -p ~/bench-scratch/aer4715/out && cd ~/bench-scratch/aer4715
    cp ../aer4675/replay_hener.py ../aer4675/hener_rows.json .
    ~/pyefis-venv/bin/python replay_hener.py   # not inside flock: it takes the bench lock per grab

then, beside the two sets of grabs:

    python3 make_strips.py <AER-4715 shots dir>
    python3 make_before_after.py <AER-4675 shots dir> <AER-4715 shots dir>
