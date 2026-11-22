# AER-6268: the heading bug walk on the sim bench

The question: when the bug is changed on the bus, which way does X-Plane's
autopilot turn? On dev, a +40 change was flown 320 deg the long way round
(XPB-018, AER-6052).

Two sim-bench sessions were flown on 2026-11-22 with a C172 G1000 on the AFC2
route (`rig/profile.yaml`). For each one, a private fixgw on this branch ran
on the Beelink (`udp_in` 49104, netfix 127.0.0.1:3594). It reads X-Plane over
its own RREF subscriptions, with the autopilot adapter on. `rig/seq.py` waited
for the driver to couple GPS, set the bug to the heading, and pressed HDG.
Then it set the bug +40.2, -40 and +150 from the heading, about 45 s apart,
and pressed DISC at the end. The DISC trips the driver's servo guard, which
is why both sessions end `aborted: autopilot servos dropped`. Nothing touched
the live gateway, `pyefis.service` or its config.

| Run | Session | fixgw | The walk |
|---|---|---|---|
| `run1` | `2786fb6ef13a`, 06:39Z | 4daf49e | bursts of up to 10 steps, the next burst when the dial shows the last or after 0.5 s |
| `run2` | `4f20ad787ee9`, 06:50Z | ecce3fd | one step per reply, the next when the dial shows the last |

## Result

`python3 analyze.py <run>` scores each change from the bus and raw logs. The
dev line is AER-6052's run, scored by the same script. Its logs are in
pyEfis#519, `docs/aer-6052-evidence/bench/run1/`, from fix-gateway dev 16282cd.

| Run | Change | First FD roll | Heading moved against the change | Dial at the bug | Heading at the bug (within 5 deg) | Dial moves in the first 10 s, median gap |
|---|---|---|---|---|---|---|
| dev (AER-6052) | +40.5 (72.7 to 113.2) | -3.1 (left) | **319.0 deg** | (no raw log) | 111.7 s | (no raw log) |
| run1 | +40.2 | +3.1 | 0.0 | 20.34 s | 24.3 s | 20, 0.52 s |
| run1 | -40.0 | -3.0 | 0.0 | 20.32 s | 25.2 s | 20, 0.52 s |
| run1 | +150.0 | +3.1 | 0.0 | 77.62 s | 82.5 s | 20, 0.53 s |
| run2 | +40.2 | +3.0 | 0.0 | 1.56 s | 16.0 s | 40, 0.038 s |
| run2 | -40.0 | -3.1 | 0.1 | 1.61 s | 15.1 s | 40, 0.039 s |
| run2 | +150.0 | +3.2 | 0.0 | 6.09 s | 52.9 s | 150, 0.040 s |

- **Every walked change turned the short way.** The flight director's first
  roll command was on the side of the change, and the heading never moved
  against it. run2's 0.1 deg is the heading's noise before the turn began.
- **run1 found XPB-024.** X-Plane applied each burst of 10 `heading_up`
  packets as one step: the dial moved 1.0 deg per burst, about every 0.52 s,
  which was the adapter's wait. So the bug crept at about 2 deg/s, and the
  aircraft followed it, slower than a standard-rate turn.
- **run2 is the shipped walk.** One step per reply, and the dial moves on
  every reply, about 26 a second. The dial reaches the bug in 1.6 s for 40
  deg and 6.1 s for 150 deg. The rest is the aircraft's own turn, about 2.8 deg/s
  over the 150.
- The bug ends on the knob's steps. The dial started at 73.39 in both runs,
  so a bug of 112.7 lands at 112.39, and the bus then shows the dial's value.

## Files

- `run*/steps.jsonl`: each sequencer step, with the bus state.
- `run*/bus.jsonl.gz`: the private gateway's updates of HEAD, FDROLL, ROLL,
  HEADBUG, APLAT, APENG, TRACK and APMSG, with flags.
- `run*/raw.jsonl.gz`: the adapter's raw log, holding every dataref it reads
  on change, including `hdg_dial`, the sim's heading dial.
- `run*/truth.jsonl.gz`, `session.json` and `timeline.jsonl`: the sim bench's
  own record (`sim-session.sh fetch`).
- `run*/session.txt`, `events.txt` and `fixgw.txt`: the hooks, the follower
  and the private gateway's log. The netfix "second writer" warning on
  HEADBUG is the sequencer acting as the control head.
- `rig/`: everything that ran on the Beelink.

## Re-flying it

On the Beelink, in `~/bench-scratch/aer6268`, with `rig/*` and
`sim_follow.py` (from any earlier run directory) copied there:

```sh
bash setup.sh run3                      # private fixgw tree at the branch tip
# from the agent container, with XPBENCH_TOKEN:
sh makerplane/processes/paperclip/scripts/sim-session.sh submit rig/profile.yaml AER-6268
# on the Beelink, straight after the submit:
setsid nohup python3 sim_follow.py --sid <id> --dir run3 \
    --on-start 'sh session.sh start' --on-end 'sh session.sh stop' \
    > run3/follow.out 2>&1 < /dev/null &
python3 analyze.py run3                 # once the follower writes DONE
```
