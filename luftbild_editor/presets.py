"""Voreinstellungen: eingebaute und eigene (als JSON gespeichert)."""
from __future__ import annotations

import json
import os

from .processing import TONAL_FIELDS

# Nur Tonwert-/Filterwerte. Zuschnitt und Drehung bleiben beim Bild.
#
# Abgestimmt auf gescannte Schwarz-Weiß-Luftbilder (Reihenmesskammer-Filme):
# - Histogrammstreckung mit kleinem Abschnitt (0,2–1 %), damit Lichter auf
#   Dächern/Wegen nicht ausfressen.
# - Kontrastausgleich (CLAHE, wie das für Weltkriegs-Luftbilder entwickelte
#   "Multidodging") holt Details aus flauen Bereichen.
# - Unscharf maskieren mit Radius ~1 px und Stärke ~70 % als Grundwert; die
#   Schwelle verhindert, dass Filmkorn mitgeschärft wird.
_SHARP = {"sharpen_amount": 0.7, "sharpen_radius": 1.0, "sharpen_threshold": 0.01}

BUILTIN_PRESETS: dict[str, dict] = {
    "Original": {},
    "Standard Luftbild": {
        "stretch": True, "clip_black": 0.3, "clip_white": 0.3, "stretch_buffer": 0.03,
        "equalize": 0.25, **_SHARP,
    },
    "Flau / kontrastarm": {
        "stretch": True, "clip_black": 0.8, "clip_white": 0.5, "equalize": 0.5,
        "sharpen_amount": 0.7, "sharpen_radius": 1.2, "sharpen_threshold": 0.01,
    },
    "Ungleich belichtet": {
        "flatten": 0.7, "stretch": True, "clip_black": 0.4, "clip_white": 0.4,
        "equalize": 0.3, "sharpen_amount": 0.6, "sharpen_radius": 1.0,
        "sharpen_threshold": 0.01,
    },
    "Details / Auswertung": {
        "stretch": True, "clip_black": 0.4, "clip_white": 0.4, "equalize": 0.4,
        "clarity": 0.15, "sharpen_amount": 1.1, "sharpen_radius": 0.9,
        "sharpen_threshold": 0.012,
    },
    "Grobkörnig": {
        "dust": 0.3, "denoise": 0.5, "stretch": True, "clip_black": 0.4,
        "clip_white": 0.4, "equalize": 0.2, "sharpen_amount": 0.5,
        "sharpen_radius": 1.5, "sharpen_threshold": 0.025,
    },
    "Staub & Kratzer": {
        "dust": 0.7, "denoise": 0.15, "stretch": True, "clip_black": 0.3,
        "clip_white": 0.3, "equalize": 0.2, "sharpen_amount": 0.5,
        "sharpen_radius": 1.0, "sharpen_threshold": 0.015,
    },
    "Zu dunkel": {
        "stretch": True, "clip_black": 0.2, "clip_white": 0.8, "midtones": 0.3,
        "equalize": 0.3, "sharpen_amount": 0.6, "sharpen_radius": 1.0,
        "sharpen_threshold": 0.01,
    },
    "Zu hell": {
        "stretch": True, "clip_black": 0.8, "clip_white": 0.2, "midtones": -0.25,
        "equalize": 0.3, "sharpen_amount": 0.6, "sharpen_radius": 1.0,
        "sharpen_threshold": 0.01,
    },
    "GIS / DGM sicher": {
        # entspricht dem Gemini-Skript: Statistik aus der Bildmitte, 0,5/99,5 %,
        # 10 % Puffer, Unscharf maskieren Sigma 1 / Stärke 50 %
        "stretch": True, "clip_black": 0.5, "clip_white": 0.5, "stats_center": True,
        "stretch_buffer": 0.1, "sharpen_amount": 0.5, "sharpen_radius": 1.0,
    },
    "Negativ": {
        "invert": True, "stretch": True, "clip_black": 0.3, "clip_white": 0.3,
        "stretch_buffer": 0.03, "equalize": 0.25, **_SHARP,
    },
}


def user_preset_file(directory: str) -> str:
    return os.path.join(directory, "voreinstellungen.json")


def load_user_presets(directory: str) -> dict[str, dict]:
    path = user_preset_file(directory)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): {kk: vv for kk, vv in v.items() if kk in TONAL_FIELDS}
            for k, v in data.items() if isinstance(v, dict)}


def save_user_presets(directory: str, presets: dict[str, dict]) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(user_preset_file(directory), "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)
