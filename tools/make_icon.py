"""Generates assets/telltale.ico (16–256 px) and PNG previews.

The icon is the panel in miniature: graphite faceplate, the green "all good"
light and three segmented meters.

Usage:  python tools/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
S = 1024  # drawn large and scaled down for clean edges

FACE = (27, 32, 37, 255)      # COLOR_FACE
EDGE = (48, 56, 63, 255)      # COLOR_EDGE
TRACK = (38, 45, 51, 255)     # COLOR_TRACK
CALM = (98, 168, 220, 255)    # COLOR_CALM
OK = (95, 191, 134, 255)      # COLOR_OK

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
margin = 40
draw.rounded_rectangle((margin, margin, S - margin, S - margin), radius=180, fill=FACE, outline=EDGE, width=16)

# The light: a square, like the one next to the headline.
draw.rectangle((172, 168, 300, 296), fill=OK)

# Three meters, five chunky segments each, so they still read at 16 px.
left, right = 172, S - 172
segments, gap = 5, 26
seg_w = (right - left - (segments - 1) * gap) / segments
for row, lit in enumerate((3, 4, 2)):
    top = 388 + row * 170
    for index in range(segments):
        x0 = left + index * (seg_w + gap)
        draw.rectangle((x0, top, x0 + seg_w, top + 96), fill=CALM if index < lit else TRACK)

ASSETS.mkdir(parents=True, exist_ok=True)
preview = img.resize((256, 256), Image.LANCZOS)
preview.save(ASSETS / "telltale_256.png")
preview.resize((64, 64), Image.LANCZOS).save(ASSETS / "telltale_64.png")
preview.save(
    ASSETS / "telltale.ico",
    sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
)
print("icon:", ASSETS / "telltale.ico", (ASSETS / "telltale.ico").stat().st_size, "bytes")
