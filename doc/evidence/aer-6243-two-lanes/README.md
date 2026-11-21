# AER-6243: two lanes on the Beelink, lane 1 killed five ways

MAOS brief `briefs/redundant_lanes.md` section 8, slice 1. Two private lanes on the
Beelink simulator bench, each a gateway from this branch's `two_lane_<n>.yaml` plus its
own pyEfis on Xvfb, joined by a pty CAN-FIX bus (`bench/hub.py`). The bench's own
gateway and kiosk are not touched: the lanes use netfix 3497 and 3498, X-Plane sockets
49021 and 49022, and displays :7 and :8.

The brief names the bench's own gateway as lane 1. These runs use a private lane 1
instead, because killing the live gateway five ways would take the shared bench's glass
away from every other agent for the run, and the bench's gateway does not run quorum or
canfix. The two private lanes are the same code and configuration the brief describes.

Lane 1 is the PFD and leads in normal flight (quorum node id 2). The five faults, each
held 20 s and then recovered:

| Mode | Fault | Recovery |
|---|---|---|
| `gw_kill` | SIGKILL lane 1's gateway | start it again |
| `gw_stop` | SIGSTOP lane 1's gateway (a hang) | SIGCONT |
| `efis_kill` | SIGKILL lane 1's pyEfis | start it again |
| `efis_stop` | SIGSTOP lane 1's pyEfis (a frozen display) | SIGCONT |
| `can_cut` | the hub stops forwarding frames | forward again |

**Pass (brief section 8):** lane 2's PITCH, ROLL, IAS and ALT stay within their
time-to-live and valid for the whole run, and lane 2 leads within 1.5 s of any lane-1
gateway death. `bench/recorder.py` subscribes to both lanes; the gateway sends a
subscribed key on every write and on its expiry, so each line's arrival time is that
key's timestamp. `bench/analyze.py` judges the run from `events.csv`.

## Runs

- `dry-run/`, 2026-11-21 22:00 to 22:07 UTC. X-Plane off (outside the sim bench's flying
  window), so a synthetic feeder writes PITCH, ROLL, IAS and ALT into each lane at 10 Hz
  (`lanes.sh feed`). This proves the harness and the gateway side. It does not prove the
  X-Plane feed path. `summary.md` is the verdict.
- `xplane/`: the same run with each lane fed by X-Plane over RREF, sim bench session
  `eb3686e0dac3`. Added when it has flown.

Each `sheet_<mode>.png` is four 1920x1080 frames at half size: top left lane 2 before
the fault, top right lane 2 during it (10 s in), bottom left lane 2 after recovery
(25 s), bottom right lane 1 during it. The SVS is off in both lanes (two GL panes on one
box load the bench for nothing), so the attitude indicator draws its plain sky. In the
dry run the sky is grey and the VSI shows a red X because only four keys are fed.

## Reproduce

On the Beelink, as `pyefis`:

```sh
mkdir -p ~/bench-scratch/aer6243/h   # copy bench/* into h/
sh h/lanes.sh setup <fix-gateway sha> <pyEfis sha>   # 2ded770, ea09204f for these runs
sh h/lanes.sh start
sh h/lanes.sh feed            # only when X-Plane is not flying
sh h/lanes.sh run             # about 7 minutes
~/pyefis-venv/bin/python h/analyze.py ~/bench-scratch/aer6243/run
sh h/lanes.sh stop
```

For the X-Plane run, `sim_follow.py` (maos-workspace) calls `night.sh start` when the
session flies: it waits for 10 s of fresh PITCH on lane 2, lets the flight settle 60 s,
then runs, judges and stops. The sheets come from
`ffmpeg -i before -i during -i after -i lane1_during -filter_complex "xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0,scale=1920:-1"`.
