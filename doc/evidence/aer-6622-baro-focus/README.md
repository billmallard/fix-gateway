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

## CDI SRC shows the source (Bill, the same session)

The CDI SRC key showed the fixed text `SRC`. It now shows `NAVSRC` through
the page's new `enum` field (`{0: VLOC1, 1: VLOC2, 2: GPS, 3: GPS2}`, the
names pyEfis's annunciator uses for NAVSRC). A press still pulses pyEfis's
cdi-source button.

`branch/cdi_src_probe.log`: RESULT PASS. Three firmware presses of key 9
(`AD_PRESS 9`) stepped NAVSRC from 1 to 2, 0 and 1, and the log prints the
name the page gives each value. pyEfis's button wraps NAVSRC through 0, 1
and 2, so GPS2 never appears from a press. The probe reads the name from
the page file. The glass of the panel is Bill's eye, and no frame of it is
taken.

The first attempt at the probe expected a cycle of four. Because of a shell
fallback it also ran twice, and it left NAVSRC on VLOC2. The corrected
probe ran once, and one more press put NAVSRC back on GPS (2.0), where it
started.

    cd ~/bench-scratch
    flock -w 900 /tmp/pyefis-bench.lock python3 -u cdi_src_probe.py

## Map range from the panel (Bill, the same session)

The MAP RANGE key showed the ladder index and flashed red, and nothing on the
panel could change the range. Keys 11 and 12 (RANGE - and +) now pulse
pyEfis's `map-range-down` and `map-range-up` buttons (`TSBTN137`, `TSBTN138`),
which step `MAPRANGE` by one and stop at the ends.

The bench's live screen, `screens/managed.yaml`, had no `range_key` on its
moving map, so no key could move it. I added `range_key: MAPRANGE` to the
active config on the bench, with a backup beside it. That is a bench config
change, not part of either PR.

`branch/map-range/probe.log` and `map-range-strip.png`: key 12, 12, 11, 11
stepped MAPRANGE 2, 3, 4, 3, 2. The map's range readout followed: 50, 200,
500, 200 and 50 NM. The strip crops the map's status text at x 1255-1555,
y 20-110 and scales it 2x nearest-neighbour.

**Found on the way:** the map came up at 50 NM, not its 10 NM. `MAPRANGE` is
an index into the map's own ladder, and fix-gateway's database initial of 2
means 10 NM on the default ladder (2,5,10,...). On this screen's ladder
(2,10,50,200,500,2000), 2 is 50 NM. On the bench I set `MAPRANGE = 1` in
`init_data/custom.ini` (backed up), wrote 1 to the bus, and the map is back
at 10 NM. The general problem stays: a screen whose ladder differs from the
default boots at a different range once it is bound. The same index is why
the panel's MAP RANGE key shows a number, not a range.

    flock -w 900 /tmp/pyefis-bench.lock sh map_range_probe.sh aer6622-rng
