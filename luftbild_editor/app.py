"""Programmstart."""
import faulthandler
import logging
import os
import sys
import time

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from . import main_window as mw


def dark_palette() -> QPalette:
    p = QPalette()
    base, window, text = QColor(38, 38, 38), QColor(53, 53, 53), QColor(225, 225, 225)
    for role, color in ((QPalette.Window, window), (QPalette.WindowText, text),
                        (QPalette.Base, base), (QPalette.AlternateBase, window),
                        (QPalette.ToolTipBase, QColor(25, 25, 25)),
                        (QPalette.ToolTipText, text), (QPalette.Text, text),
                        (QPalette.Button, window), (QPalette.ButtonText, text),
                        (QPalette.Highlight, QColor(255, 196, 0)),
                        (QPalette.HighlightedText, QColor(20, 20, 20)),
                        (QPalette.PlaceholderText, QColor(130, 130, 130))):
        p.setColor(role, color)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(120, 120, 120))
    return p


def setup_logging() -> str:
    """Protokoll nach %APPDATA%\\Luftbild-Editor\\protokoll.txt (auch ohne Konsole)."""
    folder = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation) \
        or os.path.expanduser("~/.luftbild-editor")
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


def self_test(win, app, image, report) -> int:
    """Startet das Programm, lädt ein Bild und prüft, ob die Vorschau erscheint."""
    from PySide6.QtCore import QThreadPool

    def wait(seconds=60):
        end = time.time() + seconds
        while time.time() < end:
            QThreadPool.globalInstance().waitForDone(100)
            app.processEvents()
            if QThreadPool.globalInstance().activeThreadCount() == 0:
                for _ in range(10):
                    app.processEvents()
                    time.sleep(0.02)
                if QThreadPool.globalInstance().activeThreadCount() == 0:
                    return

    lines, ok = [], True

    def check(name, cond):
        nonlocal ok
        ok &= bool(cond)
        lines.append(f"{'OK    ' if cond else 'FEHLER'} {name}")

    win.add_files([image])
    wait(120)
    check("Bild geladen", win.doc is not None)
    win.tabs.setCurrentIndex(1)
    wait()
    check("Vorschau angezeigt", not win.view.pix_item.pixmap().isNull())
    check("Histogramm berechnet", win.histogram._after is not None)
    item = win.preset_list.item(1)
    win._apply_preset_item(item)
    wait()
    check("Voreinstellung angewendet", win.entry.settings.stretch)
    check("Vorschau nach Voreinstellung", not win.view.pix_item.pixmap().isNull())
    win.view.actual_size()
    wait()
    win._render_detail()
    wait()
    check("Detailansicht 100 %", win.view.detail_item.isVisible())
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("Luftbild-Editor")
    app.setOrganizationName("Luftbild-Editor")
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
