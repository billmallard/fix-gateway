"""The altimeter tape from each Beelink cruise frame: dev on top, the branch
below, BARO 29.92 / 30.92 / 28.92 left to right. 1:1 crops of the 1920x1080
x11grab frames, so the digits are the glass's own.

usage: python3 make_strip.py   (from this directory; needs Pillow)
"""
from PIL import Image, ImageDraw

BOX = (995, 40, 1170, 660)          # the tape and its Kollsman box
SETTINGS = ("29.92", "30.92", "28.92")
ROWS = (("dev", "dev cf26588: ALT is GPS height"),
        ("fix", "branch: ALT from pressure and BARO"))
W, H = BOX[2] - BOX[0], BOX[3] - BOX[1]
LABEL = 22
out = Image.new("RGB", (W * 3, (H + LABEL) * 2), "black")
d = ImageDraw.Draw(out)
for r, (side, title) in enumerate(ROWS):
    y = r * (H + LABEL)
    d.text((4, y + 5), title, fill="white")
    for c, b in enumerate(SETTINGS):
        im = Image.open("beelink/%s/png/cruise-%s.png" % (side, b)).crop(BOX)
        out.paste(im, (c * W, y + LABEL))
out.save("tape_strip.png")
