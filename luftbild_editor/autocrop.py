"""Automatischer Zuschnitt (läuft komplett lokal, ohne Internet).

- align_to_content: Rahmen fester Größe an den Bildinhalt eines Luftbildes legen –
  der schwarze Scanrahmen mit Messmarken und Beschriftung wird erkannt und gemieden.
- detect_sheet: in einem Foto aus dem Archiv das Blatt (Archivale) finden; Drehung und
  Zuschnitt so wählen, dass vor allem das Blatt zu sehen ist (Hände, Stifte, Tisch weg).
"""
from __future__ import annotations

import cv2
import numpy as np

from . import processing as P
from .geo import rotated_size, rotation_matrix

WORK_SIDE = 1024   # Rechengröße (lange Seite)


def _small_rotated(doc: P.Document, rotation: float):
    """Verkleinertes, gedrehtes Bild und Faktor Rechengröße -> volle Auflösung."""
    small, sc = P._resize_long(doc.raw, WORK_SIDE)
    small = P.as_float(small).astype(np.float32)
    small = P.rotate_image(small, rotation)
    W, H = rotated_size(*doc.size, rotation)
    return small, W / small.shape[1], (W, H)


def content_mask(doc: P.Document, rotation: float = 0.0):
    """True = Bildinhalt (ohne schwarzen Rahmen) in Rechengröße, dazu der Faktor."""
    small, f, canvas = _small_rotated(doc, rotation)
    return P.frame_mask(small), f, canvas


def align_to_content(doc: P.Document, rotation: float, size: tuple[int, int] | None,
                     near: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """Zuschnitt im gedrehten Bild, der möglichst nur Bildinhalt enthält.

    size: feste Rahmengröße (Breite, Höhe) in Pixeln; None = größter Rahmen innerhalb
    des Inhalts. Unter den gleich guten Lagen wird die gewählt, die dem Inhalt mittig
    liegt (bzw. am nächsten an `near`, der bisherigen Rahmenmitte).
    """
    valid, f, (W, H) = content_mask(doc, rotation)
    h, w = valid.shape
    ys, xs = np.nonzero(valid)
    if xs.size == 0:
        return 0, 0, W, H
    if size is None:
        # vom Umriss des Inhalts aus jede Kante nach innen ziehen, bis an keiner Kante
        # mehr Rahmen liegt (größtes Rechteck, das nur Bildinhalt enthält)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        for _ in range(w + h):
            edges = {"t": valid[y0, x0:x1].mean(), "b": valid[y1 - 1, x0:x1].mean(),
                     "l": valid[y0:y1, x0].mean(), "r": valid[y0:y1, x1 - 1].mean()}
            side = min(edges, key=edges.get)
            if edges[side] >= 0.995 or x1 - x0 < 4 or y1 - y0 < 4:
                break
            if side == "t":
                y0 += 1
            elif side == "b":
                y1 -= 1
            elif side == "l":
                x0 += 1
            else:
                x1 -= 1
        crop = (round(x0 * f), round(y0 * f), round((x1 - x0) * f), round((y1 - y0) * f))
        return P.clamp_crop(crop, W, H)

    cw, ch = min(size[0], W), min(size[1], H)
    sw, sh = max(1, min(w, round(cw / f))), max(1, min(h, round(ch / f)))
    # Anteil Inhalt für jede Lage des Rahmens (Summenbild)
    ii = cv2.integral(valid.astype(np.uint8), sdepth=cv2.CV_32S).astype(np.int64)
    inside = (ii[sh:, sw:] - ii[:-sh, sw:] - ii[sh:, :-sw] + ii[:-sh, :-sw])
    best = inside.max()
    good = inside >= best - max(1, 0.002 * sw * sh)
    gy, gx = np.nonzero(good)
    if near is not None:
        tx, ty = near[0] / f - sw / 2, near[1] / f - sh / 2
    else:   # Mitte aller passenden Lagen = möglichst gleicher Abstand zum Rand ringsum
        tx, ty = (gx.min() + gx.max()) / 2, (gy.min() + gy.max()) / 2
    i = np.argmin((gx - tx) ** 2 + (gy - ty) ** 2)
    x, y = round(gx[i] * f), round(gy[i] * f)
    return min(max(0, x), W - cw), min(max(0, y), H - ch), cw, ch


def detect_sheet(doc: P.Document, margin: float = 0.01):
    """Blatt Papier (Archivale) im Foto finden.

    Gibt (rotation, crop) zurück: Drehung in Grad, damit das Blatt gerade liegt, und den
    Zuschnitt im gedrehten Bild. None, wenn kein Blatt sicher erkannt wurde.
    Ansatz: das Blatt ist eine große, helle, zusammenhängende Fläche mit geraden Kanten.
    Hände, Stifte und Tisch heben sich davon ab und fallen beim Öffnen/Schließen der
    Fläche weg. Der Zuschnitt muss nicht pixelgenau sein.
    """
    small, f0, _ = _small_rotated(doc, 0.0)
    h, w = small.shape
    blur = cv2.GaussianBlur(small, (0, 0), max(1.0, max(h, w) / 400))
    u8 = np.clip(blur * 255, 0, 255).astype(np.uint8)
    candidates = []
    # 1) helle Fläche (Otsu), 2) Fläche innerhalb starker Kanten
    t1, bright = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    candidates.append(bright)
    # zweite, höhere Schwelle nur unter den hellen Pixeln: trennt Papier von Händen,
    # die das Blatt berühren (Haut ist meist dunkler als Papier)
    upper = u8[u8 > t1]
    if upper.size > 100:
        t2, _ = cv2.threshold(upper.reshape(-1, 1), 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        for t in (t2, (t1 + t2) / 2):
            candidates.append(((u8 > t) * 255).astype(np.uint8))
    edges = cv2.Canny(u8, 30, 90)
    edges = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    filled = np.zeros_like(edges)
    cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(filled, cnts, -1, 255, -1)
    candidates.append(filled)

    k = max(5, int(max(h, w) * 0.02) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    found = []
    for mask in candidates:
        m = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)    # Stifte, Finger, Ränder weg
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)      # Schrift im Blatt füllen
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if not 0.08 * w * h <= area <= 0.985 * w * h:
                continue
            rect = cv2.minAreaRect(c)
            rw, rh = rect[1]
            if rw < 1 or rh < 1:
                continue
            # wie rechteckig ist die Fläche? (Blatt ≈ 1; Blatt + Hand deutlich weniger)
            fill = area / (rw * rh)
            if fill > 0.6:
                found.append((area, fill, rect))
    if not found:
        return None
    # unter den ausreichend großen Flächen die rechteckigste: eine Hand am Blattrand macht
    # die Fläche größer, aber weniger rechteckig
    biggest = max(a for a, _, _ in found)
    best = max((f, a, r) for a, f, r in found if a >= 0.5 * biggest)
    best = (None, best[2])
    (cx, cy), (rw, rh), angle = best[1]
    # Winkel so wählen, dass die Drehung höchstens ±45° beträgt
    if angle > 45:
        angle -= 90
        rw, rh = rh, rw
    elif angle < -45:
        angle += 90
        rw, rh = rh, rw
    # OpenCV misst im Uhrzeigersinn geneigt; zum Geraderichten entgegen drehen
    rotation = round(-float(angle), 2) if abs(angle) > 0.3 else 0.0
    # Blattecken in Pixel der vollen Auflösung, dann in das gedrehte Bild umrechnen
    box = cv2.boxPoints(((cx, cy), (rw, rh), angle)) * f0
    W, H = doc.size
    m = rotation_matrix(W, H, rotation)
    pts = np.c_[box, np.ones(4)] @ m.T
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    # ein wenig nach innen, damit kein Tisch am Rand stehen bleibt
    dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
    crop = (round(x0 + dx), round(y0 + dy), round(x1 - x0 - 2 * dx), round(y1 - y0 - 2 * dy))
    return rotation, P.clamp_crop(crop, *rotated_size(W, H, rotation))
