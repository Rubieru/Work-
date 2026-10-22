"""Georeferenzierung erhalten (GeoTIFF-Tags und World-Files).

Intern wird die Lage als affine Abbildung G (2x3) gespeichert, die
Pixel-Eckkoordinaten (x, y) auf Kartenkoordinaten abbildet:
    X = G[0,0]*x + G[0,1]*y + G[0,2]
    Y = G[1,0]*x + G[1,1]*y + G[1,2]
Beim Zuschneiden und Drehen wird G passend umgerechnet, damit das
Ergebnis im GIS an der richtigen Stelle liegt.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field

import cv2
import numpy as np

try:
    import tifffile
except ImportError:  # pragma: no cover
    tifffile = None

TAG_PIXEL_SCALE = 33550
TAG_TIEPOINT = 33922
TAG_TRANSFORMATION = 34264
# unverändert übernommene GeoTIFF-/GDAL-Tags (Koordinatensystem, NoData, Metadaten)
COPY_TAGS = (34735, 34736, 34737, 42112, 42113)

WORLD_EXTS = {".tif": (".tfw", ".tifw", ".wld"), ".tiff": (".tfw", ".tiffw", ".wld"),
              ".jpg": (".jgw", ".jpgw", ".wld"), ".jpeg": (".jgw", ".jpegw", ".wld"),
              ".png": (".pgw", ".pngw", ".wld")}


@dataclass
class Geo:
    affine: np.ndarray                      # 2x3, Pixelecken -> Karte
    tags: list = field(default_factory=list)  # (code, dtype, count, value) zum Kopieren
    prj: str | None = None                  # Pfad einer .prj-Datei
    from_geotiff: bool = False


def _sidecar(path: str, exts) -> str | None:
    stem = os.path.splitext(path)[0]
    for e in exts:
        for cand in (stem + e, stem + e.upper()):
            if os.path.exists(cand):
                return cand
    return None


def read_geo(path: str, page=None) -> Geo | None:
    """Liest die Georeferenz aus GeoTIFF-Tags (page) oder einem World-File."""
    geo = None
    if page is not None:
        tags = page.tags
        affine = None
        t = tags.get(TAG_TRANSFORMATION)
        if t is not None:
            m = np.array(t.value, dtype=np.float64).reshape(4, 4)
            affine = np.array([[m[0, 0], m[0, 1], m[0, 3]], [m[1, 0], m[1, 1], m[1, 3]]])
        else:
            sc, tp = tags.get(TAG_PIXEL_SCALE), tags.get(TAG_TIEPOINT)
            if sc is not None and tp is not None:
                sx, sy = sc.value[0], sc.value[1]
                i, j, _, x, y, _ = tp.value[:6]
                affine = np.array([[sx, 0.0, x - i * sx], [0.0, -sy, y + j * sy]])
        if affine is not None:
            copy = []
            for code in COPY_TAGS:
                tag = tags.get(code)
                if tag is not None:
                    copy.append((code, int(tag.dtype), tag.count, tag.value))
            geo = Geo(affine, copy, from_geotiff=True)
    if geo is None:
        ext = os.path.splitext(path)[1].lower()
        wf = _sidecar(path, WORLD_EXTS.get(ext, (".wld",)))
        if wf:
            try:
                with open(wf, encoding="ascii", errors="ignore") as f:
                    a, d, b, e, c, fy = (float(v) for v in f.read().split()[:6])
                # World-File bezieht sich auf die Pixelmitte -> auf Ecke umrechnen
                geo = Geo(np.array([[a, b, c - 0.5 * a - 0.5 * b],
                                    [d, e, fy - 0.5 * d - 0.5 * e]]))
            except (OSError, ValueError):
                geo = None
    if geo is not None:
        prj = os.path.splitext(path)[0] + ".prj"
        geo.prj = prj if os.path.exists(prj) else None
    return geo


def normalize_angle(angle: float) -> float:
    """Winkel auf -180..180 Grad."""
    a = (float(angle) + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def quarter_turns(angle: float) -> int | None:
    """Anzahl Vierteldrehungen im Uhrzeigersinn, wenn angle ein Vielfaches von 90° ist."""
    q = normalize_angle(angle) / 90.0
    k = round(q)
    return k % 4 if abs(q - k) < 1e-9 else None


def rotated_size(w: int, h: int, angle: float) -> tuple[int, int]:
    """Größe der Leinwand nach der Drehung: das ganze Bild bleibt sichtbar."""
    k = quarter_turns(angle)
    if k is not None:
        return (h, w) if k % 2 else (w, h)
    r = np.deg2rad(angle)
    c, s = abs(np.cos(r)), abs(np.sin(r))
    return int(np.ceil(w * c + h * s - 1e-6)), int(np.ceil(w * s + h * c - 1e-6))


def rotation_matrix(w: int, h: int, angle: float) -> np.ndarray:
    """2x3-Matrix Quellpixel -> Pixel der gedrehten Leinwand (Mitte auf Mitte)."""
    nw, nh = rotated_size(w, h, angle)
    m = cv2.getRotationMatrix2D(((w - 1) / 2.0, (h - 1) / 2.0), -angle, 1.0)
    m[0, 2] += (nw - w) / 2.0
    m[1, 2] += (nh - h) / 2.0
    return m


def output_affine(geo: Geo, size: tuple[int, int], rotation: float,
                  crop: tuple[int, int, int, int]) -> np.ndarray:
    """Abbildung des bearbeiteten Bildes (gedreht + zugeschnitten).

    size ist die Größe des ungedrehten Originals.
    """
    w, h = size
    x0, y0 = crop[0], crop[1]
    # Ausgabe-Ecke -> gedrehte Ecke
    T = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], dtype=np.float64)
    if abs(normalize_angle(rotation)) > 1e-9:
        m = rotation_matrix(w, h, rotation)
        inv = np.vstack([cv2.invertAffineTransform(m), [0, 0, 1]])
        to_center = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]], dtype=np.float64)
        to_corner = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]], dtype=np.float64)
        T = to_corner @ inv @ to_center @ T
    G = np.vstack([geo.affine, [0, 0, 1]])
    return (G @ T)[:2]


def geotiff_tags(geo: Geo, affine: np.ndarray) -> list:
    """extratags für tifffile.imwrite."""
    a, b, c = affine[0]
    d, e, f = affine[1]
    if abs(b) < 1e-12 and abs(d) < 1e-12:
        tags = [(TAG_PIXEL_SCALE, 12, 3, (a, -e, 0.0), True),
                (TAG_TIEPOINT, 12, 6, (0.0, 0.0, 0.0, c, f, 0.0), True)]
    else:
        m = (a, b, 0.0, c, d, e, 0.0, f, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        tags = [(TAG_TRANSFORMATION, 12, 16, m, True)]
    for code, dtype, count, value in geo.tags:
        if dtype == 2:  # ASCII: Anzahl ermittelt tifffile
            tags.append((code, "s", 0, value, True))
        else:
            tags.append((code, dtype, count, value, True))
    return tags


def write_sidecars(geo: Geo, affine: np.ndarray, out_path: str, world_file: bool) -> None:
    stem, ext = os.path.splitext(out_path)
    if world_file:
        a, b, c = affine[0]
        d, e, f = affine[1]
        cx = c + 0.5 * a + 0.5 * b
        cy = f + 0.5 * d + 0.5 * e
        wext = WORLD_EXTS.get(ext.lower(), (".wld",))[0]
        with open(stem + wext, "w", encoding="ascii") as fh:
            fh.write("\n".join(f"{v:.10f}" for v in (a, d, b, e, cx, cy)) + "\n")
    if geo.prj and os.path.abspath(geo.prj) != os.path.abspath(stem + ".prj"):
        shutil.copyfile(geo.prj, stem + ".prj")
