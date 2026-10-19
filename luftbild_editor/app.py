"""Programmstart."""
import faulthandler
import logging
import os
import sys
import time

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from . import main_window as mw
from . import theme


def dark_palette() -> QPalette:
    p = QPalette()
    base, window, text = QColor(theme.BASE), QColor(theme.WINDOW), QColor(theme.TEXT)
    for role, color in ((QPalette.Window, window), (QPalette.WindowText, text),
                        (QPalette.Base, base), (QPalette.AlternateBase, window),
                        (QPalette.ToolTipBase, QColor(25, 25, 25)),
                        (QPalette.ToolTipText, text), (QPalette.Text, text),
                        (QPalette.Button, window), (QPalette.ButtonText, text),
                        (QPalette.Highlight, QColor(theme.ACCENT)),
                        (QPalette.HighlightedText, QColor(theme.ACCENT_TEXT)),
                        (QPalette.PlaceholderText, QColor(130, 130, 130))):
        p.setColor(role, color)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(120, 120, 120))
    return p


def setup_logging() -> str:
    """Protokoll nach %APPDATA%\\Luftbild-Editor\\protokoll.txt (auch ohne Konsole)."""
    folder = theme.log_dir()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "protokoll.txt")
    try:  # Datei klein halten
        if os.path.getsize(path) > 2_000_000:
            os.remove(path)
    except OSError:
        pass
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Abstürze tief in C-Bibliotheken ebenfalls protokollieren
    global _fault_file
    _fault_file = open(path, "a", encoding="utf-8")
    faulthandler.enable(_fault_file)

    def excepthook(t, v, tb):
        logging.getLogger("luftbild_editor").error("Unerwarteter Fehler", exc_info=(t, v, tb))
        if QApplication.instance():
            QMessageBox.critical(None, "Fehler", f"{t.__name__}: {v}\n\nProtokoll: {path}")

    sys.excepthook = excepthook
    mw.LOG_FILE = path
    return path


def _viewport_contrast(win) -> float:
    """Standardabweichung der sichtbaren Pixel in der Bildansicht (0 = leer)."""
    import numpy as np
    from PySide6.QtGui import QImage
    img = win.view.viewport().grab().toImage().convertToFormat(QImage.Format_Grayscale8)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())
    return float(a[:, :img.width()].std())


def self_test(win, app, image, report) -> int:
    """Startet das Programm, lädt ein Bild und prüft, ob die Vorschau sichtbar ist."""
    from PySide6.QtCore import QThreadPool

    def wait(seconds=60):
        end = time.time() + seconds
        while time.time() < end:
            QThreadPool.globalInstance().waitForDone(100)
            app.processEvents()
            if QThreadPool.globalInstance().activeThreadCount() == 0:
                for _ in range(20):
                    app.processEvents()
                    time.sleep(0.02)
                if QThreadPool.globalInstance().activeThreadCount() == 0:
                    return

    lines, ok = [], True
    shots = os.path.dirname(os.path.abspath(report))

    def check(name, cond, info=""):
        nonlocal ok
        ok &= bool(cond)
        lines.append(f"{'OK    ' if cond else 'FEHLER'} {name} {info}")

    def shot(name):
        win.grab().save(os.path.join(shots, f"selbsttest_{name}.png"))

    t0 = time.time()
    win.add_files([image])
    wait(120)
    check("Bild geladen", win.doc is not None, f"({time.time() - t0:.1f} s)")
    win.tabs.setCurrentIndex(0)
    win._on_tab(0)
    wait()
    c = _viewport_contrast(win)
    check("Bild im Zuschneiden sichtbar", c > 3, f"(Kontrast {c:.1f})")
    shot("1_zuschneiden")
    w, h = win.doc.size
    for sb, v in zip(win.spin_crop, (w // 50, 0, w - w // 25, h - h // 40)):
        sb.setValue(v)
    win.sl_rotation.setValue(0.3)
    win.sl_rotation.valueChanged.emit(0.3)
    wait()
    t0 = time.time()
    win.tabs.setCurrentIndex(1)
    wait()
    check("Vorschau berechnet", not win.view.pix_item.pixmap().isNull(),
          f"({time.time() - t0:.1f} s inkl. Drehung)")
    check("Histogramm berechnet", win.histogram._after is not None)
    c = _viewport_contrast(win)
    check("Vorschau sichtbar", c > 3, f"(Kontrast {c:.1f})")
    shot("2_verbessern")
    win._apply_preset_item(win.preset_list.item(1))
    wait()
    check("Voreinstellung angewendet", win.entry.settings.stretch)
    c = _viewport_contrast(win)
    check("Vorschau nach Voreinstellung sichtbar", c > 3, f"(Kontrast {c:.1f})")
    shot("3_voreinstellung")
    win.view.actual_size()
    wait()
    win._render_detail()
    wait()
    check("Detailansicht 100 %", win.view.detail_item.isVisible())
    shot("4_detail")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    # Name für Einstellungen/Protokoll bleibt gleich, angezeigt wird APP_NAME
    app.setApplicationName("Luftbild-Editor")
    app.setOrganizationName("Luftbild-Editor")
    app.setApplicationDisplayName(theme.APP_NAME)
    if theme.logo_path():
        app.setWindowIcon(QIcon(theme.logo_path()))
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    log_path = setup_logging()
    logging.getLogger("luftbild_editor").info("Start Version %s, Protokoll %s",
                                              __version__, log_path)
    win = mw.MainWindow()
    win.show()
    if len(argv) >= 4 and argv[1] == "--selbsttest":
        return self_test(win, app, argv[2], argv[3])
    if len(argv) > 1:
        win.add_files(argv[1:])
    return app.exec()
