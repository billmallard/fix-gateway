# AER-6630 (TESS2b): a Tessera press gives pyEfis's knob to the heading bug

2026-11-26, Beelink simulator bench. Tessera board 1 (firmware 1.2.12) at
`/dev/tessera` and the Knobster on the same fixgw. X-Plane was off, so the
tapes and the heading are flagged, and the attitude shows its SVS UNAVAIL
fallback. The served PFD is `screens/managed.yaml`, with the HSI's readouts
in `top_panel`.

Each side was staged with `bench-deploy.sh` (owner AVIONICS):

| Side | pyEfis | fix-gateway |
|---|---|---|
| branch | `aer-6630/panel-focus` 4251ead5 | `aer-6630/hdg-bug-focus-key` 8912789 |
| dev | `dev` 5143414f | `dev` 8c92684 |

The deploy copied the new stock files into the active config: page key 4 with
`press: {pulse: TSBTN132}`, and the `TSBTN{id}32` binding. `--restore` put
both back.

## What the probe does (`focus_probe.py`)

1. One knob step (`ENC3` +1, a raw netfix write like the Knobster plugin's)
   before any press. HEADBUG must not move.
2. `AD_PRESS 4` to `/dev/tessera`. This is the firmware's own synthetic press
   of key 4 (HDG BUG), as `press_probe.py` used for AER-6622.
3. Five steps of +1, then five of -1. HEADBUG must follow each step and end
   where it started.
4. A wait past the 10 s focus timeout, then one more step. HEADBUG must not
   move.

## Result

| Side | Result | After the press |
|---|---|---|
| branch | **PASS** (`branch/probe.log`) | HEADBUG 0 -> 1, 2, 3, 4, 5 -> 4, 3, 2, 1, 0. Unchanged before the press and after the timeout |
| dev | **FAIL** (`dev/probe.log`) | HEADBUG stays 0 on all ten steps. Key 4 has no press on dev |

`branch-readout-strip.png` is the HSI's readout panel, cropped from the
1920x1080 glass and scaled 3x nearest-neighbour. Top to bottom: before, after
the press (cyan focus ring on HDG), after +5 (005°), and after the timeout
(ring gone, 000°). `dev-readout-strip.png` shows the same marks on dev: no
ring, 000° throughout. `branch/clip.mp4` is the whole glass at 10 fps across
the branch run (x11grab). The full frames are in `branch/` and `dev/`, scaled
to 960x540.

## A defect this run found: the attitude display goes black (AER-6639)

On the branch, the attitude area goes black once HDG takes focus
(`branch/frame-focused.png`), and it stays black until pyEfis restarts. A
plain touch on the HDG readout does the same on dev, with no AER-6630 code
(`touch-dev/`, `touch_dev.sh`). A touch on the baro box does not. AER-6639
tracks the defect. It is in the HSI's focus state, not in the new action.
These PRs stay drafts until it is fixed.

## Not shown here

- A finger on the panel and a hand on the Knobster. The press is the
  firmware's `AD_PRESS`, and the turns are bus writes in the Knobster
  plugin's form. The hands-on check is Bill's, at the panel (AER-6622).
- The heading bug on the rose: with no sim heading, the HSI draws no bug.
  The readout shows the bug's value.

## Run it

On the bench, as `pyefis`, with both branches staged:

```
flock -w 900 /tmp/pyefis-bench.lock sh run_focus_probe.sh out
```

The probe talks to netfix on a raw socket. Never use
`fixgw.netfix.Client.stop()` against a shared gateway: it sends `@xkill`.
