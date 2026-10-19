"""Voreinstellungen: eingebaute und eigene (als JSON gespeichert)."""
from __future__ import annotations

import json
import os

from .processing import TONAL_FIELDS

# Nur Tonwert-/Filterwerte. Zuschnitt und Drehung bleiben beim Bild.
BUILTIN_PRESETS: dict[str, dict] = {
    "Original": {},
    "Standard": {
        "stretch": True, "clip_black": 0.5, "clip_white": 0.5,
        "sharpen_amount": 0.8, "sharpen_radius": 1.0,
    },
    "Kräftig": {
        "stretch": True, "clip_black": 1.0, "clip_white": 1.0, "clarity": 0.35,
        "sharpen_amount": 1.2, "sharpen_radius": 1.2, "sharpen_threshold": 0.01,
    },
    "Flaues Bild": {
        "stretch": True, "clip_black": 2.0, "clip_white": 1.0, "midtones": -0.1,
        "clarity": 0.6, "sharpen_amount": 1.0, "sharpen_radius": 1.2,
    },
    "Ungleich belichtet": {
        "flatten": 0.8, "stretch": True, "clip_black": 0.5, "clip_white": 0.5,
        "clarity": 0.3, "sharpen_amount": 0.8, "sharpen_radius": 1.0,
    },
    "Körnig / verrauscht": {
        "denoise": 0.5, "stretch": True, "clip_black": 0.5, "clip_white": 0.5,
        "sharpen_amount": 0.7, "sharpen_radius": 1.8, "sharpen_threshold": 0.02,
    },
    "Feine Details": {
        "stretch": True, "clip_black": 0.3, "clip_white": 0.3,
        "sharpen_amount": 1.6, "sharpen_radius": 0.7, "sharpen_threshold": 0.008,
    },
    "Dunkler Scan": {
        "stretch": True, "clip_black": 0.3, "clip_white": 1.0, "midtones": 0.35,
        "clarity": 0.2, "sharpen_amount": 0.8, "sharpen_radius": 1.0,
    },
    "Negativ": {
        "invert": True, "stretch": True, "clip_black": 0.5, "clip_white": 0.5,
        "sharpen_amount": 0.8, "sharpen_radius": 1.0,
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
