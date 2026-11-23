import cv2
import numpy as np
import pytest

from luftbild_editor import autocrop as A
from luftbild_editor import processing as P
from tests.sample import synthetic_aerial


def archive_photo(angle, seed=0, size=(2000, 1500)):
    """Foto einer Archivale: helles Blatt mit Schrift auf dunklem Tisch, Hand und Stift."""
    w, h = size
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 0.25, np.float32) + rng.normal(0, 0.03, (h, w)).astype(np.float32)
    box = cv2.boxPoints(((w * 0.5, h * 0.5), (w * 0.55, h * 0.53), angle)).astype(np.int32)
    cv2.fillPoly(img, [box], 0.85)
    for y in range(int(h * 0.33), int(h * 0.66), 40):
        cv2.line(img, (int(w * 0.35), y), (int(w * 0.62), y), 0.3, 3)
    cv2.ellipse(img, (int(w * 0.72), int(h * 0.7)), (180, 90), 30, 0, 360, 0.55, -1)
    cv2.line(img, (200, 300), (900, 450), 0.1, 12)
    return np.clip(img, 0, 1), box


@pytest.mark.parametrize("angle", [0, 7, -12, 25])
def test_detect_sheet_straightens_and_crops(angle):
    img, _ = archive_photo(angle)
    doc = P.Document(P.LoadedImage(img, 16, None))
    rotation, crop = A.detect_sheet(doc)
    assert abs(rotation + angle) < 1.5                      # gerade gedreht
    base = P.as_float(doc.prepare(P.Settings(rotation=rotation, crop=crop)).base)
    assert (base > 0.6).mean() > 0.75                       # fast nur Blatt (Rest: Schrift)
    assert (base < 0.2).mean() < 0.05                       # kaum Tisch oder Stift
    # Blatt wird nicht unnötig beschnitten: Ausschnitt ≈ Blattgröße
    assert crop[2] * crop[3] > 0.85 * (2000 * 0.55) * (1500 * 0.53)


def test_detect_sheet_returns_none_without_sheet():
    rng = np.random.default_rng(1)
    img = rng.normal(0.4, 0.05, (800, 1000)).astype(np.float32)
    assert A.detect_sheet(P.Document(P.LoadedImage(np.clip(img, 0, 1), 16, None))) is None


def framed_aerial(dx, dy):
    a = synthetic_aerial(1400, 1300)[60:-60, 60:-60]
    img = np.zeros((1500, 1450), np.float32)
    img[dy:dy + a.shape[0], dx:dx + a.shape[1]] = a
    img[20:60, 300:500] = 0.95                              # Bildnummer im Rahmen
    return img, a.shape


@pytest.mark.parametrize("dx,dy", [(90, 130), (40, 60), (140, 200)])
def test_align_fixed_size_frame_to_content(dx, dy):
    img, (ah, aw) = framed_aerial(dx, dy)
    doc = P.Document(P.LoadedImage(img, 16, None))
    crop = A.align_to_content(doc, 0.0, (1100, 1000))
    assert crop[2:] == (1100, 1000)
    base = doc.prepare(P.Settings(crop=crop)).base
    assert (base > 0.02).mean() > 0.997                     # kein schwarzer Rand im Rahmen
    # mittig im Inhalt
    assert abs(crop[0] + 550 - (dx + aw / 2)) < 30 and abs(crop[1] + 500 - (dy + ah / 2)) < 30


def test_align_largest_frame_without_size():
    img, (ah, aw) = framed_aerial(90, 130)
    doc = P.Document(P.LoadedImage(img, 16, None))
    x, y, w, h = A.align_to_content(doc, 0.0, None)
    assert x >= 90 and y >= 130 and x + w <= 90 + aw and y + h <= 130 + ah
    assert w > aw - 40 and h > ah - 40


def test_align_near_previous_position():
    img, _ = framed_aerial(90, 130)
    doc = P.Document(P.LoadedImage(img, 16, None))
    # kleiner Rahmen: viele Lagen passen – die nächste zur bisherigen Mitte gewinnt
    crop = A.align_to_content(doc, 0.0, (400, 300), near=(400, 400))
    assert abs(crop[0] + 200 - 400) < 20 and abs(crop[1] + 150 - 400) < 20
