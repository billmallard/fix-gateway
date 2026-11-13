# DEMO2 soak: the demo source loops unattended for 24 h (AER-5461)

The gateway ran from the shipped config (`DEMO`, `COMPUTE`, `FLIGHTPLAN`,
`ANNUNCIATE`, `NETFIX`, `SYSTEM`, `STATE_PERSIST` on) on the Beelink bench
for 24 h, under `soak.py`, which logs the demo's status, the process's RSS
and threads, the flight-plan engine's state and any key with two writers
every 5 minutes.

It ran as a second gateway beside the bench's own: its config is a copy of
`src/fixgw/config` with netfix moved to port 3491, run from a scratch clone
of this branch. The bench's live fixgw, pyEfis and their checkouts were not
touched, and nothing reads the soak gateway. This soak covers the gateway
and the demo loop only. The kiosk display soak is DEMO6.

## Commands

On the Beelink (`pyefis@10.110.10.241`), with the bench venv, which has
`geographiclib` 2.1:

```
D=~/bench-scratch/aer-5461
git clone -q https://github.com/billmallard/fix-gateway.git $D/fix-gateway
git -C $D/fix-gateway checkout -q <sha>
cp -r $D/fix-gateway/src/fixgw/config $D/config
sed -i 's/port: 3490/port: 3491/' $D/config/connections/netfix.yaml
systemd-run --user --unit=aer5461-demo-soak --working-directory=$D \
    --setenv=PYTHONPATH=$D/fix-gateway/src \
    /home/pyefis/pyefis-venv/bin/python $D/fix-gateway/doc/evidence/aer-5461-demo-soak/soak.py \
    $D/config/default.yaml 24.1 300 $D/soak.jsonl
```

The run that counts was at 1e5020d, the merged head with DEMO7 traffic. It
started 2026-11-12 21:28Z and logged to `soak.jsonl`, copied here. An earlier
run at 0cab387, before the merge and without traffic, was stopped after 7 h
so the 24 h would cover the code that ships. Its log is
`soak-0cab387-partial.jsonl`.

## Results

At 1e5020d, from 2026-11-12 21:28Z to 2026-11-13 21:34Z (24.1 h, 290
samples). The unit exited on its own, with result `success`.

| What | Result |
|---|---|
| Loops completed | 73, counted up with no gaps |
| Demo state | `flying` and alive in every sample: it never refused or stopped |
| Route accepted by the engine | `FPLSEQ` equal to `FPLLDSEQ` in all 290 samples, reaching 147. `FPLCMDACK` reached 148 |
| RSS | 38,372 to 38,420 kB over the whole day. The last 12 samples (1 h) were all 38,420 |
| Threads | 8 in every sample |
| Keys with two writers | none in any sample |
| `LAT` flagged bad at a sample | 0. The 5 s restart hold fell between samples |
| Journal (`CRITICAL`, `ERROR`, `WARNING`, `Traceback`) | 1 line: `flightplan: could not write flightplan_state.json`, the pre-existing persist race (fix-gateway #169, BACKLOG G85), not the demo |

Not sampled: `soak.py` logs no TFC keys, so this run shows the traffic was up
only at the start (`TFCCOUNT` 3 and `TFC1CS` `DEMO1` read over netfix at
21:28Z), not that it lasted all 24 h.

The pre-merge run (0cab387, 7 h, 21 loops) showed the same: counters in step,
8 threads, RSS about 39 MB, and 6 lines of the same flightplan race.
