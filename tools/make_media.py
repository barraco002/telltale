"""Renders the README media from demo snapshots, so it never depends on what your PC
happens to be doing: docs/screenshot.png, docs/states.png, docs/demo.gif and
docs/social-preview.png.

The real panel is drawn on screen and captured, so this needs a visible desktop.

Usage (from the project folder):  python tools/make_media.py
"""
import math
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont, ImageGrab

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import telltale  # noqa: E402

DOCS = os.path.join(ROOT, "docs")
FONT_FILE = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "bahnschrift.ttf")
STAGE = "#111417"            # quiet backdrop behind the panel
INK, INK_2 = "#e7ebee", "#97a3ad"

REST = telltale.Advice(
    telltale.LEVEL_REST, "Give your PC a break",
    "The GPU has been at 82 °C for a while. It's fine, in theory.", ("gpu-heat",))
URGENT = telltale.Advice(
    telltale.LEVEL_URGENT, "Urgent: close or throttle apps",
    "Only 240 MB of RAM left. Top RAM users: chrome 1.8 GB, code 900 MB.", ("ram",))

app = telltale.MonitorApp(start_topmost=True, persist=False)
app._worker.stop()  # demo snapshots only
time.sleep(0.3)
while not app._queue.empty():
    app._queue.get_nowait()
app._pos, app._size = (80, 80), None


def font(size, variation="Regular"):
    face = ImageFont.truetype(FONT_FILE, size)
    try:
        face.set_variation_by_name(variation)
    except Exception:
        pass
    return face


def snap(cpu=27.0, free=5300.0, gpu=46.0, advice=None):
    return telltale.Snapshot(
        cpu_percent=cpu, cpu_temp=None, gpu_percent=0.0, gpu_temp=gpu, gpu_present=True,
        gpu_util_reported=False, gpu_name="Demo GPU", ram_available_mb=free, ram_total_mb=16303.0,
        advice=advice or telltale.Advice(), stamp=time.time())


def reset(scale):
    for history in app._history.values():
        history.clear()
    app._hot = {"cpu": False, "gpu": False}
    app._set_scale(scale)
    for i in range(40):  # a minute of ordinary activity, so the peak markers look natural
        app._render(snap(cpu=24 + 20 * math.sin(i / 3.1) * math.sin(i / 7.0),
                         free=5300 + 400 * math.sin(i / 5.0), gpu=46 + 3 * math.sin(i / 6.0)))


def grab():
    for _ in range(6):
        app.update()
        time.sleep(0.04)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    return ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height()), all_screens=True).convert("RGB")


def shot(snapshot):
    app._render(snapshot)
    return grab()


os.makedirs(DOCS, exist_ok=True)

# 1. Plain screenshot at scale 1 (also used by tools/check_layout.py).
reset(1.0)
shot(snap()).save(os.path.join(DOCS, "screenshot.png"))

# 2. The three states side by side, with a caption under each.
reset(1.25)
states = [
    (shot(snap()), "Quiet while everything is fine"),
    (shot(snap(cpu=41, gpu=82, advice=REST)), "A nudge when heat or load lasts"),
    (shot(snap(cpu=33, free=240, gpu=55, advice=URGENT)), "A clear call when it matters"),
]
pad, gap, caption = 48, 40, font(26)
width = 2 * pad + sum(img.width for img, _ in states) + gap * (len(states) - 1)
height = 2 * pad + max(img.height for img, _ in states) + 56
board = Image.new("RGB", (width, height), STAGE)
draw = ImageDraw.Draw(board)
x = pad
for img, text in states:
    board.paste(img, (x, pad))
    draw.text((x, pad + max(i.height for i, _ in states) + 22), text, font=caption, fill=INK_2)
    x += img.width + gap
board.save(os.path.join(DOCS, "states.png"))

# 3. Demo GIF: calm, the GPU heats up, it cools down, RAM runs out, back to calm.
reset(1.5)
frames, durations = [], []


def frame(snapshot, ms):
    frames.append(shot(snapshot))
    durations.append(ms)


for i in range(10):
    frame(snap(cpu=22 + 14 * math.sin(i / 1.7)), 260)
for gpu in (58, 68, 76, 81):
    frame(snap(cpu=64, gpu=gpu), 320)
for i in range(8):
    frame(snap(cpu=58 + 6 * math.sin(i), gpu=82, advice=REST), 300)
for gpu in (74, 62, 52):
    frame(snap(cpu=30, gpu=gpu), 300)
for free in (2600, 1500, 900, 520, 330):
    frame(snap(cpu=48, free=free, gpu=55), 260)
for i in range(9):
    frame(snap(cpu=35 + 8 * math.sin(i), free=240, gpu=55, advice=URGENT), 300)
for free in (900, 2400, 4800):
    frame(snap(cpu=26, free=free, gpu=50), 300)
for i in range(4):
    frame(snap(cpu=22 + 10 * math.sin(i)), 300)

pad = 28
stage_w = max(f.width for f in frames) + 2 * pad
stage_h = max(f.height for f in frames) + 2 * pad
staged = []
for f in frames:
    canvas = Image.new("RGB", (stage_w, stage_h), STAGE)
    canvas.paste(f, (pad, pad))
    staged.append(canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE))
staged[0].save(os.path.join(DOCS, "demo.gif"), save_all=True, append_images=staged[1:],
               duration=durations, loop=0, optimize=True, disposal=1)

# 4. Social preview card, 1280x640 (upload it in the repo settings).
reset(2.0)
panel = shot(snap())
card = Image.new("RGB", (1280, 640), STAGE)
draw = ImageDraw.Draw(card)
px = 1280 - 72 - panel.width
card.paste(panel, (px, (640 - panel.height) // 2))
left = 80
draw.text((left, 170), "Telltale", font=font(120, "SemiBold"), fill=INK)
tag_font = font(36)
words, lines, line = "A tiny always-on-top hardware gauge for Windows that stays quiet until your PC needs you.".split(), [], ""
for word in words:
    candidate = f"{line} {word}".strip()
    if draw.textlength(candidate, font=tag_font) > px - left - 56:
        lines.append(line)
        line = word
    else:
        line = candidate
lines.append(line)
for index, text in enumerate(lines):
    draw.text((left, 330 + index * 50), text, font=tag_font, fill=INK_2)
draw.rectangle((left, 296, left + 64, 302), fill=telltale.COLOR_OK)
draw.text((left, 330 + len(lines) * 50 + 34), "Free and open source. Windows 10 and 11.", font=font(26), fill=INK_2)
card.save(os.path.join(DOCS, "social-preview.png"))

app._closing = True
app.destroy()
for name in ("screenshot.png", "states.png", "demo.gif", "social-preview.png"):
    path = os.path.join(DOCS, name)
    print(f"{name:20} {Image.open(path).size} {os.path.getsize(path) // 1024} KB")
