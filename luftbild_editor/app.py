"""Programmstart."""
import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


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


def main(argv=None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("Luftbild-Editor")
    app.setOrganizationName("Luftbild-Editor")
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    win = MainWindow()
    win.show()
    if len(argv) > 1:
        win.add_files(argv[1:])
    return app.exec()
