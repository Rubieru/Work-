"""Bildverarbeitung für historische Luftbilder (Graustufen).

Alle Operationen arbeiten auf float32-Bildern im Bereich 0..1.

Wichtig für die Live-Vorschau: Jeder Filter ist entweder lokal (wirkt nur
in einer kleinen Umgebung eines Pixels) oder nutzt global vorab berechnete
Werte (Histogramm-Grenzen, Ausleuchtungskarte). Dadurch sieht die
verkleinerte Vorschau, ein vergrößerter Ausschnitt und der gekachelte
Export in voller Auflösung gleich aus.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import asdict, dataclass, fields, replace

import cv2
import numpy as np

try:  # tifffile liest/schreibt große und 16-Bit-TIFFs inkl. DPI zuverlässig
    import tifffile
except ImportError:  # pragma: no cover - optional
    tifffile = None


# --------------------------------------------------------------------------
# Einstellungen
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    # Geometrie
    rotation: float = 0.0          # Grad, positiv = im Uhrzeigersinn
    crop: tuple | None = None      # (x, y, w, h) in Pixeln des gedrehten Bildes

    # Bildfehler
    invert: bool = False           # Negativ umkehren
    flatten: float = 0.0           # Ausleuchtung ausgleichen 0..1
    denoise: float = 0.0           # Filmkorn reduzieren 0..1

    # Kontrast
    stretch: bool = False          # Histogrammstreckung an/aus
    clip_black: float = 0.5        # % der dunkelsten Pixel, die schwarz werden
    clip_white: float = 0.5        # % der hellsten Pixel, die weiß werden
    midtones: float = 0.0          # -1 (dunkler) .. +1 (heller)
    clarity: float = 0.0           # lokaler Kontrast 0..1

    # Schärfen (Unscharf maskieren)
    sharpen_amount: float = 0.0    # 0..3 (0..300 %)
    sharpen_radius: float = 1.0    # Pixel im Originalbild
    sharpen_threshold: float = 0.0 # 0..0.1 Schwelle gegen Kornverstärkung


GEOMETRY_FIELDS = ("rotation", "crop")
TONAL_FIELDS = tuple(f.name for f in fields(Settings) if f.name not in GEOMETRY_FIELDS)

# Radius des lokalen Kontrasts relativ zur langen Bildseite
CLARITY_SIGMA_REL = 0.015


def tonal_dict(s: Settings) -> dict:
    return {k: getattr(s, k) for k in TONAL_FIELDS}


def with_tonal(s: Settings, values: dict) -> Settings:
    clean = {k: v for k, v in values.items() if k in TONAL_FIELDS}
    defaults = tonal_dict(Settings())
    defaults.update(clean)
    return replace(s, **defaults)


def neutral(s: Settings) -> Settings:
    """Nur die Geometrie behalten (für den Vorher-Vergleich)."""
    return Settings(rotation=s.rotation, crop=s.crop)


def settings_to_json(s: Settings) -> dict:
    d = asdict(s)
    d["crop"] = list(s.crop) if s.crop else None
    return d


def settings_from_json(d: dict) -> Settings:
    valid = {f.name for f in fields(Settings)}
    d = {k: v for k, v in d.items() if k in valid}
    if d.get("crop"):
        d["crop"] = tuple(int(v) for v in d["crop"])
    return Settings(**d)


# --------------------------------------------------------------------------
# Laden / Speichern
# --------------------------------------------------------------------------

@dataclass
class LoadedImage:
    data: np.ndarray   # float32, 0..1, 2D
    bits: int          # 8 oder 16 (Herkunft)
    dpi: float | None


def _to_gray_float(arr: np.ndarray, rgb_order: bool) -> tuple[np.ndarray, int]:
    arr = np.asarray(arr)
    # Mehrseitige TIFFs: erste Seite
    while arr.ndim > 3:
        arr = arr[0]
    if arr.ndim == 3:
        if arr.shape[2] == 1:
            arr = arr[:, :, 0]
        else:
            if arr.shape[2] == 4:
                arr = arr[:, :, :3]
            code = cv2.COLOR_RGB2GRAY if rgb_order else cv2.COLOR_BGR2GRAY
            if arr.dtype not in (np.uint8, np.uint16, np.float32):
                arr = arr.astype(np.float32)
            arr = cv2.cvtColor(np.ascontiguousarray(arr), code)
    if arr.dtype == np.uint8:
        return arr.astype(np.float32) / 255.0, 8
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 65535.0, 16
    if arr.dtype == bool:
        return arr.astype(np.float32), 8
    arr = arr.astype(np.float32)
    if np.issubdtype(arr.dtype, np.floating) and arr.max(initial=0) <= 1.0:
        return np.clip(arr, 0, 1), 16
    mx = float(arr.max(initial=1)) or 1.0
    return np.clip(arr / mx, 0, 1).astype(np.float32), 16


def load_image(path: str) -> LoadedImage:
    ext = os.path.splitext(path)[1].lower()
    if tifffile is not None and ext in (".tif", ".tiff"):
        try:
            with tifffile.TiffFile(path) as tf:
                page = tf.pages[0]
                arr = page.asarray()
                dpi = None
                try:
                    xr = page.tags.get("XResolution")
                    unit = page.tags.get("ResolutionUnit")
                    if xr is not None:
                        num, den = xr.value
                        val = num / den if den else None
                        if val and unit is not None and int(unit.value) == 3:
                            val *= 2.54  # pro cm -> pro Zoll
                        dpi = val
                except Exception:
                    dpi = None
                photometric = int(getattr(page, "photometric", 1))
            data, bits = _to_gray_float(arr, rgb_order=True)
            if photometric == 0:  # MINISWHITE: 0 = weiß
                data = 1.0 - data
            return LoadedImage(np.ascontiguousarray(data), bits, dpi)
        except Exception:
            pass  # Fallback auf OpenCV
    # np.fromfile + imdecode funktioniert auch mit Umlauten im Pfad (Windows)
    buf = np.fromfile(path, dtype=np.uint8)
    arr = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED | cv2.IMREAD_ANYDEPTH)
    if arr is None:
        raise ValueError(f"Bild konnte nicht gelesen werden: {path}")
    data, bits = _to_gray_float(arr, rgb_order=False)
    return LoadedImage(np.ascontiguousarray(data), bits, None)


EXPORT_FORMATS = {
    "tif16": ("TIFF 16 Bit, verlustfrei (*.tif)", ".tif"),
    "tif8": ("TIFF 8 Bit, verlustfrei (*.tif)", ".tif"),
    "png": ("PNG 8 Bit, verlustfrei (*.png)", ".png"),
    "jpg": ("JPEG, hohe Qualität (*.jpg)", ".jpg"),
}


def to_uint(img: np.ndarray, bits: int) -> np.ndarray:
    img = np.clip(img, 0.0, 1.0)
    if bits == 16:
        return (img * 65535.0 + 0.5).astype(np.uint16)
    return (img * 255.0 + 0.5).astype(np.uint8)


def save_image(path: str, arr: np.ndarray, fmt: str, dpi: float | None = None) -> None:
    if fmt in ("tif16", "tif8") and tifffile is not None:
        kwargs = {"photometric": "minisblack", "compression": "zlib"}
        if dpi:
            kwargs["resolution"] = (float(dpi), float(dpi))
            kwargs["resolutionunit"] = "INCH"
        try:
            tifffile.imwrite(path, arr, **kwargs)
        except Exception:
            kwargs.pop("compression")
            tifffile.imwrite(path, arr, **kwargs)
        return
    ext = EXPORT_FORMATS[fmt][1]
    params = []
    if fmt == "jpg":
        params = [cv2.IMWRITE_JPEG_QUALITY, 95]
    ok, enc = cv2.imencode(ext, arr, params)
    if not ok:
        raise IOError(f"Bild konnte nicht gespeichert werden: {path}")
    enc.tofile(path)


# --------------------------------------------------------------------------
# Dokument mit Zwischenspeichern für schnelle Vorschau
# --------------------------------------------------------------------------

def _resize_long(img: np.ndarray, long_side: int) -> tuple[np.ndarray, float]:
    h, w = img.shape
    s = long_side / max(h, w)
    if s >= 1.0:
        return img, 1.0
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    out = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    return out, nw / w


def rotate_image(img: np.ndarray, angle: float) -> np.ndarray:
    """Dreht um die Bildmitte, Leinwandgröße bleibt gleich."""
    if abs(angle) < 1e-6:
        return img
    h, w = img.shape
    m = cv2.getRotationMatrix2D(((w - 1) / 2.0, (h - 1) / 2.0), -angle, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def clamp_crop(crop, w: int, h: int) -> tuple[int, int, int, int]:
    if not crop:
        return 0, 0, w, h
    x, y, cw, ch = (int(round(v)) for v in crop)
    x = min(max(0, x), w - 1)
    y = min(max(0, y), h - 1)
    cw = max(1, min(cw, w - x))
    ch = max(1, min(ch, h - y))
    return x, y, cw, ch


@dataclass
class Prepared:
    """Geometrisch fertiges Bild + Hilfsdaten, unveränderlich nach Erstellung."""
    base: np.ndarray            # voll aufgelöst, gedreht & zugeschnitten
    levels: list                # [(scale, array)] aufsteigend nach scale, endet mit (1.0, base)
    small: np.ndarray           # ~512 px, für Ausleuchtungskarte
    small_scale: float
    stats: np.ndarray           # ~2048 px Stichprobe für Histogramm
    long_side: int

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.base.shape
        return w, h

    def gain_map(self, strength: float) -> np.ndarray | None:
        if strength <= 0:
            return None
        key = round(strength, 4)
        cache = self.__dict__.setdefault("_gain_cache", {})
        if key not in cache:
            sm = self.small
            sigma = max(sm.shape) / 6.0
            illum = cv2.GaussianBlur(sm, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
            illum = np.maximum(illum, 1e-3)
            gain = (float(illum.mean()) / illum) ** strength
            cache.clear()
            cache[key] = np.clip(gain, 0.25, 4.0).astype(np.float32)
        return cache[key]


class Document:
    def __init__(self, loaded: LoadedImage, path: str | None = None):
        self.original = loaded.data
        self.bits = loaded.bits
        self.dpi = loaded.dpi
        self.path = path
        self._lock = threading.Lock()
        self._rot_key = None
        self._rotated = None
        self._prep_key = None
        self._prep = None

    @classmethod
    def open(cls, path: str) -> "Document":
        return cls(load_image(path), path)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.original.shape
        return w, h

    def rotated(self, angle: float) -> np.ndarray:
        with self._lock:
            key = round(angle, 4)
            if self._rot_key != key:
                self._rotated = rotate_image(self.original, angle)
                self._rot_key = key
            return self._rotated

    def prepare(self, s: Settings) -> Prepared:
        w, h = self.size
        crop = clamp_crop(s.crop, w, h)
        key = (round(s.rotation, 4), crop)
        with self._lock:
            if self._prep_key == key:
                return self._prep
        rot = self.rotated(s.rotation)
        x, y, cw, ch = crop
        base = np.ascontiguousarray(rot[y:y + ch, x:x + cw])
        levels = []
        for target in (512, 2048):
            arr, sc = _resize_long(base, target)
            if sc < 1.0:
                levels.append((sc, arr))
        levels.append((1.0, base))
        small, small_scale = _resize_long(base, 512)
        stats, _ = _resize_long(base, 2048)
        prep = Prepared(base=base, levels=levels, small=small, small_scale=small_scale,
                        stats=stats, long_side=max(base.shape))
        with self._lock:
            self._prep_key, self._prep = key, prep
        return prep


# --------------------------------------------------------------------------
# Tonwert-Pipeline
# --------------------------------------------------------------------------

def _apply_gain(img: np.ndarray, prep: Prepared, s: Settings, x0: float, y0: float,
                scale: float) -> np.ndarray:
    gain = prep.gain_map(s.flatten)
    if gain is None:
        return img
    k = prep.small_scale
    h, w = img.shape
    # Ausgabepixel (u, v) -> Basiskoordinate (x0 + u/scale) -> Kleinbild * k
    m = np.array([[k / scale, 0, x0 * k], [0, k / scale, y0 * k]], dtype=np.float64)
    g = cv2.warpAffine(gain, m, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                       borderMode=cv2.BORDER_REPLICATE)
    return img * g


def compute_levels(prep: Prepared, s: Settings) -> tuple[float, float]:
    """Schwarz- und Weißpunkt der Histogrammstreckung (global, einmal pro Einstellung)."""
    if not s.stretch:
        return 0.0, 1.0
    st = prep.stats
    if s.invert:
        st = 1.0 - st
    if s.flatten > 0:
        sc = st.shape[1] / prep.base.shape[1]
        st = _apply_gain(st, prep, s, 0.0, 0.0, sc)
    sample = st[::2, ::2] if st.size > 1_500_000 else st
    lo, hi = np.percentile(sample, [s.clip_black, 100.0 - s.clip_white])
    lo, hi = float(lo), float(hi)
    if hi - lo < 1e-4:
        hi = lo + 1e-4
    return lo, hi


def filter_margin(prep: Prepared, s: Settings) -> int:
    """Benötigter Rand (Originalpixel), damit Kacheln nahtlos sind.

    OpenCV nutzt für float-Gaussfilter Kerne mit Radius ~4 Sigma.
    """
    m = 2.0
    if s.sharpen_amount > 0:
        m = max(m, 4 * max(s.sharpen_radius, 0.5) + 2)
    if s.clarity > 0:
        m = max(m, 4 * CLARITY_SIGMA_REL * prep.long_side + 2)
    if s.denoise > 0:
        m = max(m, 2 * (1 + 3 * s.denoise) * 1.5 + 2)
    return int(math.ceil(m))


def apply_tonal(img: np.ndarray, prep: Prepared, s: Settings, levels: tuple[float, float],
                x0: float, y0: float, scale: float) -> np.ndarray:
    """img: Ausschnitt mit Ursprung (x0, y0) in Basispixeln, verkleinert um scale."""
    img = img.astype(np.float32, copy=True)
    if s.invert:
        img = 1.0 - img
    if s.flatten > 0:
        img = _apply_gain(img, prep, s, x0, y0, scale)
    if s.denoise > 0:
        sigma_space = (1 + 3 * s.denoise) * scale
        if sigma_space >= 0.4:
            sigma_color = 0.01 + 0.09 * s.denoise
            img = cv2.bilateralFilter(img, 0, sigma_color, sigma_space)

    lo, hi = levels
    if (lo, hi) != (0.0, 1.0):
        img = (img - lo) * (1.0 / (hi - lo))
    np.clip(img, 0.0, 1.0, out=img)

    if abs(s.midtones) > 1e-4:
        gamma = 3.0 ** (-s.midtones)
        img = np.power(img, gamma, dtype=np.float32)

    if s.clarity > 0:
        sigma = CLARITY_SIGMA_REL * prep.long_side * scale
        if sigma >= 0.5:
            blur = cv2.GaussianBlur(img, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
            img = img + s.clarity * (img - blur)

    if s.sharpen_amount > 0:
        sigma = max(s.sharpen_radius * scale, 0.5)
        blur = cv2.GaussianBlur(img, (0, 0), sigma, borderType=cv2.BORDER_REFLECT)
        detail = img - blur
        if s.sharpen_threshold > 0:
            t = s.sharpen_threshold
            weight = np.clip((np.abs(detail) - t) / t, 0.0, 1.0)
            detail *= weight
        img = img + s.sharpen_amount * detail

    np.clip(img, 0.0, 1.0, out=img)
    return img


def render(prep: Prepared, s: Settings, region=None, scale: float = 1.0,
           levels: tuple[float, float] | None = None) -> np.ndarray:
    """Berechnet einen Ausschnitt (x, y, w, h in Basispixeln) in der Größe scale.

    Die Ausgabe hat round(w*scale) x round(h*scale) Pixel.
    """
    W, H = prep.size
    x, y, w, h = region if region is not None else (0, 0, W, H)
    scale = min(float(scale), 1.0)
    if levels is None:
        levels = compute_levels(prep, s)

    # kleinste Auflösungsstufe wählen, die nicht hochskaliert werden muss
    ls, src = prep.levels[-1]
    for lscale, arr in prep.levels:
        if lscale >= scale - 1e-9:
            ls, src = lscale, arr
            break

    pad = filter_margin(prep, s)
    sh, sw = src.shape
    x0l = max(0, int(math.floor((x - pad) * ls)))
    y0l = max(0, int(math.floor((y - pad) * ls)))
    x1l = min(sw, int(math.ceil((x + w + pad) * ls)))
    y1l = min(sh, int(math.ceil((y + h + pad) * ls)))
    tile = src[y0l:y1l, x0l:x1l]

    f = scale / ls
    if f < 0.999:
        tw = max(1, round(tile.shape[1] * f))
        th = max(1, round(tile.shape[0] * f))
        tile = cv2.resize(tile, (tw, th), interpolation=cv2.INTER_AREA)
        fx = tw / (x1l - x0l)
    else:
        fx = 1.0
    eff = ls * fx  # Ausgabepixel pro Basispixel

    out = apply_tonal(tile, prep, s, levels, x0l / ls, y0l / ls, eff)

    cx = int(round((x * ls - x0l) * fx))
    cy = int(round((y * ls - y0l) * fx))
    cw = max(1, int(round(w * scale)))
    ch = max(1, int(round(h * scale)))
    out = out[cy:cy + ch, cx:cx + cw]
    if out.shape != (ch, cw):  # Rundungsdifferenz am Rand ausgleichen
        out = cv2.copyMakeBorder(out, 0, max(0, ch - out.shape[0]), 0,
                                 max(0, cw - out.shape[1]), cv2.BORDER_REPLICATE)[:ch, :cw]
    return out


def render_full(doc: Document, s: Settings, bits: int = 16, tile: int = 2048,
                progress=None) -> np.ndarray:
    """Volle Auflösung, gekachelt (spart Arbeitsspeicher bei großen Scans)."""
    prep = doc.prepare(s)
    levels = compute_levels(prep, s)
    W, H = prep.size
    out = np.empty((H, W), dtype=np.uint16 if bits == 16 else np.uint8)
    tiles = [(x, y) for y in range(0, H, tile) for x in range(0, W, tile)]
    for i, (x, y) in enumerate(tiles):
        w, h = min(tile, W - x), min(tile, H - y)
        out[y:y + h, x:x + w] = to_uint(render(prep, s, (x, y, w, h), 1.0, levels), bits)
        if progress:
            progress(i + 1, len(tiles))
    return out


def export(doc: Document, s: Settings, path: str, fmt: str, progress=None) -> None:
    bits = 16 if fmt == "tif16" else 8
    arr = render_full(doc, s, bits=bits, progress=progress)
    save_image(path, arr, fmt, doc.dpi)


def histogram(img: np.ndarray, bins: int = 256) -> np.ndarray:
    sample = img[::2, ::2] if img.size > 2_000_000 else img
    h, _ = np.histogram(sample, bins=bins, range=(0.0, 1.0))
    return h.astype(np.float64)


def display_8bit(img: np.ndarray, auto: bool = True) -> np.ndarray:
    """8-Bit-Ansicht, optional leicht gestreckt, damit dunkle Scans sichtbar sind."""
    if auto:
        sample = img[::4, ::4] if img.size > 4_000_000 else img
        lo, hi = np.percentile(sample, [0.2, 99.8])
        if hi - lo > 1e-4:
            img = (img - lo) / (hi - lo)
    return to_uint(img, 8)
