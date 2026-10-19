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

# Abgestimmt an echten Weltkriegs-Luftbildern: Ziel ist kaum reines Schwarz/Weiß
# (helle Flächen und dunkle Partien behalten Zeichnung) bei möglichst viel lokalem
# Kontrast. Weiß wird deshalb kaum abgeschnitten.
#
# "Automatisch" ist ein Platzhalter: die Werte misst das Programm je Bild
# (processing.auto_tonal), unabhängig davon, ob Stadt, Wald, Wiesen oder Wasser.
AUTO = "Automatisch (empfohlen)"

BUILTIN_PRESETS: dict[str, dict] = {
    "Original": {},
    AUTO: {},
    "Standard (ausgewogen)": {
        "stretch": True, "clip_black": 0.2, "clip_white": 0.1, "stretch_buffer": 0.02,
        "equalize": 0.35, "midtones": 0.1, **_SHARP,
    },
    # --- nach Bildinhalt
    "Stadt / Bebauung": {
        # viele Kanten, helle Dächer und Straßen, harte Schatten: wenig Ausgleich
        # (sonst Lichthöfe), feines Schärfen
        "stretch": True, "clip_black": 0.2, "clip_white": 0.05, "equalize": 0.25,
        "midtones": 0.05, "sharpen_amount": 0.9, "sharpen_radius": 0.8,
        "sharpen_threshold": 0.01,
    },
    "Wald": {
        # dunkel und strukturreich: Mitteltöne anheben, Struktur herausholen
        "stretch": True, "clip_black": 0.1, "clip_white": 0.2, "equalize": 0.45,
        "midtones": 0.3, "sharpen_amount": 0.6, "sharpen_radius": 1.2,
        "sharpen_threshold": 0.015,
    },
    "Wiesen & Felder": {
        # große, flaue Flächen: kräftiger Ausgleich und Klarheit für feine Spuren
        # (Wege, Gräben, Trichter, Bewuchsmerkmale)
        "stretch": True, "clip_black": 0.3, "clip_white": 0.1, "equalize": 0.6,
        "clarity": 0.2, "midtones": 0.0, "sharpen_amount": 0.7, "sharpen_radius": 1.3,
        "sharpen_threshold": 0.012,
    },
    "Wasser / Fluss": {
        # glatte Wasserflächen nicht aufrauen: wenig Ausgleich, Korn glätten,
        # hohe Schwelle beim Schärfen
        "stretch": True, "clip_black": 0.1, "clip_white": 0.1, "equalize": 0.2,
        "denoise": 0.3, "midtones": 0.1, "sharpen_amount": 0.6, "sharpen_radius": 1.0,
        "sharpen_threshold": 0.02,
    },
    # --- nach Bildproblem
    "Flau / kontrastarm": {
        "stretch": True, "clip_black": 0.4, "clip_white": 0.1, "equalize": 0.6,
        "midtones": 0.1, **_SHARP,
    },
    "Details / Auswertung": {
        # z. B. Bombentrichter, Laufgräben, Stellungen
        "stretch": True, "clip_black": 0.2, "clip_white": 0.05, "equalize": 0.45,
        "clarity": 0.15, "midtones": 0.05, "sharpen_amount": 1.0, "sharpen_radius": 0.9,
        "sharpen_threshold": 0.012,
    },
    "Zu dunkel": {
        "stretch": True, "clip_black": 0.1, "clip_white": 0.2, "equalize": 0.4,
        "midtones": 0.35, **_SHARP,
    },
    "Zu hell": {
        "stretch": True, "clip_black": 0.2, "clip_white": 0.05, "equalize": 0.45,
        "midtones": -0.15, **_SHARP,
    },
    "Ungleich belichtet": {
        "flatten": 0.6, "stretch": True, "clip_black": 0.2, "clip_white": 0.1,
        "equalize": 0.35, "midtones": 0.1, **_SHARP,
    },
    "Grobkörnig": {
        "dust": 0.3, "denoise": 0.5, "stretch": True, "clip_black": 0.2,
        "clip_white": 0.1, "equalize": 0.3, "midtones": 0.1, "sharpen_amount": 0.5,
        "sharpen_radius": 1.5, "sharpen_threshold": 0.025,
    },
    "Staub & Kratzer": {
        "dust": 0.7, "denoise": 0.15, "stretch": True, "clip_black": 0.2,
        "clip_white": 0.1, "equalize": 0.3, "midtones": 0.1, "sharpen_amount": 0.5,
        "sharpen_radius": 1.0, "sharpen_threshold": 0.015,
    },
    "GIS / DGM sicher": {
        # entspricht dem Gemini-Skript: 0,5/99,5 %, 10 % Puffer,
        # Unscharf maskieren Sigma 1 / Stärke 50 %
        "stretch": True, "clip_black": 0.5, "clip_white": 0.5, "stats_center": True,
        "stretch_buffer": 0.1, "sharpen_amount": 0.5, "sharpen_radius": 1.0,
    },
    "Negativ": {
        "invert": True, "stretch": True, "clip_black": 0.2, "clip_white": 0.1,
        "stretch_buffer": 0.02, "equalize": 0.35, **_SHARP,
    },
}


def preset_values(name: str, prep=None, user: dict | None = None) -> dict | None:
    """Werte einer Voreinstellung; "Automatisch" wird für das Bild (prep) berechnet."""
    if name == AUTO:
        if prep is None:
            return None
        from .processing import auto_tonal
        return auto_tonal(prep)
    if user and name in user:
        return user[name]
    return BUILTIN_PRESETS.get(name)


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
