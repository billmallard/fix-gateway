# TURN1 (AER-4759) on the bench: the CDI through the fly-by turn at HENER

The live Beelink bench (fix-gateway `aer-4759/turn-radius-on-124` 3b9982b,
staged with `bench-deploy.sh --ref fix-gateway=aer-4759/turn-radius-on-124
--owner AVIONICS --issue AER-4759`, pyEfis left on its own staging), fed the
same X-Plane trajectory through the 61 deg fly-by at HENER by the same replay
as AER-4715 (`../aer-4715-arc-xtk/README.md`), held at the same three points.
X-Plane was not running.

`hsi_before_after_hener.png`: the HSI at each point, top row from the
AER-4715 grabs (the arc at standard rate), bottom row from this branch (the
arc at a 15 deg bank), 2x nearest-neighbour. `hsi_fpl_through_hener.png` and
`map_through_hener.png` are this branch's strips.

What the engine published at each hold (`bench/events.log`):

| Point | Trace t | `FPLCDI` AER-4675 | `FPLCDI` AER-4715 | `FPLCDI` TURN1 |
|---|---:|---:|---:|---:|
| before turn start | 160.1 | -0.001 | -0.001 | -0.001 |
| in the turn | 168.7 | -0.115 | -0.013 | -0.004 |
| past the bisector | 177.2 | -0.048 | -0.030 | -0.020 |

Full scale is 2 nm (ENR). The difference from AER-4715 is under 0.01 of
full scale, which does not show at this scale; the table is the evidence,
the strip shows nothing else moved. The trajectory is X-Plane's, so the CDI
is how far X-Plane's flown path is from the engine's arc. Turn start moves
from 0.379 to 0.457 nm before HENER (trace t 164.1 to 161.8), still after the
first hold. `bench/hener_bus.csv` is the bus at 2 Hz, with `GPSCRS`,
`COURSE` and `CDI` added to the logged keys.

These grabs are from the second of two runs. In the first, the in-turn HSI
grab showed CRS 073 while `FPLCRS` had read 134.2 on the bus for 8 s; the
second run logged `GPSCRS` and `COURSE` beside it, all three switched
together, and its grab shows 134. The first run was the first replay after
the bench restarted pyEfis for the staging. That is a display-side lag,
reported on AER-4759, not an engine output.

To regenerate, on the Beelink with the SIM2b rig in `~/bench-scratch/aer4464`:

    mkdir -p ~/bench-scratch/aer4759/out && cd ~/bench-scratch/aer4759
    cp ../aer4715/replay_hener.py ../aer4715/hener_rows.json .
    ~/pyefis-venv/bin/python replay_hener.py   # not inside flock: it takes the bench lock per grab

then, beside the two sets of grabs:

    python3 make_strips.py <TURN1 shots dir>
    python3 make_before_after.py <AER-4715 shots dir> <TURN1 shots dir>
