# -*- coding: utf-8 -*-
"""Generate assets/icon-180.png -- a square tile that reads at 96px."""
from pathlib import Path

from PIL import Image, ImageDraw

S = 720          # draw big, downscale for clean edges
OUT = 180
BG = (17, 21, 26, 255)        # near-black slate
CORAL = (216, 90, 48, 255)    # same accent as the README chart
TEAL = (29, 158, 117, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded background
d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=BG)

# part profile: a square outline, slightly left of centre
stroke = int(S * 0.062)
pad = int(S * 0.20)
box = [pad, pad + int(S * 0.02), S - pad, S - pad + int(S * 0.02)]
d.rounded_rectangle(box, radius=int(S * 0.10), outline=CORAL, width=stroke)

# a hole through it
r = int(S * 0.135)
cx, cy = S // 2, S // 2 + int(S * 0.02)
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=TEAL)

# small centre mark inside the hole, so the hole still reads as a hole
r2 = int(S * 0.042)
d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=BG)

img = img.resize((OUT, OUT), Image.LANCZOS)
out = Path(__file__).resolve().parent / "icon-180.png"
img.save(out, "PNG", optimize=True)
print("wrote", out, out.stat().st_size, "bytes")
