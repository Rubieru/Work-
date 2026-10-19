"""Erzeugt luftbild_editor/resources/logo.ico (Symbol der .exe).

Ab 48 px das vollständige PZU-Logo. Für 16–32 px (Explorer-Liste, Taskleiste) eine
vereinfachte Fassung: türkises Feld mit dem 4×4-Raster des Logos – das feine Original
wäre in dieser Größe nur ein grauer Fleck.
"""
import os
import sys

from PIL import Image, ImageDraw

here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "luftbild_editor",
                    "resources")
src = os.path.join(here, "logo.png")
if not os.path.exists(src):
    print("Kein logo.png vorhanden – Standardsymbol wird verwendet.")
    sys.exit(0)

TEAL = (0, 157, 136, 255)


def full(size):
    img = Image.open(src).convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return square.resize((size, size), Image.LANCZOS)


def small(size):
    k = 8  # überabgetastet zeichnen
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=s // 8, fill=TEAL)
    line = max(k, s // 16)
    for i in (1, 2, 3):
        p = i * s // 4
        d.rectangle([p - line // 2, s // 10, p + line // 2, s - s // 10], fill="white")
        d.rectangle([s // 10, p - line // 2, s - s // 10, p + line // 2], fill="white")
    return img.resize((size, size), Image.LANCZOS)


sizes = [16, 24, 32, 48, 64, 128, 256]
images = [small(n) if n <= 32 else full(n) for n in sizes]
images[-1].save(os.path.join(here, "logo.ico"), sizes=[(n, n) for n in sizes],
                append_images=images[:-1])
print("logo.ico erzeugt")
