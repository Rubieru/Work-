import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QThreadPool  # noqa: E402

from luftbild_editor import processing as P  # noqa: E402
from tests.sample import synthetic_aerial  # noqa: E402


def wait(app, ms=300):
    for _ in range(200):
        QThreadPool.globalInstance().waitForDone(ms)
        app.processEvents()
        if QThreadPool.globalInstance().activeThreadCount() == 0:
            app.processEvents()
            return
    raise TimeoutError


@pytest.fixture(scope="module")
def app():
    from luftbild_editor.app import dark_palette
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a.setStyle("Fusion")
    a.setPalette(dark_palette())
    return a


def test_full_workflow(app, tmp_path, monkeypatch):
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    src = tmp_path / "Luftbild 1953.tif"
    P.save_image(str(src), P.to_uint(synthetic_aerial(1200, 900), 16), "tif16", 600)

    win = MainWindow()
    win.show()
    win.add_files([str(src)])
    wait(app)
    assert win.doc is not None and win.doc.size == (1200, 900)

    # 1) Zuschneiden: per Zahlenfeld pixelgenau
    win.tabs.setCurrentIndex(0)
    for sb, v in zip(win.spin_crop, (40, 40, 1120, 820)):
        sb.setValue(v)
    assert win.entry.settings.crop == (40, 40, 1120, 820)
    win.sl_rotation.setValue(0.5)
    win.sl_rotation.valueChanged.emit(0.5)
    assert win.entry.settings.rotation == 0.5

    # 2) Verbessern: Voreinstellung + Regler
    win.tabs.setCurrentIndex(1)
    wait(app)
    item = next(win.preset_list.item(i) for i in range(win.preset_list.count())
                if win.preset_list.item(i).text() == "Kräftig")
    win._apply_preset_item(item)
    wait(app)
    assert win.entry.settings.stretch and win.entry.settings.sharpen_amount > 0
    win.sliders["sharpen_amount"].reset()
    QCoreApplication.processEvents()
    win._render_preview()
    wait(app)
    assert win.histogram._after is not None

    # Rückgängig stellt die Voreinstellung wieder her
    win._commit_history()
    win.undo()
    assert win.entry.settings.sharpen_amount == pytest.approx(1.2)

    # Zoom auf 100 % erzeugt Detailansicht in voller Auflösung
    win.view.actual_size()
    win._render_detail()
    wait(app)

    # 3) Speichern (ohne Dialog)
    out = tmp_path / "ergebnis.tif"
    P.export(win.doc, win.entry.settings, str(out), "tif16")
    back = P.load_image(str(out))
    assert back.data.shape == (820, 1120)
    win.close()
