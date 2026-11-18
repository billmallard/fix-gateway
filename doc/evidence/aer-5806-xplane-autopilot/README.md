# AER-5806 (AFC2): the X-Plane autopilot adapter on the sim bench

Session `bf3a33402db4` on the sim bench, 2026-11-18 05:03-05:08Z. X-Plane 12.4.3,
the Laminar C172 G1000 (preconfigured autopilot type 2, GFC-700), en route on V27
from 3 nm before HENER at 7,500 ft.

- `modes.md`: the bench log per mode. For each step, the sequencer's actions, what
  the gateway published (bus), and the raw datarefs the adapter received (sim).
- `steps.jsonl`: the sequencer's own log. Each press, and each PASS or FAIL with
  the state it read.
- The adapter's raw log is the playback fixture,
  `tests/plugins/xplane/data/afc2_c172_bf3a33402db4_raw.jsonl`.
  `..._bus.json` beside it holds the per-key changes the gateway published.
  `tests/plugins/xplane/test_xplane_autopilot_playback.py` replays one against
  the other.

## How it ran

The driver (xplane-bench) flew the profile: air start, autopilot coupled to GPS,
en-route legs. On the Beelink, `sim_follow.py` started a **private** fixgw from
this branch, with its own config tree, udp_in 49101 and netfix
127.0.0.1:3591. It used the bench's RREF block plus `autopilot: enabled: true,
raw_log: ...`. A sequencer then wrote the control head's requests into that
gateway (`APCMD`, then `APCMDSEQ`) and its targets (`ALTSEL`, `VSSEL`, `IASSEL`),
and logged every key with its flags. The live gateway and pyEfis were not touched.

The scripts are in the AER-5806 bench directory, not in this repo:
`seq.py` (the sequencer), `session.sh` (the hooks), `mkconfig.py` (the private
config), `profile.yaml`. Regenerate `modes.md` from `steps.jsonl`, `bus.jsonl`
and `raw.jsonl` with the script quoted in the PR.

## What it showed

| Done-when | Result |
|---|---|
| HDG to NAV capture | HDG to GPS, at 9.8 s (driver) and 146.3 s (head request). NAV captured on the first reply both times. The aircraft was on course, so no armed phase was visible |
| VS to ALT capture at ALTSEL | VS with ALT armed, captured 54 s later at 7,935 ft (ALTSEL 8,000), then ALT |
| IAS climb | IAS with ALT armed, airspeed within 5 kt of the 80 kt target after 21 s (82.9 kt), ALT captured at 8,402 ft (ALTSEL 8,500) |
| LVL | X-Plane reported ROL with PIT, ALT armed, then ALT. It has no LVL state, so none was claimed |
| AP disconnect | APENG False and APDISC True within 0.1 s of DISC |
| NAV with no nav source | HSI on NAV2 (no signal): APMSG "NAV refused: no signal on NAV2", nothing sent, APLAT HDG and nothing armed for 5 s |
| Stale | The sim went silent 0.6 s after the disconnect (the driver's servo guard paused it). Subscribers got the old flag 1.9 s after the last fresh write for FDPITCH (tol 1 s) and 2.1 to 2.4 s after it for APLAT and APENG (tol 2 s) |

Not shown on the bench: the disconnect **acknowledgement**. The second DISC came
3 s after the first, when X-Plane had already stopped sending. The adapter
correctly refused it (no fresh data), so APDISC stayed latched. The
acknowledgement is covered by the unit tests and the fake-sim server run.

The old flag reaches a subscriber on the database's 1 s expiry tick. A read
reports a key old as soon as its tol passes; a push can arrive up to 1 s later.
That is the existing database behaviour, not this adapter's.

The flight director's pitch is the commanded attitude, not an error. In steady
flight FDPITCH tracks PITCH: 2.84 against 3.10, and 6.40 against 6.59 in the IAS
climb.
