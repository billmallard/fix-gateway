"""Build the two strips in this directory from the bench grabs (see README.md).

    python3 make_strips.py <shots dir>
"""
import sys

from PIL import Image, ImageDraw, ImageFont

S = sys.argv[1]
STATES = [("1_before_turn", "before turn start"), ("2_in_turn", "in the turn"), ("3_past_bisector", "past the bisector")]
FONT = ImageFont.load_default(size=26)


def strip(kind, boxes, scale):
    tiles = []
    for name, label in STATES:
        im = Image.open(f"{S}/{name}_{kind}.png").convert("RGB")
        parts = [im.crop(b) for b in boxes]
        t = Image.new("RGB", (sum(p.width for p in parts), max(p.height for p in parts) + 40), "black")
        x = 0
        for p in parts:
            t.paste(p, (x, 40))
            x += p.width
        ImageDraw.Draw(t).text((10, 6), label, fill="white", font=FONT)
        tiles.append(t)
    w = sum(t.width for t in tiles) + 20 * (len(tiles) - 1)
    out = Image.new("RGB", (w, max(t.height for t in tiles)), (40, 40, 40))
    x = 0
    for t in tiles:
        out.paste(t, (x, 0))
        x += t.width + 20
    return out.resize((int(out.width * scale), int(out.height * scale)), Image.LANCZOS)


# HSI (left of the PFD) and the top of the Flight Plan page; the map around HENER.
strip("fpl", [(450, 640, 810, 1045), (1250, 140, 1920, 560)], 0.8).save("hsi_fpl_through_hener.png", optimize=True)
strip("map", [(1400, 330, 1820, 740)], 1.0).save("map_through_hener.png", optimize=True)
