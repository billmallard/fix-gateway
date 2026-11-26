# AER-6622: the panel's BARO key gives pyEfis's knob to the baro setting

Bill, at the bench on 2026-11-26: HDG BUG and CDI SRC worked. BARO flashed
green, then red. It did not take focus, and the Knobster could not change baro
until he touched the altitude box on the PFD. The cause was in two places. The
shipped page gave key 1 no `press`, which the plugin answers with a red flash.
pyEfis's `focus` action also had nothing to answer for BARO, because only the
HSI and the autopilot panel took it.

The fix is in two PRs:

- pyEfis `aer-6622/baro-focus`: the altimeter tape answers `focus: BARO` (its
  baro box) and `focus: <alt_bug_dbkey>` (its ALTSEL box). It adds
  `buttons/baro-focus.yaml` and a stock binding on `TSBTN{id}36`.
- fix-gateway `aer-6622/baro-focus-key`: page key 1 pulses `TSBTN136`.

## The bench run (Beelink, both branches staged, 2026-11-26 13:29 UTC)

`branch/probe.log`: RESULT PASS. Before the press, one knob step leaves BARO
at 29.92. After the firmware's own press of key 1 (`AD_PRESS 1`), five steps
up and five down move BARO 0.01 inHg each, from 29.92 to 29.97 and back.
After the 10 s timeout a step does nothing.

`baro-box-strip.png`: the PFD's baro box at the four marks, cropped from the
1920x1080 frames at x 990-1140, y 600-660 and scaled 3x nearest-neighbour.
From left to right: before, focused (cyan ring), turned (29.97), timed out
(no ring).

The run does not include a dev baseline on the bench. Bill's report is the
before. The unit tests (`tests/screens/test_focus_action.py` in pyEfis)
fail without the altimeter change.

## Commands

As `pyefis` on the Beelink, with both branches staged by `bench-deploy.sh --ref`:

    cd ~/bench-scratch
    flock -w 900 /tmp/pyefis-bench.lock sh run_baro_focus_probe.sh aer6622-baro

The strip, from this directory:

    python3 - <<'PY'
    from PIL import Image, ImageDraw
    names = ("before", "focused", "turned", "timed_out")
    box = (990, 600, 1140, 660); S = 3
    w = (box[2] - box[0]) * S; h = (box[3] - box[1]) * S
    out = Image.new("RGB", (w * 4 + 30, h + 40), "white"); d = ImageDraw.Draw(out)
    for i, n in enumerate(names):
        im = Image.open("branch/frame-%s.png" % n).convert("RGB").crop(box).resize((w, h), Image.NEAREST)
        x = i * (w + 10); out.paste(im, (x, 40)); d.text((x + 4, 12), n, fill="black")
    out.save("baro-box-strip.png")
    PY
