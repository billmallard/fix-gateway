# AER-5856 (AFC2b): the pilot's settings both ways with X-Plane, on the sim bench

Session `ef1dea3b4773` on the sim bench, 2026-11-18, 09:59Z start. The sequencer ran
10:01:13-10:04:53Z. The aircraft was the Laminar C172 G1000 (GFC-700), en route on V27 from
3 nm before HENER at 7,500 ft, with the autopilot coupled to GPS. The heading bug and the OBS
courses therefore steered nothing.

## How it ran

- **The gateway.** On the Beelink, `sim_follow.py` started a private fixgw from the branch at
  `a2db549`. It used the bench's live `xplane.yaml` (built by AFC2's `mkconfig.py`), on
  udp_in 49102, with netfix on 127.0.0.1:3592. The live fixgw and pyEfis were not touched.
- **An old config.** The live `xplane.yaml` still has the plain `HEADBUG`, `NAV1CRS` and
  `NAV2CRS` rows. The private gateway logged each one as ignored at start, so the path for an
  old seeded file ran too.
- **The sequencer.** `seq.py` wrote the settings over netfix, the path pyEfis uses. It read
  X-Plane's seven dials on **its own** RREF subscription (`truth.jsonl`, changes only), so the
  dial values below do not come from the adapter under test. It turned X-Plane's own knobs
  with CMND packets: `heading_up`, `obs1_up`, `obs2_up`, `barometer_up`, `altitude_up`,
  `vertical_speed_up` and `airspeed_up`.
- **The checks.** For each key, `steps.jsonl` logs one PASS or FAIL per check:
  - the bus starts on the sim's dial;
  - pilot: after a write, X-Plane's dial followed within 5 s;
  - pilot: the bus and the dial then both held the value for 5 s, sampled every 50 ms;
  - cockpit: three presses of the sim's knob moved its dial;
  - cockpit: the bus followed the dial;
  - spin: 15 clicks 40 ms apart, with the bus sampled continuously from the first click to
    3 s after the last; no sample was off the click path or stepped back;
  - spin: the bus and the dial both ended on the last click;
  - restore: the starting value written back.

  For the heading bug and both courses, the spin starts at 352 and crosses 360 to end at 7.

## Result: 57 of 57 checks pass

| Key | Start | Checks passed | Spin's last click | Bus / dial at end | Bus samples in the spin |
|---|---|---|---|---|---|
| `HEADBUG` | 73.42 | 8 / 8 | 7 | 7 / 7 | 9670 |
| `NAV1CRS` | 241 | 8 / 8 | 7 | 7 / 7 | 9331 |
| `NAV2CRS` | 0 | 8 / 8 | 7 | 7 / 7 | 9614 |
| `BARO` | 29.92 | 8 / 8 | 30.15 | 30.15 / 30.15 | 10672 |
| `ALTSEL` | 7500 | 8 / 8 | 9800 | 9800 / 9800 | 10875 |
| `VSSEL` | 0 | 8 / 8 | 2300 | 2300 / 2300 | 9902 |
| `IASSEL` | 105 | 8 / 8 | 128 | 128 / 128 | 9552 |

The gateway's one-writer check logged a second writer (netfix beside xplane) for exactly
these seven keys, the set `doc/autopilot_keys.md` names. It logged no errors.

## What this does not cover

- **The pyEfis glass was not in the loop.** The writes came over netfix, as pyEfis's do, but
  from a script, not from the AFC3 panel's value boxes or the HSI knobs. Whether the AFC4 PFD
  shows the bugs is not covered.
- **The coupled modes.** The aircraft stayed in GPS and ALT. A changed heading bug in HDG mode,
  or a changed ALTSEL during a capture, is a mode behaviour AFC2's session covers. This session
  does not.

## Regenerate

`seq.py`, `session.sh` and `profile.yaml` are here. `mkconfig.py` and `sim_follow.py` are
AFC2's, unchanged (`~/bench-scratch/aer5806/run3` on the Beelink). Submit `profile.yaml` with
`sim-session.sh submit`, then arm:

    python3 sim_follow.py --sid <id> --dir . --on-start "sh session.sh start" --on-end "sh session.sh stop"

The table above is built from `steps.jsonl`.
