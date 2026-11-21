# gdl90_out bench capture (EFB1, AER-6142)

## `gdl90_out_xplane_2026-11-21.jsonl.gz`

**Recorded** on the Beelink bench on 2026-11-21 between 05:00:18 and 05:23:47 UTC, during
xplane-bench session `e493b3dc13e8` (profile `fp8-ksmx-rnav30-missed`, SR22): the KSMX RNAV
30 from 2 nm before PAMJI, the missed approach to WINCH, offsets on HANNU-WINCH, and five
minutes in the WINCH hold. The outcome was `complete`. The file is unedited apart from gzip.

What ran: this branch at `52677d9`, as a private fixgw from `~/bench-scratch/aer6142`
(`fixgw.server:main --config-file`, netfix on 127.0.0.1:3496). Its own `xplane` plugin read
position, attitude and speeds from X-Plane over RREF on udp_in 49011. It had no recv or send
rows, no `dataref_writes` and no autopilot, so it wrote nothing to the sim. `gdl90_out`
listened for discovery on 127.0.0.1:63193, took `geo_altitude_key: ALT` with
`geo_altitude_datum: msl` (X-Plane's elevation is height above MSL), and used the call sign
`MKRBENCH`. `capture_gdl90_out.py` (this directory) played the tablet:

    python3 capture_gdl90_out.py --discovery 127.0.0.1:63193 --netfix 127.0.0.1:3496 \
        --seconds 4200 --stop STOP --out cap.jsonl

The bench's live fixgw and pyEfis were not touched. The run was started and stopped by
`sim_follow.py` hooks. The config builder (`build_config.py`) and the hook (`session.sh`)
are on the Beelink beside the run.

What it holds: 1,410 s, 8,458 datagrams and 2,664 bus readings. For the first 82 s X-Plane
had not started, every key was unwritten, and GPS-valid was clear. Live flight followed until
X-Plane quit at 1,307 s, when GPS-valid cleared within the keys' 2 s tol. No frame failed its
FCS.

`test_bench_capture.py` checks each ownship and AHRS message against the bus readings around
it. On this file it compares 1,221 valid ownship reports and 179 invalid ones, and leaves out 8
that fall within 2 s of the two position-validity changes (the reasons are in the test).

Independent of the bus: each decoded ownship position was measured against X-Plane's own 5 Hz
truth track for the session (`sim-session.sh fetch e493b3dc13e8`; not committed, 2.2 MB). The
distances were a median of 0.5 m and a p95 of 1.7 m, against an encoding LSB of 2.4 m. The 8
reports more than 5 m off all come before truth logging starts: the air-start position, then
the sim holding still for 5 s before the driver released it.
