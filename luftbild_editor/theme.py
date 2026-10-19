"""Name, Farben und Logo des Programms an einer Stelle.

Um Farben an das Firmendesign anzupassen, genügt es, die Werte hier zu ändern.
Das Logo wird aus resources/logo.png geladen (falls vorhanden); daraus wird beim
Bauen der .exe auch das Programmsymbol (logo.ico) erzeugt.
"""
import os

APP_NAME = "Luftbild-Editor PZU"

# Akzentfarbe (Knöpfe, Auswahl, Zuschneiderahmen) und Schriftfarbe darauf
ACCENT = "#ffc400"
ACCENT_TEXT = "#111111"

# dunkle, neutrale Oberfläche – Grautöne lassen sich so am besten beurteilen
WINDOW = "#353535"
BASE = "#262626"
TEXT = "#e1e1e1"
VIEW_BACKGROUND = "#2d2d2d"

RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")
LOGO_PNG = os.path.join(RESOURCES, "logo.png")


def logo_path() -> str | None:
    return LOGO_PNG if os.path.exists(LOGO_PNG) else None


def log_dir() -> str:
    """Ordner für das Protokoll: %APPDATA%\\Luftbild-Editor (Windows)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "Luftbild-Editor")
