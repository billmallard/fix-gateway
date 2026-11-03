# AER-4675 on the bench: the fly-by turn at HENER

The live Beelink bench (pyEfis `dev` 228a879, fix-gateway
`aer-4675/bisector-sequencing` 675122d, staged with `bench-deploy.sh --ref
fix-gateway=aer-4675/bisector-sequencing`), fed X-Plane's recorded trajectory
through the 61 deg fly-by at HENER on V27: trace t = 100-200 s of
`tests/sim/fixtures/sim2b_v27_xplane_2026-11-01.csv.gz`, at its recorded 5 Hz,
with the golden route KSBA GVO V27 AVOLS KSMX published through pyEfis's
FixBridge and `ACT 6`, the leg DEANO -> HENER. X-Plane was not running: the
trajectory is X-Plane's, while the engine and the glass are live.

The replay holds the position at three points and grabs the whole screen
(x11grab) with the Flight Plan tab and then the Map tab showing:

| Point | Trace t | What the engine published (bus, `bench/events.log`) |
|---|---:|---|
| before turn start | 160.1 | `FPLCRS` 073, `WPNAME` HENER, `WPDIS` 0.53, TO, `FPLALERT` on, FROM anchor DEANO |
| in the turn | 168.7 | `FPLCRS` 134 (the HENER -> VTU leg), `WPNAME` HENER, `WPDIS` 0.24, TO, `FPLALERT` off, FROM anchor DEANO |
| past the bisector | 177.2 | `FPLCRS` 134, `WPNAME` VTU, `WPDIS` 15.5, FROM anchor HENER, `FPLMSG` "SEQ HENER -> VTU" |

`hsi_fpl_through_hener.png`: the HSI and the top of the Flight Plan page at
each point. `map_through_hener.png`: the moving map around HENER at each point.
The compass card reads 000 because the replay writes no `HEAD`; that is the
replay, not the change.

`bench/hener_bus.csv` is the bus at 2 Hz through the replay. The course turned
to 134 and `FPLALERT` dropped in the same row, 0.372 nm before HENER (the
headless replay of the same trace: 0.379 nm). The sequence came 24 s later on
the wall clock, which includes the 14 s hold in the turn; on the trace's own
clock it is 9.7 s (`tests/sim`, headless).

To regenerate, on the Beelink with the SIM2b rig in `~/bench-scratch/aer4464`
(`v27_follow.py`, `nfx.py`, `fp/`):

    mkdir -p ~/bench-scratch/aer4675/out && cd ~/bench-scratch/aer4675
    # hener_rows.json: [t, lat, lon, alt_ft, gs_kt, trk, xp_wpt, xp_crs] for
    # 100 <= t <= 200 from the fixture csv above
    ~/pyefis-venv/bin/python replay_hener.py     # bench/replay_hener.py here

then, beside the grabs in `out/shots`:

    python3 make_strips.py <path to out/shots>
