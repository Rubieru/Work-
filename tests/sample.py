"""Künstliches, flaues Luftbild für Tests (Felder, Wege, Korn, Vignettierung)."""
import cv2
import numpy as np


def synthetic_aerial(w=3000, h=2200, seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w), np.float32)
    # Felder als zufällige Polygone mit unterschiedlicher Helligkeit
    for _ in range(140):
        cx, cy = rng.integers(0, w), rng.integers(0, h)
        pts = np.array([[cx + rng.integers(-350, 350), cy + rng.integers(-250, 250)]
                        for _ in range(4)], np.int32)
        cv2.fillPoly(img, [pts], float(rng.uniform(0.2, 0.8)))
    # Feldstruktur (Ackerfurchen)
    yy, xx = np.mgrid[0:h, 0:w]
    img += 0.03 * np.sin((xx + yy) / 3.0).astype(np.float32)
    # Wege und Gebäude
    for _ in range(14):
        p1 = tuple(int(v) for v in (rng.integers(0, w), rng.integers(0, h)))
        p2 = tuple(int(v) for v in (rng.integers(0, w), rng.integers(0, h)))
        cv2.line(img, p1, p2, 0.95, int(rng.integers(3, 9)))
    for _ in range(60):
        x, y = int(rng.integers(0, w - 40)), int(rng.integers(0, h - 40))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(8, 30)), y + int(rng.integers(8, 30))),
                      0.1, -1)
    img = cv2.GaussianBlur(img, (0, 0), 2.2)              # Unschärfe
    r = np.hypot((xx - w / 2) / w, (yy - h / 2) / h)
    img *= (1.0 - 0.9 * r ** 2).astype(np.float32)        # Vignettierung
    img = 0.3 + 0.35 * img                                # flau
    img += rng.normal(0, 0.015, img.shape).astype(np.float32)  # Korn
    img = np.clip(img, 0, 1)
    # schwarzer Scanrand
    img[:40, :] = img[-40:, :] = 0.02
    img[:, :40] = img[:, -40:] = 0.02
    return img
