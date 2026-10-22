"""Voreinstellungen: eingebaute und eigene (als JSON gespeichert)."""
from __future__ import annotations

import json
import os

from .processing import TONAL_FIELDS

# Nur Tonwert-/Filterwerte. Zuschnitt und Drehung bleiben beim Bild.
#
# Die Namen sagen, was die Voreinstellung macht. Grundlage aller Voreinstellungen ist die
# Histogrammstreckung auf 1–255 (schwarzer Scanrahmen zählt nicht mit). Danach wird die
# mittlere Helligkeit des Originals wiederhergestellt (Gamma), damit das Bild durch die
# Streckung kontrastreicher, aber nicht heller wird. Filter
# werden nur maßvoll eingesetzt, damit die radiometrische Information für die
# photogrammetrische Einrichtung erhalten bleibt.
_BASE = {"stretch": True, "clip_black": 0.1, "clip_white": 0.05, "stats_center": True,
         "keep_brightness": True}
# Hochpassfilter (Unscharf maskieren): Radius 1 px; die Schwelle schont das Filmkorn
_HP = {"sharpen_amount": 0.7, "sharpen_radius": 1.0, "sharpen_threshold": 0.008}

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
    # --- Streckung, einzeln und mit je einem Filter
    "Histogrammstreckung": _p(),
    "Streckung + Hochpassfilter": _p(**_HP),
    "Streckung + Tiefpassfilter": _p(lowpass=0.8),
    "Streckung + Medianfilter": _p(median=3),
    "Medianfilter + Hochpassfilter": _p(median=3, sharpen_amount=0.9, sharpen_radius=1.2,
                                        sharpen_threshold=0.01),
    # --- Histogramm abflachen (zusätzlich zur Streckung)
    "Abflachen weich (Glockenform)": _p(hist_flat=0.6, hist_mode="glocke"),
    "Abflachen stark (Ausgleich)": _p(hist_flat=0.7, hist_mode="gleichmaessig"),
    "Abflachen: Spitze kappen": _p(hist_flat=0.8, hist_mode="begrenzt"),
    # --- weitere Werkzeuge
    "Lokaler Kontrastausgleich (CLAHE)": _p(equalize=0.35),
    "Helligkeitsausgleich (Vignettierung)": _p(flatten=0.6),
    "Gamma: Mitteltöne aufhellen": _p(midtones=0.25),
    "Gamma: Mitteltöne abdunkeln": _p(midtones=-0.2),
    "Staub- & Kratzerfilter": _p(dust=0.7),
    "Streckung mit 10 % Puffer": {
        # entspricht dem Gemini-Skript: 0,5/99,5 %, 10 % Puffer, Hochpass Sigma 1 / 50 %
        "stretch": True, "clip_black": 0.5, "clip_white": 0.5, "stats_center": True,
        "stretch_buffer": 0.1, "sharpen_amount": 0.5, "sharpen_radius": 1.0,
    },
    "Negativ umkehren": _p(invert=True),
}

# Erklärung je Voreinstellung (Tooltip): was sie macht und wofür sie passt
PRESET_INFO: dict[str, str] = {
    "Original": "Keine Bearbeitung.",
    AUTO: "Misst Helligkeit, Kontrast und Filmkorn dieses Bildes: Histogrammstreckung, "
          "bei sehr spitzem Histogramm leichtes Abflachen, bei starkem Korn Medianfilter, "
          "danach Hochpassfilter.",
    "Histogrammstreckung": "Streckung der Grauwerte auf 1–255; die mittlere Helligkeit "
                           "des Originals bleibt. Sonst nichts – für Bilder ohne Bildfehler.",
    "Streckung + Hochpassfilter": "Streckung, dann Hochpassfilter (Kanten betonen). "
                                  "Für die meisten Bilder – Bebauung, Wege, Trichterränder "
                                  "werden deutlicher.",
    "Streckung + Tiefpassfilter": "Streckung, dann Tiefpassfilter (Mittelwert der Umgebung). "
                                  "Glättet Rauschen, macht das Bild aber weicher.",
    "Streckung + Medianfilter": "Streckung, dann Medianfilter 3×3. Entfernt Körnung und "
                                "einzelne Störpixel, Kanten bleiben erhalten. Gut für Wasser, "
                                "Wiesen und gleichmäßige Flächen.",
    "Medianfilter + Hochpassfilter": "Erst Körnung mit dem Medianfilter entfernen, dann "
                                     "schärfen – für grobkörnige Bilder, die trotzdem scharf "
                                     "werden sollen.",
    "Abflachen weich (Glockenform)": "Streckung, dann die Spitze im Histogramm abflachen "
                                     "(Glockenform um die mittlere Helligkeit). Für sehr "
                                     "flaue Bilder.",
    "Abflachen stark (Ausgleich)": "Streckung, dann Histogrammausgleich – annähernd gleich "
                                   "viele Pixel je Grauwert. Maximaler Kontrast, hellt auf.",
    "Abflachen: Spitze kappen": "Streckung, nur die höchste Spitze im Histogramm wird "
                                "gekappt. Für große gleichmäßige Flächen (Wasser, Felder).",
    "Lokaler Kontrastausgleich (CLAHE)": "Histogrammausgleich je Bildbereich. Holt Details aus "
                                         "dunklen und flauen Partien, verändert die "
                                         "Grauwerte aber örtlich unterschiedlich.",
    "Helligkeitsausgleich (Vignettierung)": "Gleicht dunkle Bildränder und Helligkeitsverläufe "
                                            "aus, dann Streckung.",
    "Gamma: Mitteltöne aufhellen": "Streckung, dann Gammakorrektur: mittlere Grautöne heller, "
                                   "Schwarz und Weiß bleiben. Für dunkle Bilder.",
    "Gamma: Mitteltöne abdunkeln": "Streckung, dann Gammakorrektur: mittlere Grautöne dunkler. "
                                   "Für helle Bilder.",
    "Staub- & Kratzerfilter": "Streckung, dann Ersetzen einzelner heller/dunkler Punkte durch "
                              "den Median der Umgebung (nur Ausreißer werden verändert).",
    "Streckung mit 10 % Puffer": "Weichere Streckung (0,5 %/99,5 % mit 10 % Puffer) und "
                                 "leichter Hochpassfilter – wie das Gemini-Skript.",
    "Negativ umkehren": "Kehrt ein Negativ um und streckt das Histogramm.",
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
