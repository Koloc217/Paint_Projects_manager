#!/usr/bin/env python3
"""Draws the app icons (static/icons/*.png): a rounded "PH" tile (Project Hub) with an amber dot.

    python tools/make_icons.py            # rewrites every icon
    python tools/make_icons.py --text AB  # other letters, e.g. your own build

Needs Pillow and a bold sans font (DejaVu Sans Bold is looked up in the usual places on Linux, Windows and macOS).
The look: dark background #0f172a, an indigo gradient tile, white letters, an amber dot in the corner.
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "static", "icons")
BG = (15, 23, 42)
TOP, BOTTOM = (99, 102, 241), (79, 70, 229)
DOT, RING = (251, 191, 36), BG
SCALE = 4                                    # drawn 4x larger and shrunk: smooth edges

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
]

# name -> (size, space around the tile as a share of the size, transparent background)
ICONS = {
    "icon-512.png": (512, 0.10, False),
    "icon-192.png": (192, 0.10, False),
    "icon-maskable-512.png": (512, 0.20, False),    # the system crops it to a circle/squircle: keep the tile inside
    "apple-touch-icon.png": (180, 0.10, False),
    "favicon-32.png": (32, 0.03, True),
}


def find_font(size):
    for path in FONTS:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    sys.exit("No bold font found: add a path of one to FONTS in tools/make_icons.py")


def draw_icon(size, margin, transparent, text):
    big = size * SCALE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0) if transparent else BG + (255,))
    side = round(big * (1 - 2 * margin))
    left = round(big * margin)
    # vertical gradient clipped to a rounded square
    tile = Image.new("RGBA", (side, side))
    px = ImageDraw.Draw(tile)
    for y in range(side):
        t = y / max(1, side - 1)
        px.line([(0, y), (side, y)], fill=tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, side - 1, side - 1], radius=round(side * 0.22), fill=255)
    img.paste(tile, (left, left), mask)

    draw = ImageDraw.Draw(img)
    font = find_font(round(side * 0.49))
    box = draw.textbbox((0, 0), text, font=font)
    cx, cy = left + side / 2, left + side / 2 + side * 0.02
    draw.text((cx - (box[0] + box[2]) / 2, cy - (box[1] + box[3]) / 2), text, font=font, fill="white")

    if size >= 64:                                           # too small to read at favicon size
        dx, dy, r = left + side - side * 0.167, left + side * 0.169, side * 0.08
        ring = side * 0.02
        draw.ellipse([dx - r - ring, dy - r - ring, dx + r + ring, dy + r + ring], fill=RING)
        draw.ellipse([dx - r, dy - r, dx + r, dy + r], fill=DOT)
    return img.resize((size, size), Image.LANCZOS)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--text", default="PH", help="letters on the tile (1-3), default PH")
    text = parser.parse_args().text
    os.makedirs(OUT, exist_ok=True)
    for name, (size, margin, transparent) in ICONS.items():
        img = draw_icon(size, margin, transparent, text)
        if name == "apple-touch-icon.png":
            img = img.convert("RGB")                          # iOS does not like transparency
        img.save(os.path.join(OUT, name), optimize=True)
        print(f"{name}: {size}x{size}")


if __name__ == "__main__":
    main()
