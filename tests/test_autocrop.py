import cv2
import numpy as np
import pytest

from luftbild_editor import autocrop as A
from luftbild_editor import processing as P
from tests.sample import synthetic_aerial


def _write(img, positions, text, scale, value, thickness=2):
    """Text in ein float-Bild schreiben (OpenCV zeichnet Text nur in 8-Bit-Bilder)."""
    canvas = np.zeros(img.shape, np.uint8)
    for x, y in positions:
        cv2.putText(canvas, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, 255, thickness)
    out = img.copy()
    out[canvas > 0] = value
    return out


def archive_photo(angle, seed=0, size=(2000, 1500)):
    """Foto einer Archivale: helles Blatt mit Schrift auf dunklem Tisch, Hand und Stift."""
    w, h = size
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 0.25, np.float32) + rng.normal(0, 0.03, (h, w)).astype(np.float32)
    box = cv2.boxPoints(((w * 0.5, h * 0.5), (w * 0.55, h * 0.53), angle)).astype(np.int32)
    cv2.fillPoly(img, [box], 0.85)
    # Schrift liegt auf dem Blatt und ist mit ihm gedreht (wie in einem echten Foto)
    canvas = np.zeros((h, w), np.uint8)
    for y in range(int(h * 0.33), int(h * 0.66), 40):
        cv2.putText(canvas, "Bericht 1944 Blatt", (int(w * 0.35), y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6 * w / 2000 * 1.6, 255, 2)
    m = cv2.getRotationMatrix2D((w * 0.5, h * 0.5), -angle, 1.0)
    canvas = cv2.warpAffine(canvas, m, (w, h))
    img[canvas > 127] = 0.25
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


def page_without_border(w=1600, h=1200, seed=3):
    """Blatt füllt das ganze Foto: kein Tisch, nur Papier mit Schrift (leicht ungleich hell)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = (0.82 + 0.06 * xx / w - 0.04 * yy / h).astype(np.float32)
    img += rng.normal(0, 0.01, (h, w)).astype(np.float32)
    pos = [(x, y) for y in range(int(h * 0.08), int(h * 0.92), 34)
           for x in range(int(w * 0.07), int(w * 0.9), 60) if rng.random() < 0.8]
    img = _write(img, pos, "abc", 0.6, 0.15)
    # Fleck/Schatten am Rand (keine Schrift)
    cv2.ellipse(img, (int(w * 0.95), int(h * 0.5)), (60, 200), 0, 0, 360, 0.6, -1)
    return np.clip(img, 0, 1)


def test_sheet_filling_photo_keeps_whole_image():
    doc = P.Document(P.LoadedImage(page_without_border(), 16, None))
    rotation, crop = A.detect_sheet(doc)
    assert rotation == 0.0 and crop == (0, 0, 1600, 1200)


def test_text_region_used_when_no_sheet_edge():
    # Text nur in der linken Hälfte, Papier ohne Kante -> Zuschnitt umfasst die Schrift
    img = np.full((1200, 1600), 0.85, np.float32)
    img = _write(img, [(120, y) for y in range(150, 1050, 34)], "Archivale 1944", 0.7, 0.15)
    doc = P.Document(P.LoadedImage(img, 16, None))
    rotation, (x, y, w, h) = A.detect_sheet(doc)
    assert x <= 120 and y <= 130 and x + w >= 300 and y + h >= 1040
    assert x + w < 1600 and w * h < 0.8 * 1600 * 1200


def test_text_mask_ignores_hand_and_table():
    img, _ = archive_photo(0)
    tm = A.text_mask(P._resize_long(img, A.WORK_SIDE)[0])
    ys, xs = np.nonzero(tm)
    # Schrift liegt im Blatt (Mitte), nicht bei Hand (rechts unten) oder Stift (links oben)
    assert xs.size > 200
    assert np.mean((xs > 0.25 * tm.shape[1]) & (xs < 0.75 * tm.shape[1])) > 0.9


def wood_table_photo(seed=0, w=1600, h=730, sheet=(560, 15, 1080, 715), tilt=1.0):
    """Wie echte Archivfotos: weißes Blatt mit Schrift auf hellem, gemasertem Holztisch
    (ähnlich hell wie Papier, aber orange), dunkler Stuhl rechts, Schuh links."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    grain = np.sin(xx / 7.0 + 3 * np.sin(yy / 90.0) + rng.normal(0, 0.3)) * 0.5 + 0.5
    grain = cv2.GaussianBlur(grain, (0, 0), 1.2)
    base = 0.80 + 0.12 * grain + rng.normal(0, 0.02, (h, w)).astype(np.float32)
    rgb = np.dstack([base * 1.0, base * 0.70, base * 0.35])            # orange Holz
    x0, y0, x1, y1 = sheet
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    box = cv2.boxPoints(((cx, cy), (x1 - x0, y1 - y0), tilt)).astype(np.int32)
    paper = np.zeros((h, w), np.uint8)
    cv2.fillPoly(paper, [box], 255)
    rgb[paper > 0] = (0.93, 0.93, 0.95)
    text = np.zeros((h, w), np.uint8)
    for y in range(y0 + 60, y1 - 40, 28):
        cv2.putText(text, "Bericht an den Landrat 1944", (x0 + 40, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, 255, 1)
    rgb[text > 0] = (0.2, 0.2, 0.25)
    rgb[:, int(w * 0.93):] = (0.12, 0.13, 0.16)                       # Stuhl
    cv2.circle(rgb, (0, h // 2), 80, (0.1, 0.1, 0.1), -1)              # Schuh
    return np.clip(rgb, 0, 1)


@pytest.mark.parametrize("gray", [False, True])
def test_sheet_on_bright_wood_table(gray):
    rgb = wood_table_photo()
    u8 = P.to_uint(rgb, 8)
    g = cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY)
    doc = P.Document(P.LoadedImage(g, 8, None, None, None if gray else u8))
    rotation, (x, y, w, h) = A.detect_sheet(doc)
    # Ausschnitt ≈ Blatt (560..1080 x 15..715), kein Holz
    assert abs(x - 560) < 30 and abs(x + w - 1080) < 30
    assert y < 40 and y + h > 690
    base = P.as_float(doc.prepare(P.Settings(rotation=rotation, crop=(x, y, w, h))).base)
    assert (base > 0.8).mean() > 0.85


def test_aerial_alignment_leaves_archive_photos_alone():
    # dunkler Stuhl nur an einer Seite ist kein Scanrahmen -> nicht ausrichten
    u8 = P.to_uint(wood_table_photo(), 8)
    doc = P.Document(P.LoadedImage(cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY), 8, None, None, u8))
    assert A.align_to_content(doc, 0.0, None) is None
    assert A.align_to_content(doc, 0.0, (500, 400)) is None
    # Luftbild mit Scanrahmen weiterhin
    img, _ = framed_aerial(90, 130)
    assert A.align_to_content(P.Document(P.LoadedImage(img, 16, None)), 0.0, None) is not None


# ---------------------------------------------------------------- Leserichtung

FONT_FILES = ["/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              # Windows
              "C:/Windows/Fonts/cour.ttf", "C:/Windows/Fonts/consola.ttf",
              "C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/arial.ttf"]


def text_page(seed=0, w=1240, h=1754, landscape=False):
    """Lesbare Seite mit deutscher Schrift (Brief oder Tabelle), aufrecht."""
    import os
    import random
    from PIL import Image, ImageDraw, ImageFont
    fonts = [f for f in FONT_FILES if os.path.exists(f)]
    if not fonts:
        pytest.skip("keine TrueType-Schrift verfügbar")
    rnd = random.Random(seed)
    if landscape:
        w, h = h, w
    words = ("der die das und in den von zu mit Bürgermeister Landrat Kreises Fliegeralarm "
             "Luftschutz Nachweisung Monat Gemeinde Bericht Anlage Juni 1943 Zahl Std. Min. "
             "Angriffe Bomben gefallen Gebäude gemeldet worden erfolgte").split()
    img = Image.new("L", (w, h), 236)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(rnd.choice(fonts), rnd.choice([22, 26, 30]))
    y = rnd.randint(90, 250)
    while y < h - 150:
        line = " ".join(rnd.choice(words) for _ in range(rnd.randint(3, 8)))
        d.text((rnd.choice([110, 110, 110, 300]), y), line, font=font, fill=40)
        y += int(font.size * rnd.uniform(1.5, 2.1))
    a = np.array(img, np.float32) + np.random.default_rng(seed).normal(0, 4, (h, w))
    return np.clip(a, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_orientation_turns_text_upright(seed, k):
    page = text_page(seed, landscape=seed % 2 == 1)
    turned = np.ascontiguousarray(np.rot90(page, -k))      # um k*90° im UZS verdreht
    got = A.detect_orientation(P.Document(P.LoadedImage(turned, 8, None)))
    want = (-k * 90) % 360
    assert got is not None and abs(((got - want + 180) % 360) - 180) <= 1.0


def test_orientation_corrects_small_skew():
    page = text_page(3)
    h, w = page.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 5, 1)       # 5° gegen den Uhrzeigersinn
    tilted = cv2.warpAffine(page, m, (w, h), borderValue=236)
    got = A.detect_orientation(P.Document(P.LoadedImage(tilted, 8, None)))
    assert abs(got - 5) <= 1.0                               # 5° im Uhrzeigersinn zurück


def test_auto_orient_on_photo_measures_on_sheet():
    # Blatt mit Schrift auf Holztisch, Foto um 90° verdreht: Ergebnis aufrecht und zugeschnitten
    rgb = wood_table_photo()
    page = text_page(1, w=500, h=690)
    x0, y0 = 560, 20
    for c in range(3):
        rgb[y0:y0 + 690, x0:x0 + 500, c] = page / 255.0
    u8 = np.ascontiguousarray(np.rot90(P.to_uint(rgb, 8), -1))
    doc = P.Document(P.LoadedImage(cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY), 8, None, None, u8))
    rotation, crop = A.auto_orient(doc)
    assert abs(((rotation - 270 + 180) % 360) - 180) <= 2.0  # 90° zurück
    base = P.as_float(doc.prepare(P.Settings(rotation=rotation, crop=crop)).base)
    assert base.shape[0] > base.shape[1]                      # Blatt steht wieder hochkant
    assert (base > 0.6).mean() > 0.8
