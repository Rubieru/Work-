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
_SHARP = {"sharpen_amount": 0.6, "sharpen_radius": 1.0, "sharpen_threshold": 0.008}
_BASE = {"stretch": True, "clip_black": 0.1, "clip_white": 0.05, "stats_center": True}

# Grundlage (nach der Bachelorarbeit zur Bildaufbereitung historischer Luftbilder für die
# Photogrammetrie): Histogramm über den ganzen Grauwertbereich strecken (Ausgabe 1–255),
# die Spitze abflachen und nur maßvoll filtern, damit die radiometrische Information für
# die photogrammetrische Einrichtung erhalten bleibt. Der schwarze Scanrahmen zählt bei
# allen Messungen nicht mit.
#
# "Automatisch" ist ein Platzhalter: die Werte misst das Programm je Bild
# (processing.auto_tonal).
AUTO = "Automatisch (empfohlen)"


def _p(**kw):
    d = dict(_BASE)
    d.update(kw)
    return d


BUILTIN_PRESETS: dict[str, dict] = {
    "Original": {},
    AUTO: {},
    # wie Abb. 1 der Bachelorarbeit: lineare Streckung auf 1–255. Die Spitze wird dabei
    # flacher, weil sich dieselben Pixel auf mehr Grauwerte verteilen; die Helligkeits-
    # verhältnisse (und damit die Zeichnung in den Lichtern) bleiben erhalten.
    "Standard (Bachelorarbeit)": _p(sharpen_amount=0.4, sharpen_radius=1.0,
                                    sharpen_threshold=0.01),
    # --- Varianten des Abflachens (zusätzlich zur Streckung)
    "Abflachen weich": _p(hist_flat=0.6, hist_mode="glocke", **_SHARP),
    "Abflachen stark": _p(hist_flat=0.7, hist_mode="gleichmaessig", **_SHARP),
    "Abflachen schonend": _p(hist_flat=0.8, hist_mode="begrenzt", **_SHARP),
    "Nur strecken": _p(),
    # --- nach Bildinhalt
    "Stadt / Bebauung": _p(hist_flat=0.5, hist_mode="glocke", sharpen_amount=0.9,
                           sharpen_radius=0.8, sharpen_threshold=0.01),
    "Wald": _p(hist_flat=0.6, hist_mode="glocke", midtones=0.1, equalize=0.15,
               sharpen_amount=0.6, sharpen_radius=1.2, sharpen_threshold=0.015),
    "Wiesen & Felder": _p(hist_flat=0.7, hist_mode="gleichmaessig", equalize=0.25,
                          sharpen_amount=0.7, sharpen_radius=1.3, sharpen_threshold=0.012),
    "Wasser / Fluss": _p(hist_flat=0.5, hist_mode="begrenzt", denoise=0.3,
                         sharpen_amount=0.5, sharpen_radius=1.0, sharpen_threshold=0.02),
    # --- nach Bildproblem
    "Flau / kontrastarm": _p(clip_black=0.2, hist_flat=0.8, hist_mode="glocke",
                             equalize=0.3, **_SHARP),
    "Details / Auswertung": _p(hist_flat=0.6, hist_mode="glocke", equalize=0.3, clarity=0.1,
                               sharpen_amount=1.0, sharpen_radius=0.9,
                               sharpen_threshold=0.012),
    "Zu dunkel": _p(clip_white=0.1, hist_flat=0.6, hist_mode="glocke", midtones=0.25,
                    **_SHARP),
    "Zu hell": _p(clip_black=0.2, clip_white=0.02, hist_flat=0.6, hist_mode="glocke",
                  midtones=-0.2, **_SHARP),
    "Ungleich belichtet": _p(flatten=0.6, hist_flat=0.5, hist_mode="glocke", **_SHARP),
    "Grobkörnig": _p(dust=0.3, denoise=0.5, hist_flat=0.5, hist_mode="glocke",
                     sharpen_amount=0.5, sharpen_radius=1.5, sharpen_threshold=0.025),
    "Staub & Kratzer": _p(dust=0.7, denoise=0.15, hist_flat=0.5, hist_mode="glocke",
                          sharpen_amount=0.5, sharpen_radius=1.0, sharpen_threshold=0.015),
    "GIS / DGM sicher": {
        # entspricht dem Gemini-Skript: 0,5/99,5 %, 10 % Puffer,
        # Unscharf maskieren Sigma 1 / Stärke 50 %
        "stretch": True, "clip_black": 0.5, "clip_white": 0.5, "stats_center": True,
        "stretch_buffer": 0.1, "sharpen_amount": 0.5, "sharpen_radius": 1.0,
    },
    "Negativ": _p(invert=True, hist_flat=0.6, hist_mode="glocke", **_SHARP),
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
