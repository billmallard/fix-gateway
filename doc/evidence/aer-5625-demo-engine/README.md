# AER-5625 (DEMO3): the demo's CHT event on the Beelink's EMS screen

`cht-strip.png`: the CHT columns of pyEfis's EMS screen, 1:1, through the
event. The cylinder number is drawn in the colour of the value:

| frame | demo clock | CHT13 | annunciate flag | glass |
|---|---|---|---|---|
| `f032_t436` | go-around begins | 158.9 C | clear | white 317 F |
| `f040_t472` | climbing | 205.0 C (warn 204.4) | clear | yellow 401 F |
| `f00_t415` | after the alarm crossing | 237.2 C (alarm 232.2) | set (`a`) | red 459 F, the EMS2 button is red |
| `f03_t428` | after enrichment | 221.8 C | clear (deadband 3.2 C) | yellow 431 F |
| `f09_t455` | recovering | 203.6 C | clear | white 398 F |

`ems-at-alarm.png` is the whole 1920x1080 screen at the alarm. `values.txt`
holds the gateway's reading of CHT13, CHTMAX1, FUELF1, EGT13, VS and ALT,
taken over netfix beside each frame. The flags column is the `a` there.

## How it was taken (2026-11-14, Beelink 10.110.10.241)

The live bench runs X-Plane with DEMO off, so nothing live was touched. An
isolated stack ran under `~/bench-scratch/aer5625/stack`:

- this branch's fix-gateway (`git clone --shared ~/src/fix-gateway`, branch
  `aer-5625/demo-engine` at 9edf2e4), run with `PYTHONPATH=<clone>/src` and a
  copy of the bench's active fixgw config. The copy had DEMO, FLIGHTPLAN,
  NETFIX, SYSTEM, ANNUNCIATE and COMPUTE on, XPLANE, KNOBSTER, CANFIX, QUORUM,
  STATE_PERSIST and DATA_RECORDER off, and netfix on 127.0.0.1:3492. The
  engine limits are the shipped `init_data/default.ini` (CHT13 highWarn
  204.44, highAlarm 232.22).
- the bench's pyEfis checkout (dev, c445c000) with a copy of its active
  config, `FixPort: 3492` and `defaultScreen: EMS`. It ran fullscreen over
  the kiosk, under the bench lock, which was never restarted.
- one 1920x1080 grab every 4 to 5 s:
  `ffmpeg -f x11grab -draw_mouse 0 -video_size 1920x1080 -i :0.0 -frames:v 1`.

`make_strip.py` crops and captions the five frames into `cht-strip.png`.

## What this capture does not show

Two capture scripts ran, one after the other. The first had been left
running from an earlier attempt, and the second restarted the private
gateway while the first was waiting. So the frames come from two clocks:
`f0NN_tNNN` (three digits) from the first, `fNN_tNNN` from the second.
Both read the same gateway's single loop after the restart. The 25 s
between them covers the alarm crossing itself, so no frame shows the value
between 229 C and 237 C. The tests cover the crossing:
`tests/plugins/demo/test_engine.py`, the flag set once and cleared once,
by the annunciate plugin.

The SVS/attitude area is grey in an x11grab of the GL widget here; that is
not part of this change.
