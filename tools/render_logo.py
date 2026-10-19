"""Zeichnet das PZU-Logo aus der Shape-Datei (tools/logo/pzulogo.shp) als PNG.

Ergebnis: luftbild_editor/resources/logo.png (1024 px, scharf in jeder Größe).
Benötigt nur numpy, OpenCV und pyshp (pip install pyshp).
"""
import os

import cv2
import numpy as np
import shapefile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "luftbild_editor", "resources", "logo.png")
SIZE, PAD = 1024, 8
# FARBE-Spalte: 0 = weiße Felder, 1 = schwarzes Raster, 2 = PZU-Türkis (#009D88)
COLORS = {0: (255, 255, 255, 255), 1: (40, 40, 40, 255), 2: (136, 157, 0, 255)}

r = shapefile.Reader(os.path.join(HERE, "logo", "pzulogo"))
xmin, ymin, xmax, ymax = r.bbox
scale = (SIZE - 2 * PAD) / max(xmax - xmin, ymax - ymin)
# 4-fach überabgetastet zeichnen und verkleinern = glatte Kanten
k = 4
img = np.zeros((SIZE * k, SIZE * k, 4), np.uint8)
items = []
for shape, rec in zip(r.shapes(), r.records()):
    parts = list(shape.parts) + [len(shape.points)]
    rings = [np.array([[((x - xmin) * scale + PAD) * k, ((ymax - y) * scale + PAD) * k]
                       for x, y in shape.points[parts[i]:parts[i + 1]]], np.int32)
             for i in range(len(parts) - 1)]
    items.append((rec["AREA"], rec["FARBE"], rings))
for _, color, rings in sorted(items, key=lambda t: -t[0]):  # große Flächen zuerst
    cv2.fillPoly(img, rings, COLORS.get(color, (0, 0, 255, 255)))
img = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
cv2.imwrite(OUT, img)
print("Logo gespeichert:", os.path.abspath(OUT))
