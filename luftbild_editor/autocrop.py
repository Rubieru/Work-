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


def text_mask(small: np.ndarray) -> np.ndarray:
    """Schrift finden (ohne Texterkennung): feine, dunkle Striche auf hellerem Grund.

    Black-Hat hebt dunkle Details hervor, die schmaler als das Strukturelement sind.
    Danach bleiben nur Flecken in Schriftgröße übrig – große dunkle Flächen (Tisch,
    Hände, Schatten) und einzelne Staubpunkte fallen weg.
    """
    h, w = small.shape
    L = max(h, w)
    u8 = np.clip(small * 255, 0, 255).astype(np.uint8)
    k = max(9, int(L / 60) | 1)
    bh = cv2.morphologyEx(u8, cv2.MORPH_BLACKHAT,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    thr = max(20.0, float(np.percentile(bh, 99)) * 0.35)
    m = (bh > thr).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros(n, bool)
    if n > 1:
        bw, bhh, area = st[1:, 2], st[1:, 3], st[1:, 4]
        size = np.maximum(bw, bhh)
        ok = (size >= max(2, L * 0.002)) & (size <= L * 0.06) & (area >= 3) \
            & (area <= 0.9 * bw * bhh)
        keep[1:] = ok
    return keep[lab]


def _text_box(tm: np.ndarray):
    """Bereich mit Schrift (robust gegen einzelne Fehlstellen) oder None."""
    ys, xs = np.nonzero(tm)
    if xs.size < max(200, tm.size * 0.0005):
        return None
    x0, x1 = np.percentile(xs, [0.5, 99.5])
    y0, y1 = np.percentile(ys, [0.5, 99.5])
    return float(x0), float(y0), float(x1), float(y1), xs, ys


def _paper_mask(doc: P.Document, small: np.ndarray, tmask: np.ndarray, text):
    """Maske der Pixel in Papierfarbe (Lab bei Farbbildern, sonst Helligkeit)."""
    h, w = small.shape
    x0, y0, x1, y1 = (int(v) for v in text[:4])
    if doc.is_color:
        col = P.as_float(cv2.resize(doc.color, (w, h), interpolation=cv2.INTER_AREA))
        feat = cv2.cvtColor(np.ascontiguousarray(col, dtype=np.float32), cv2.COLOR_RGB2Lab)
        feat = cv2.GaussianBlur(feat, (0, 0), max(1.0, max(h, w) / 400))
    else:
        feat = cv2.GaussianBlur(small, (0, 0), max(1.0, max(h, w) / 400))[..., None] * 100
    # Papier = Umgebung der Schrift ohne die Schrift selbst
    near = np.zeros((h, w), np.uint8)
    near[y0:y1 + 1, x0:x1 + 1] = 1
    k = max(3, int(max(h, w) / 150) | 1)
    near &= cv2.dilate(tmask.astype(np.uint8), np.ones((k, k), np.uint8)) == 0
    sample = feat[near.astype(bool)]
    if sample.shape[0] < 200:
        return None
    med = np.median(sample, axis=0)
    dist = np.sqrt(((feat - med) ** 2).sum(axis=2))
    # typische Abweichung (Median) – robust gegen Schriftreste am Rand der Stichprobe
    spread = np.median(np.sqrt(((sample - med) ** 2).sum(axis=1)))
    return ((dist < max(6.0, 4.0 * spread)) * 255).astype(np.uint8)


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

    # 3) Papierfarbe: rund um die Schrift gemessen; zum Blatt gehört, was diese Farbe hat
    #    (trennt Papier von Händen und Tisch, auch wenn sie gleich hell sind)
    tmask = text_mask(small)
    text = _text_box(tmask)
    if text is not None:
        paper = _paper_mask(doc, small, tmask, text)
        if paper is not None:
            candidates.append(paper)

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
    # Schrift gibt es nur auf dem Dokument: der Blatt-Rahmen muss sie enthalten
    if text is not None:
        xs, ys = text[4], text[5]
        step = max(1, xs.size // 3000)
        pts = [(float(x), float(y)) for x, y in zip(xs[::step], ys[::step])]

        def holds_text(rect):
            box = cv2.boxPoints(rect).astype(np.float32)
            return np.mean([cv2.pointPolygonTest(box, p, False) >= 0 for p in pts]) >= 0.9
        found = [f for f in found if holds_text(f[2])]
    best = None
    if found:
        # unter den ausreichend großen Flächen die rechteckigste: eine Hand am Blattrand
        # macht die Fläche größer, aber weniger rechteckig
        biggest = max(a for a, _, _ in found)
        best = (None, max((f, a, r) for a, f, r in found if a >= 0.5 * biggest)[2])
    if best is None:
        if text is None:
            return None
        return _crop_from_text(doc, text, f0, w, h)
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


def _crop_from_text(doc: P.Document, text, f0: float, w: int, h: int):
    """Zuschnitt um die Schrift herum. Füllt sie fast das ganze Foto (Blatt ohne
    sichtbaren Rand), bleibt das ganze Bild stehen."""
    x0, y0, x1, y1 = text[:4]
    pad_x, pad_y = 0.05 * w, 0.05 * h
    x0, y0 = max(0.0, x0 - pad_x), max(0.0, y0 - pad_y)
    x1, y1 = min(float(w), x1 + pad_x), min(float(h), y1 + pad_y)
    W, H = doc.size
    if (x1 - x0) * (y1 - y0) >= 0.8 * w * h:
        return 0.0, (0, 0, W, H)
    crop = (round(x0 * f0), round(y0 * f0), round((x1 - x0) * f0), round((y1 - y0) * f0))
    return 0.0, P.clamp_crop(crop, W, H)
