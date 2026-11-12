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

`soak.jsonl` is copied here when the run ends, and the results go below.

## Results

Pending: the run is in progress.
