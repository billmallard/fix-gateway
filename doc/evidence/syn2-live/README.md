# SYN2 live-mode bench runs, 2026-10-30

The Beelink frames and `beelink-marks.jsonl` are from run 2 (22:28-22:48 UTC,
branch at `60153d6`, the generator now feeding `IAS`), against the bench
gateway at `27dccad` (dev with AER-4217's 2 s position bound). Run 1
(12:41 UTC, no `IAS`, so the tape read 0 kt) is in the history of this
directory. The Pi 5 log is from run 1; the Pi was not re-run, because IAS
changes nothing it can evidence without a screenshot.

Profile `fp8-ksmx-rnav30-missed` flown live at x1 (1172.2 s) into each
bench's running gateway, no sim, from a scratch copy of `src/fixgw` on
branch `syn2/live-mode` (`~/bench-scratch/syn2/src`, not the bench checkout),
under `/tmp/pyefis-bench.lock` with a 45 min outer timeout.

| Box | Display | Result | Evidence |
|---|---|---|---|
| Beelink 10.110.10.241 | X11, 1920x1080 | run 2: exit 0, 117414 writes, worst lateness 0.000 s, 9 hooks, 0 failed | `beelink-marks.jsonl`, the JPEGs here |
| Pi 5 10.110.10.199 | eglfs | run 1: exit 0, 111552 writes, worst lateness 0.000 s | `pi5-marks.jsonl` only: there is no screenshot on eglfs |

The Pi's active database has no `sim.yaml`, so its `REF*` writes were
dropped (the run says so on its first line); nothing on the glass reads them.

## Regenerate

Beelink (screenshots by `x11grab`; pyEfis's SIGUSR1 grab leaves the GL SVS
panel blank):

```
cd ~/bench-scratch/syn2/src
flock -o -w 900 /tmp/pyefis-bench.lock timeout 45m \
    ~/pyefis-venv/bin/python -m fixgw.sim live fixgw/sim/profiles/fp8-ksmx-rnav30-missed.yaml \
        --log ~/bench-scratch/syn2/marks2.jsonl \
        --hook 'DISPLAY=:0 ffmpeg -loglevel error -y -f x11grab -draw_mouse 0 \
                -video_size 1920x1080 -i :0 -frames:v 1 \
                ~/bench-scratch/syn2/shots2/$SYN_INDEX-$SYN_WHAT-$SYN_FIX.png'
```

`00-before` was one manual `x11grab` before the run. Pi 5: the same command
without `--hook`. The PNGs were converted here to JPEG (quality 85, full
1920x1080) to keep the repository small:

```
python3 -c "from PIL import Image; Image.open('X.png').convert('RGB').save('X.jpg', quality=85)"
```

## What the frames are

`01-start` at PAMJI; `02`-`04` the sequences to CIMSO, ANIBY and RW30;
`05-map` at the MAP; `06` after RESUME, to HANNU; `07` HANNU to WINCH;
`08` WINCH to WINCH, entering the hold; `09-end` the last row, read 3 s
after the feed stopped (see "Marks on the last row" in
`src/fixgw/sim/PROFILE_FORMAT.md`).
