"""Name, Farben und Logo des Programms an einer Stelle.

Um Farben an das Firmendesign anzupassen, genügt es, die Werte hier zu ändern.
Das Logo wird aus resources/logo.png geladen (falls vorhanden); daraus wird beim
Bauen der .exe auch das Programmsymbol (logo.ico) erzeugt.
"""
import os

APP_NAME = "Luftbild-Editor PZU"

COMPANY = "Plan-Zentrum Umwelt"
SUBTITLE = "Luftbild-Editor"

# Farben der Website plan-zentrumumwelt.de (Türkis #009D88, Grau der Unterzeile)
ACCENT = "#009d88"        # Knöpfe, Auswahl, Regler, Zuschneiderahmen
ACCENT_DARK = "#007a6a"   # gedrückt / Rahmen
ACCENT_LIGHT = "#d9f0ec"  # dezente Hinterlegung
ACCENT_TEXT = "#ffffff"   # Schrift auf Türkis
GREY_TEXT = "#8c8c8c"     # wie die Unterzeile im Logo der Website

# helle Oberfläche wie die Website; die Bildfläche bleibt neutral dunkelgrau,
# weil sich Graustufen darauf am besten beurteilen lassen
WINDOW = "#eef2f1"
BASE = "#ffffff"
TEXT = "#1f2a28"
MUTED = "#5f6b69"
BORDER = "#c9d3d1"
VIEW_BACKGROUND = "#3a3d3c"

RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")
LOGO_PNG = os.path.join(RESOURCES, "logo.png")


def logo_path() -> str | None:
    return LOGO_PNG if os.path.exists(LOGO_PNG) else None


def log_dir() -> str:
    """Ordner für das Protokoll: %APPDATA%\\Luftbild-Editor (Windows)."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return os.path.join(base, "Luftbild-Editor")


def stylesheet() -> str:
    """Gemeinsames Aussehen (Qt-Stylesheet)."""
    return f"""
    QMainWindow, QDialog {{ background: {WINDOW}; }}
    QToolBar {{ background: {BASE}; border: none; border-bottom: 1px solid {BORDER};
                spacing: 4px; padding: 2px 6px; }}
    QToolBar QToolButton {{ color: {TEXT}; padding: 4px 8px; border-radius: 3px; }}
    QToolBar QToolButton:hover {{ background: {ACCENT_LIGHT}; }}
    QToolBar QToolButton:pressed, QToolBar QToolButton:checked {{
        background: {ACCENT}; color: {ACCENT_TEXT}; }}
    QMenuBar {{ background: {BASE}; color: {TEXT}; }}
    QMenuBar::item:selected {{ background: {ACCENT_LIGHT}; }}
    QMenu::item:selected {{ background: {ACCENT}; color: {ACCENT_TEXT}; }}
    QGroupBox {{ border: 1px solid {BORDER}; border-radius: 4px; margin-top: 14px;
                 padding: 6px 6px 4px 6px; background: {BASE}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px;
                        color: {ACCENT_DARK}; font-weight: bold; }}
    QPushButton {{ background: {BASE}; color: {TEXT}; border: 1px solid {BORDER};
                   border-radius: 4px; padding: 5px 10px; }}
    QPushButton:hover {{ border-color: {ACCENT}; background: {ACCENT_LIGHT}; }}
    QPushButton:pressed {{ background: {ACCENT}; color: {ACCENT_TEXT}; }}
    QPushButton:disabled {{ color: #a0a8a7; border-color: #dde3e2; }}
    QPushButton#primary {{ background: {ACCENT}; color: {ACCENT_TEXT}; border: none;
                           font-weight: bold; font-size: 14px; }}
    QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
    QPushButton#primary:disabled {{ background: #b9c4c2; color: #eef2f1; }}
    QPushButton#mode:checked {{ background: {ACCENT}; color: {ACCENT_TEXT};
                                border-color: {ACCENT}; font-weight: bold; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; background: {WINDOW}; top: -1px; }}
    QTabBar::tab {{ background: {WINDOW}; color: {MUTED}; padding: 7px 14px;
                    border: 1px solid {BORDER}; border-bottom: none;
                    border-top-left-radius: 4px; border-top-right-radius: 4px; }}
    QTabBar::tab:selected {{ background: {ACCENT}; color: {ACCENT_TEXT}; font-weight: bold; }}
    QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {BASE}; border: 2px solid {ACCENT};
                                  width: 12px; margin: -6px 0; border-radius: 7px; }}
    QSlider::handle:horizontal:disabled {{ border-color: {BORDER}; }}
    QListWidget {{ background: {BASE}; border: 1px solid {BORDER}; border-radius: 4px; }}
    QListWidget::item:selected {{ background: {ACCENT}; color: {ACCENT_TEXT}; }}
    QListWidget::item:hover {{ background: {ACCENT_LIGHT}; color: {TEXT}; }}
    QStatusBar {{ background: {BASE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
    QLabel#heading {{ color: {ACCENT_DARK}; font-weight: bold; font-size: 14px; }}
    QLabel#hint {{ color: {MUTED}; }}
    QToolTip {{ background: {BASE}; color: {TEXT}; border: 1px solid {ACCENT}; }}
    """
