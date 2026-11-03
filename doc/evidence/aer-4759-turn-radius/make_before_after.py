"""Before/after HSI strip for TURN1 (AER-4759) from two sets of bench grabs (see README.md).

    python3 make_before_after.py <AER-4715 shots dir> <AER-4759 shots dir>
"""
import sys

from PIL import Image, ImageDraw, ImageFont

OLD, NEW = sys.argv[1], sys.argv[2]
STATES = [("1_before_turn", "before turn start"), ("2_in_turn", "in the turn"), ("3_past_bisector", "past the bisector")]
ROWS = [(OLD, "AER-4715: standard-rate arc"), (NEW, "TURN1: 15 deg bank arc")]
HSI = (450, 640, 810, 1045)   # the HSI, left of the PFD, as in ../aer-4715-arc-xtk/make_before_after.py
SCALE = 2                     # nearest-neighbour, so the CDI bar's offset is not smoothed away
FONT = ImageFont.load_default(size=28)
GAP, HEAD, LABEL = 20, 44, 44

w = (HSI[2] - HSI[0]) * SCALE
h = (HSI[3] - HSI[1]) * SCALE
out = Image.new("RGB", (len(STATES) * w + (len(STATES) - 1) * GAP, HEAD + len(ROWS) * (LABEL + h)), (40, 40, 40))
d = ImageDraw.Draw(out)
for c, (_, label) in enumerate(STATES):
    d.text((c * (w + GAP) + 10, 8), label, fill="white", font=FONT)
for r, (src, label) in enumerate(ROWS):
    y = HEAD + r * (LABEL + h)
    d.text((10, y + 8), label, fill="white", font=FONT)
    for c, (name, _) in enumerate(STATES):
        im = Image.open(f"{src}/{name}_fpl.png").convert("RGB").crop(HSI)
        out.paste(im.resize((w, h), Image.NEAREST), (c * (w + GAP), y + LABEL))
out.save("hsi_before_after_hener.png", optimize=True)
