"""Checks docs/screenshot.png: border, aligned meters and margins.

Usage (from the project folder):  python tools/check_layout.py
"""
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
im = Image.open(os.path.join(ROOT, "docs", "screenshot.png")).convert("RGB")
w, h = im.size
px = im.load()


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


EDGE = rgb("#30383f")
SEGMENT_COLORS = [rgb(c) for c in ("#262d33", "#3b2828", "#4f5b66", "#62a8dc", "#e3a63f", "#ec5b52")]


def near(color, target, tol=8):
    return all(abs(color[i] - target[i]) <= tol for i in range(3))


def is_segment(color):
    return any(near(color, target) for target in SEGMENT_COLORS)


ok = True


def check(name, condition, detail=""):
    global ok
    print(("PASS " if condition else "FAIL ") + name + (f" [{detail}]" if detail else ""))
    ok = ok and bool(condition)


print("size", (w, h))
check("panel size is reasonable", 200 <= w <= 620 and 110 <= h <= 560, f"{w}x{h}")
check("1 px border left and right",
      all(near(px[0, y], EDGE) and near(px[w - 1, y], EDGE) for y in range(1, h - 1)))
check("1 px border top and bottom",
      all(near(px[x, 0], EDGE) and near(px[x, h - 1], EDGE) for x in range(1, w - 1)))

# A meter is a band of pixel rows where most of the width is segments.
meter_rows = [y for y in range(h) if sum(1 for x in range(w) if is_segment(px[x, y])) > w * 0.5]
bands = []
for y in meter_rows:
    if bands and y == bands[-1][1] + 1:
        bands[-1][1] = y
    else:
        bands.append([y, y])
print("meters (y)", bands)
check("at least two meters", len(bands) >= 2, str(len(bands)))

edges = []
for top, _bottom in bands:
    xs = [x for x in range(w) if is_segment(px[x, top])]
    edges.append((min(xs), max(xs)))
print("meter edges (x)", edges)
check("meters aligned on the left", len({left for left, _ in edges}) == 1, str(edges))
check("meters aligned on the right", max(r for _, r in edges) - min(r for _, r in edges) <= 1, str(edges))
check("left margin matches right margin (±3 px)",
      bool(edges) and abs(edges[0][0] - (w - 1 - edges[0][1])) <= 3, str(edges[:1]))
check("all meters share one height", len({b - t for t, b in bands}) == 1, str(bands))
check("free bottom margin", bool(bands) and bands[-1][1] < h - 8, str(bands[-1:]))
raise SystemExit(0 if ok else 1)
