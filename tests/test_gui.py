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
    from luftbild_editor.app import app_palette
    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a.setStyle("Fusion")
    a.setPalette(app_palette())
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
                if win.preset_list.item(i).text() == "Streckung + Hochpassfilter")
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
    assert win.entry.settings.sharpen_amount == pytest.approx(0.7)

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


def test_preset_click_in_crop_tab_switches_to_edit(app, tmp_path, monkeypatch):
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    src = tmp_path / "a.tif"
    P.save_image(str(src), P.to_uint(synthetic_aerial(600, 450), 8), "tif8")
    win = MainWindow()
    win.show()
    win.add_files([str(src)])
    wait(app)
    win.tabs.setCurrentIndex(0)
    win._apply_preset_item(win.preset_list.item(1))
    wait(app)
    assert win.tabs.currentIndex() == 1
    assert not win.view.pix_item.pixmap().isNull()
    win.close()


def test_batch(tmp_path):
    from luftbild_editor.batch import BatchJob, run_batch
    from luftbild_editor.presets import BUILTIN_PRESETS
    files = []
    for i in range(3):
        p = tmp_path / f"bild_{i}.tif"
        P.save_image(str(p), P.to_uint(synthetic_aerial(400 + 10 * i, 300, seed=i), 8), "tif8")
        files.append(str(p))
    files.append(str(tmp_path / "fehlt.tif"))
    s = P.with_tonal(P.Settings(crop=(10, 10, 200, 150)), BUILTIN_PRESETS["Streckung + Hochpassfilter"])
    out = tmp_path / "aus"
    written, errors = run_batch(BatchJob(files, str(out), "png", "_b", False), s)
    assert len(written) == 3 and len(errors) == 1
    assert P.load_image(written[0]).data.shape == (300, 400)  # ohne Zuschnitt
    written, _ = run_batch(BatchJob(files[:1], str(out), "png", "_z", True), s)
    assert P.load_image(written[0]).data.shape == (150, 200)  # mit Zuschnitt
    written, errors = run_batch(BatchJob(files[:2], str(out), "png", "_a", False, auto=True), s)
    assert len(written) == 2 and not errors


def test_simple_mode_sliders(app, tmp_path, monkeypatch):
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    src = tmp_path / "a.tif"
    P.save_image(str(src), P.to_uint(synthetic_aerial(600, 450), 8), "tif8")
    win = MainWindow()
    win.show()
    win.add_files([str(src)])
    wait(app)
    win.tabs.setCurrentIndex(1)
    win.btn_simple.setChecked(True)
    win.simple_sliders["contrast"].setValue(0.5)
    win.simple_sliders["contrast"].valueChanged.emit(0.5)
    win.simple_sliders["sharpness"].valueChanged.emit(0.5)
    win.simple_sliders["brightness"].valueChanged.emit(0.2)
    s = win.entry.settings
    assert s.stretch and s.hist_flat == pytest.approx(0.5)
    assert s.sharpen_amount == pytest.approx(0.8) and s.midtones == pytest.approx(0.2)
    # Erweiterte Regler zeigen dieselben Werte
    assert win.sliders["hist_flat"].value() == pytest.approx(0.5)
    # Voreinstellung aktualisiert die einfachen Regler
    win._apply_preset_item(win.preset_list.item(0))
    assert win.simple_sliders["contrast"].value() == 0
    win.close()


def _crop_window(app, tmp_path, monkeypatch, n=2):
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    files = []
    for i in range(n):
        src = tmp_path / f"b{i}.tif"
        P.save_image(str(src), P.to_uint(synthetic_aerial(800 + 20 * i, 600, seed=i), 8), "tif8")
        files.append(str(src))
    win = MainWindow()
    win.show()
    win.chk_lock_size.setChecked(False)
    win.add_files(files)
    wait(app)
    win.tabs.setCurrentIndex(0)
    wait(app)
    return win


def test_crop_drag_does_not_start_thumbnail_storm(app, tmp_path, monkeypatch):
    win = _crop_window(app, tmp_path, monkeypatch, 1)
    calls = []
    orig = win._submit
    monkeypatch.setattr(win, "_submit", lambda kind, fn: (calls.append(kind), orig(kind, fn)))
    for i in range(40):  # wie viele Mausbewegungen beim Ziehen
        win._set_crop((10 + i, 10, 500, 400))
        app.processEvents()
    wait(app)
    assert calls.count("thumbs") == 0
    win.tabs.setCurrentIndex(1)
    from PySide6.QtTest import QTest
    QTest.qWait(600)  # Kacheln starten leicht verzögert
    wait(app)
    assert calls.count("thumbs") == 1
    win.close()


def test_corner_mode_and_previous_size(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QPointF
    win = _crop_window(app, tmp_path, monkeypatch, 2)
    v = win.view
    # Zwei-Klick-Modus direkt über die Ansicht-Logik
    win.btn_corners.setChecked(True)
    assert v.corner_mode
    v._corner_a = QPointF(20, 30)
    v.crop_rect = v.crop_rect  # unverändert
    from PySide6.QtCore import QRectF
    v.crop_rect = QRectF(QPointF(20, 30), QPointF(520, 430)).normalized()
    v.set_corner_mode(False)
    v.cropChanged.emit(v.crop_tuple())
    assert win.entry.settings.crop == (20, 30, 500, 400)
    # nächstes Bild: Größe übernehmen, Position bleibt verschiebbar
    win.image_list.setCurrentRow(1)
    wait(app)
    win.tabs.setCurrentIndex(0)
    win._apply_previous_size()
    x, y, w, h = win.entry.settings.crop
    assert (w, h) == (500, 400)
    assert win.chk_lock_size.isChecked() and v.size_locked
    win._set_crop((5, 7, w, h))
    assert win.entry.settings.crop == (5, 7, 500, 400)
    win.chk_lock_size.setChecked(False)
    win.close()


@pytest.mark.parametrize("tab", [0, 1])
def test_zoom_keeps_point_under_mouse(app, tmp_path, monkeypatch, tab):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    src = tmp_path / "zoom.tif"
    P.save_image(str(src), P.to_uint(synthetic_aerial(1600, 1200), 16), "tif16")
    win = MainWindow()
    win.resize(1200, 800)
    win.show()
    win.add_files([str(src)])
    wait(app)
    win.tabs.setCurrentIndex(tab)
    wait(app)
    view = win.view
    vp = view.viewport()
    view.zoom_by(3.0)   # Bild größer als das Fenster (sonst zentriert Qt es zwangsläufig)
    app.processEvents()
    # Maus nicht in der Mitte – mehrmals hinein- und wieder herauszoomen
    mouse = QPointF(vp.width() // 4, vp.height() * 7 // 10)
    for steps in (3, 2, 4, -2):
        target = view.mapToScene(mouse.toPoint())
        for _ in range(abs(steps)):
            ev = QWheelEvent(mouse, view.mapToGlobal(mouse), QPoint(),
                             QPoint(0, 120 if steps > 0 else -120), Qt.NoButton,
                             Qt.ControlModifier, Qt.NoScrollPhase, False)
            view.wheelEvent(ev)
        app.processEvents()
        now = view.mapFromScene(target)
        # Bildlaufleisten rechnen in ganzen Pixeln – je Schritt höchstens 1 px Rundung
        assert abs(now.x() - mouse.x()) <= 3 and abs(now.y() - mouse.y()) <= 3, (steps, now)

    # ohne Strg: Rad scrollt (hoch/runter senkrecht, seitlich gekippt waagerecht)
    z = view.zoom()
    hb, vb = view.horizontalScrollBar(), view.verticalScrollBar()
    hb.setValue((hb.minimum() + hb.maximum()) // 2)
    vb.setValue((vb.minimum() + vb.maximum()) // 2)
    for delta, bar in ((QPoint(0, -120), vb), (QPoint(-120, 0), hb)):
        before = bar.value()
        ev = QWheelEvent(mouse, view.mapToGlobal(mouse), QPoint(), delta, Qt.NoButton,
                         Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(vp, ev)
        app.processEvents()
        assert bar.value() > before
    assert view.zoom() == pytest.approx(z)
    win.close()


def test_batch_includes_current_image_and_is_not_reported_cancelled(app, tmp_path, monkeypatch):
    from luftbild_editor import batch as B
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    files = []
    for i in range(3):
        f = tmp_path / f"bild{i}.tif"
        P.save_image(str(f), P.to_uint(synthetic_aerial(500, 400, seed=i), 8), "tif8")
        files.append(str(f))
    win = MainWindow()
    win.show()
    win.add_files(files)
    wait(app)
    win.tabs.setCurrentIndex(1)
    wait(app)
    item = next(win.preset_list.item(i) for i in range(win.preset_list.count())
                if win.preset_list.item(i).text() == "Streckung + Hochpassfilter")
    win._apply_preset_item(item)
    wait(app)

    monkeypatch.setattr(B.BatchDialog, "exec", lambda self: B.BatchDialog.Accepted)
    shown = []
    monkeypatch.setattr("luftbild_editor.main_window.QMessageBox.information",
                        lambda *a: shown.append(a[2]))
    monkeypatch.setattr("luftbild_editor.main_window.QMessageBox.warning",
                        lambda *a: shown.append(a[2]))
    win.batch()
    wait(app)
    for _ in range(20):
        app.processEvents()
    out = sorted(os.listdir(tmp_path / "bearbeitet"))
    assert len(out) == 3 and any(n.startswith(os.path.basename(win.entry.path)[:-4])
                                 for n in out)
    assert shown and "3 Bilder gespeichert" in shown[0] and "abgebrochen" not in shown[0]
    win.close()


def test_crop_workflow_carry_frame_and_batch_with_own_crops(app, tmp_path, monkeypatch):
    from luftbild_editor import batch as B
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    monkeypatch.setattr("PySide6.QtCore.QSettings.value",
                        lambda self, key, default=None, type=None: default)
    files = []
    for i in range(3):
        f = tmp_path / f"scan{i}.tif"
        P.save_image(str(f), P.to_uint(synthetic_aerial(600, 500, seed=i), 8), "tif8")
        files.append(str(f))
    win = MainWindow()
    win.show()
    win.add_files(files)
    wait(app)
    win.tabs.setCurrentIndex(0)
    wait(app)

    # Bild 1: Rahmen setzen und Größe festhalten
    win._set_crop((40, 30, 400, 300))
    win.chk_lock_size.setChecked(True)
    # Bild 2: Rahmen erscheint automatisch, wird nur verschoben
    win.image_list.setCurrentRow(1)
    wait(app)
    assert win.entry.settings.crop == (40, 30, 400, 300)
    win._set_crop((100, 120, 400, 300))
    # Bild 3: übernimmt die zuletzt benutzte Stelle
    win.image_list.setCurrentRow(2)
    wait(app)
    assert win.entry.settings.crop == (100, 120, 400, 300)
    assert all(win.image_list.item(i).text().startswith("✂") for i in range(3))

    # Stapelverarbeitung: jedes Bild mit eigenem Rahmen und automatischen Reglern
    def fake_exec(dlg):
        dlg.chk_auto.setChecked(True)
        dlg.geo_mode.setCurrentIndex(dlg.geo_mode.findData("own"))
        return B.BatchDialog.Accepted
    monkeypatch.setattr(B.BatchDialog, "exec", fake_exec)
    monkeypatch.setattr("luftbild_editor.main_window.QMessageBox.information", lambda *a: None)
    win.batch()
    wait(app)
    out_dir = tmp_path / "bearbeitet"
    outs = sorted(os.listdir(out_dir))
    assert len(outs) == 3
    for name in outs:
        img = P.load_image(str(out_dir / name)).data
        assert img.shape == (300, 400) and img.min() >= 1
    # Ausschnitt von Bild 1 stimmt (Stelle 40/30, nicht die von Bild 2)
    src = P.load_image(files[0]).data[30:330, 40:440].astype(float)
    got = P.load_image(str(out_dir / "scan0_bearbeitet.tif")).data.astype(float)
    assert np.corrcoef(src.ravel(), got.ravel())[0, 1] > 0.9

    # Mehrere Bilder markieren und entfernen
    win.image_list.selectAll()
    win.remove_selected()
    assert win.entries == [] and win.image_list.count() == 0 and win.doc is None
    win.close()


def test_remove_some_images_keeps_current(app, tmp_path, monkeypatch):
    from luftbild_editor.main_window import MainWindow
    monkeypatch.setattr("PySide6.QtCore.QStandardPaths.writableLocation",
                        lambda *_: str(tmp_path / "cfg"))
    files = []
    for i in range(4):
        f = tmp_path / f"b{i}.tif"
        P.save_image(str(f), P.to_uint(synthetic_aerial(300, 200, seed=i), 8), "tif8")
        files.append(str(f))
    win = MainWindow()
    win.show()
    win.add_files(files)
    wait(app)
    win.image_list.setCurrentRow(3)
    wait(app)
    for r in (0, 1):
        win.image_list.item(r).setSelected(True)
    win.image_list.item(3).setSelected(False)
    win.remove_selected()
    wait(app)
    assert [os.path.basename(e.path) for e in win.entries] == ["b2.tif", "b3.tif"]
    assert win.entry.path.endswith("b3.tif") and win.image_list.currentRow() == 1
    win.close()


def test_batch_dialog_warns_about_images_without_frame(app, tmp_path, monkeypatch):
    from luftbild_editor import batch as B
    a, b = str(tmp_path / "a.tif"), str(tmp_path / "b.tif")
    geos = {B.path_key(a): (0.0, (0, 0, 10, 10)), B.path_key(b): (0.0, None)}
    asked = []
    monkeypatch.setattr(B.QMessageBox, "question",
                        lambda *args: asked.append(args[2]) or B.QMessageBox.No)
    dlg = B.BatchDialog(None, "a.tif", [a, b], str(tmp_path), geometries=geos)
    dlg._accept()
    assert asked and "b.tif" in asked[0] and "a.tif" not in asked[0]
    assert dlg.result() != B.BatchDialog.Accepted
