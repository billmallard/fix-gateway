# Compose cht-strip.png from the bench frames: the CHT columns of the EMS
# screen (x 1180-1460, y 700-1080 of the 1920x1080 grab), 1:1, captioned.
from PIL import Image, ImageDraw, ImageFont
frames = [("f032_t436", "before: go-around power, 159 C"), ("f040_t472", "past warn: 205 C (401 F)"),
          ("f00_t415", "past alarm: 237 C, flag set"), ("f03_t428", "enriched: 222 C, flag clear"),
          ("f09_t455", "back under warn: 204 C")]
font = ImageFont.load_default(size=16)
crops = []
for f, cap in frames:
    im = Image.open(f + ".png").convert("RGB").crop((1180, 700, 1460, 1080))
    c = Image.new("RGB", (im.width, im.height + 40), "white")
    c.paste(im, (0, 40))
    ImageDraw.Draw(c).text((6, 10), cap, fill="black", font=font)
    crops.append(c)
out = Image.new("RGB", (sum(c.width for c in crops) + 8 * (len(crops) - 1), crops[0].height), "white")
x = 0
for c in crops:
    out.paste(c, (x, 0))
    x += c.width + 8
out.save("cht-strip.png")
