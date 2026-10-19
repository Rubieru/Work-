"""Erzeugt luftbild_editor/resources/logo.ico aus logo.png (für das .exe-Symbol)."""
import os
import sys

from PIL import Image

here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "luftbild_editor",
                    "resources")
src = os.path.join(here, "logo.png")
if not os.path.exists(src):
    print("Kein logo.png vorhanden – Standardsymbol wird verwendet.")
    sys.exit(0)
img = Image.open(src).convert("RGBA")
side = max(img.size)  # quadratisch machen, Logo mittig
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
square.save(os.path.join(here, "logo.ico"),
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("logo.ico erzeugt")
