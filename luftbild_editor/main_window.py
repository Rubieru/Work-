"""Hauptfenster des Luftbild-Editors."""
from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import (QEvent, QObject, QRunnable, QSize, QStandardPaths, Qt,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import QAction, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QCheckBox, QComboBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QProgressDialog,
                               QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QStackedWidget, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from . import processing as P
from .presets import BUILTIN_PRESETS, load_user_presets, save_user_presets
from .widgets import HistogramWidget, ImageView, LabeledSlider

PREVIEW_LONG_SIDE = 2000
THUMB_LONG_SIDE = 128
IMAGE_FILTER = "Bilder (*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.jp2);;Alle Dateien (*)"


def gray_qimage(arr8: np.ndarray) -> QImage:
    arr8 = np.ascontiguousarray(arr8)
    h, w = arr8.shape
    return QImage(arr8.data, w, h, w, QImage.Format_Grayscale8).copy()


@dataclass
class ImageEntry:
    path: str
    settings: P.Settings = field(default_factory=P.Settings)
    history: list = field(default_factory=lambda: [P.Settings()])
    hist_idx: int = 0


class _Signals(QObject):
    result = Signal(str, int, object)
    progress = Signal(int, int)


class _Job(QRunnable):
    def __init__(self, signals, kind, gen, fn):
        super().__init__()
        self.signals, self.kind, self.gen, self.fn = signals, kind, gen, fn

    def run(self):
        try:
            res = self.fn()
            self.signals.result.emit(self.kind, self.gen, res)
        except Exception as exc:  # Fehler im Hintergrund an die Oberfläche melden
            traceback.print_exc()
            self.signals.result.emit("error:" + self.kind, self.gen, exc)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Luftbild-Editor")
        self.resize(1500, 950)
        self.setAcceptDrops(True)

        self.entries: list[ImageEntry] = []
        self.entry: ImageEntry | None = None
        self.doc: P.Document | None = None
        self._crop_qimage = None
        self._show_before = False
        self._gen = {"preview": 0, "detail": 0, "thumbs": 0, "load": 0}
        self._busy = {"preview": False, "detail": False}
        self._pending = {"preview": False, "detail": False}
        self._preview_scale = 1.0
        self._last_geometry = None
        self._progress = None

        self.pool = QThreadPool.globalInstance()
        self.sig = _Signals()
        self.sig.result.connect(self._on_result)
        self.sig.progress.connect(self._on_progress)

        data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        self.preset_dir = data_dir or os.path.expanduser("~/.luftbild-editor")
        self.user_presets = load_user_presets(self.preset_dir)

        self._build_ui()
        self._build_actions()

        self._t_render = self._timer(25, self._render_preview)
        self._t_detail = self._timer(90, self._render_detail)
        self._t_history = self._timer(600, self._commit_history)
        self._t_thumbs = self._timer(400, self._render_thumbs)
        self.view.viewChanged.connect(self._on_view_changed)

        QApplication.instance().installEventFilter(self)
        self._update_enabled()

    def _timer(self, ms, fn):
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(ms)
        t.timeout.connect(fn)
        return t

    # ================================================================ UI
    def _build_ui(self):
        # ---- links: Bilder + Voreinstellungen
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.addWidget(self._heading("Bilder"))
        self.image_list = QListWidget()
        self.image_list.setMaximumHeight(170)
        self.image_list.currentRowChanged.connect(self._select_image)
        ll.addWidget(self.image_list)
        open_btn = QPushButton("Bilder öffnen…")
        open_btn.clicked.connect(self.open_files)
        ll.addWidget(open_btn)

        ll.addSpacing(8)
        ll.addWidget(self._heading("Voreinstellungen"))
        hint = QLabel("Anklicken zum Ausprobieren – die Vorschau zeigt Ihr Bild.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa;")
        ll.addWidget(hint)
        self.preset_list = QListWidget()
        self.preset_list.setViewMode(QListWidget.IconMode)
        self.preset_list.setIconSize(QSize(THUMB_LONG_SIDE, THUMB_LONG_SIDE * 3 // 4))
        self.preset_list.setGridSize(QSize(THUMB_LONG_SIDE + 12, THUMB_LONG_SIDE * 3 // 4 + 30))
        self.preset_list.setResizeMode(QListWidget.Adjust)
        self.preset_list.setMovement(QListWidget.Static)
        self.preset_list.setWordWrap(True)
        self.preset_list.itemClicked.connect(self._apply_preset_item)
        self.preset_list.currentItemChanged.connect(lambda *_: self._update_enabled())
        ll.addWidget(self.preset_list, 1)
        row = QHBoxLayout()
        self.btn_save_preset = QPushButton("Aktuelle speichern…")
        self.btn_save_preset.clicked.connect(self.save_preset)
        self.btn_del_preset = QPushButton("Entfernen")
        self.btn_del_preset.clicked.connect(self.delete_preset)
        row.addWidget(self.btn_save_preset)
        row.addWidget(self.btn_del_preset)
        ll.addLayout(row)
        self._fill_preset_list()

        # ---- Mitte: Ansicht
        self.view = ImageView()
        self.view.cropChanged.connect(self._on_view_crop)
        self.view.pixelHovered.connect(self._on_hover)
        empty = QLabel("Luftbild hierher ziehen\noder „Bilder öffnen…“ klicken")
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet("color: #888; font-size: 20px;")
        self.center = QStackedWidget()
        self.center.addWidget(empty)
        self.center.addWidget(self.view)

        # ---- rechts: Schritte
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll(self._build_crop_tab()), "1  Zuschneiden")
        self.tabs.addTab(self._scroll(self._build_edit_tab()), "2  Verbessern")
        self.tabs.currentChanged.connect(self._on_tab)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 6)
        rl.addWidget(self.tabs, 1)
        self.btn_export = QPushButton("3  Bild speichern…")
        self.btn_export.setMinimumHeight(40)
        self.btn_export.setStyleSheet(
            "QPushButton { background: #ffc400; color: #111; font-weight: bold; border-radius: 4px; }"
            "QPushButton:disabled { background: #555; color: #888; }")
        self.btn_export.clicked.connect(self.export_current)
        rl.addWidget(self.btn_export)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.center)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([340, 900, 360])
        left.setMinimumWidth(250)
        right.setMinimumWidth(330)
        self.setCentralWidget(split)

        # ---- Statusleiste
        self.lbl_pos = QLabel()
        self.lbl_size = QLabel()
        self.lbl_zoom = QLabel()
        for w in (self.lbl_pos, self.lbl_size, self.lbl_zoom):
            w.setMinimumWidth(130)
            self.statusBar().addPermanentWidget(w)

    @staticmethod
    def _heading(text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        return lbl

    @staticmethod
    def _scroll(w):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setWidget(w)
        return sa

    def _info(self, text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #aaa;")
        return lbl

    def _build_crop_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._info(
            "Rahmen mit der linken Maustaste aufziehen, an Ecken und Kanten anpassen "
            "oder die Werte unten exakt eingeben.\n\n"
            "Mausrad: zoomen (bei starker Vergrößerung erscheint ein Pixelraster). "
            "Rechte Maustaste: Bild verschieben."))

        g = QGroupBox("Ausschnitt (Pixel)")
        form = QFormLayout(g)
        self.spin_crop = []
        for name in ("Links (X)", "Oben (Y)", "Breite", "Höhe"):
            sb = QSpinBox()
            sb.setRange(0, 1_000_000)
            sb.setSuffix(" px")
            sb.setKeyboardTracking(False)
            sb.valueChanged.connect(self._on_crop_spin)
            form.addRow(name, sb)
            self.spin_crop.append(sb)
        self.aspect_box = QComboBox()
        for label, val in (("Frei", None), ("Wie Original", "orig"), ("1 : 1", 1.0),
                           ("4 : 3", 4 / 3), ("3 : 4", 3 / 4), ("3 : 2", 3 / 2),
                           ("2 : 3", 2 / 3)):
            self.aspect_box.addItem(label, val)
        self.aspect_box.currentIndexChanged.connect(self._on_aspect)
        form.addRow("Seitenverhältnis", self.aspect_box)
        btn_full = QPushButton("Ganzes Bild")
        btn_full.clicked.connect(lambda: self._set_crop(None))
        form.addRow(btn_full)
        lay.addWidget(g)

        g2 = QGroupBox("Ausrichten")
        l2 = QVBoxLayout(g2)
        self.sl_rotation = LabeledSlider(
            "Drehen", -10, 10, 0.05, 0.0, decimals=2, suffix=" °",
            tooltip="Positive Werte drehen im Uhrzeigersinn. Mit dem Raster lassen sich "
                    "Straßen oder Feldgrenzen gerade ausrichten.")
        self.sl_rotation.valueChanged.connect(self._on_rotation)
        l2.addWidget(self.sl_rotation)
        self.chk_grid = QCheckBox("Raster anzeigen")
        self.chk_grid.toggled.connect(self._on_grid)
        l2.addWidget(self.chk_grid)
        lay.addWidget(g2)

        nxt = QPushButton("Weiter zu  2  Verbessern  →")
        nxt.clicked.connect(lambda: self.tabs.setCurrentIndex(1))
        lay.addWidget(nxt)
        lay.addStretch(1)
        return w

    def _build_edit_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.histogram = HistogramWidget()
        lay.addWidget(self.histogram)
        lay.addWidget(self._info(
            "Leertaste gedrückt halten = Original zeigen. Zum Beurteilen der Schärfe "
            "hineinzoomen – vergrößerte Ausschnitte werden in voller Auflösung berechnet."))

        self.sliders: dict[str, LabeledSlider] = {}

        def add(layout, key, *args, **kw):
            sl = LabeledSlider(*args, **kw)
            sl.valueChanged.connect(lambda v, k=key: self._set_value(k, v))
            layout.addWidget(sl)
            self.sliders[key] = sl
            return sl

        g = QGroupBox("Kontrast – Histogrammstreckung")
        gl = QVBoxLayout(g)
        self.chk_stretch = QCheckBox("Histogramm strecken")
        self.chk_stretch.setToolTip(
            "Spreizt die Grauwerte auf den vollen Bereich von Schwarz bis Weiß. "
            "Die wichtigste Verbesserung für flaue historische Luftbilder.")
        self.chk_stretch.toggled.connect(lambda v: self._set_value("stretch", v))
        gl.addWidget(self.chk_stretch)
        add(gl, "clip_black", "Schwarz abschneiden", 0, 5, 0.05, 0.5, suffix=" %",
            tooltip="Anteil der dunkelsten Pixel, die ganz schwarz werden.")
        add(gl, "clip_white", "Weiß abschneiden", 0, 5, 0.05, 0.5, suffix=" %",
            tooltip="Anteil der hellsten Pixel, die ganz weiß werden.")
        add(gl, "midtones", "Mitteltöne  (− dunkler / + heller)", -1, 1, 0.01, 0.0,
            decimals=0, display_factor=100,
            tooltip="Hellt die mittleren Grautöne auf oder dunkelt sie ab, "
                    "ohne Schwarz und Weiß zu verändern.")
        add(gl, "clarity", "Lokaler Kontrast", 0, 1, 0.01, 0.0, decimals=0, suffix=" %",
            display_factor=100,
            tooltip="Verstärkt Kontraste zwischen benachbarten Flächen "
                    "(Felder, Wald, Bebauung).")
        lay.addWidget(g)

        g = QGroupBox("Schärfen")
        gl = QVBoxLayout(g)
        add(gl, "sharpen_amount", "Stärke", 0, 3, 0.05, 0.0, decimals=0, suffix=" %",
            display_factor=100, tooltip="Wie stark Kanten betont werden.")
        add(gl, "sharpen_radius", "Radius", 0.3, 5, 0.1, 1.0, decimals=1, suffix=" px",
            tooltip="Größe der betonten Details. Klein = feine Details, "
                    "groß = gröbere Strukturen.")
        add(gl, "sharpen_threshold", "Schwelle", 0, 0.1, 1 / 510, 0.0, decimals=1,
            display_factor=255, suffix=" Stufen",
            tooltip="Schwache Helligkeitsunterschiede (z. B. Filmkorn) werden nicht "
                    "geschärft. Erhöhen, wenn das Korn zu stark wird.")
        lay.addWidget(g)

        g = QGroupBox("Bildfehler")
        gl = QVBoxLayout(g)
        add(gl, "denoise", "Filmkorn reduzieren", 0, 1, 0.01, 0.0, decimals=0, suffix=" %",
            display_factor=100, tooltip="Glättet Korn und Rauschen, Kanten bleiben erhalten.")
        add(gl, "flatten", "Ausleuchtung ausgleichen", 0, 1, 0.01, 0.0, decimals=0,
            suffix=" %", display_factor=100,
            tooltip="Gleicht dunkle Bildränder und Helligkeitsverläufe "
                    "(Vignettierung des Objektivs) aus.")
        self.chk_invert = QCheckBox("Negativ umkehren")
        self.chk_invert.toggled.connect(lambda v: self._set_value("invert", v))
        gl.addWidget(self.chk_invert)
        lay.addWidget(g)

        reset = QPushButton("Alle Regler zurücksetzen")
        reset.clicked.connect(lambda: self._apply_tonal({}))
        lay.addWidget(reset)
        lay.addStretch(1)
        return w

    def _build_actions(self):
        tb = self.addToolBar("Werkzeuge")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        m_file = self.menuBar().addMenu("&Datei")
        m_edit = self.menuBar().addMenu("&Bearbeiten")
        m_view = self.menuBar().addMenu("&Ansicht")

        def act(text, slot, shortcut=None, menu=None, toolbar=False):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcuts(shortcut if isinstance(shortcut, list) else [shortcut])
            if menu:
                menu.addAction(a)
            if toolbar:
                tb.addAction(a)
            return a

        act("Bilder öffnen…", self.open_files, QKeySequence.Open, m_file, True)
        self.a_export = act("Bild speichern…", self.export_current, QKeySequence.Save,
                            m_file, True)
        self.a_export_all = act("Alle Bilder speichern…", self.export_all, None, m_file)
        m_file.addSeparator()
        self.a_close = act("Bild aus Liste entfernen", self.close_current, None, m_file)
        m_file.addSeparator()
        act("Beenden", self.close, QKeySequence.Quit, m_file)
        tb.addSeparator()
        self.a_undo = act("Rückgängig", self.undo, QKeySequence.Undo, m_edit, True)
        self.a_redo = act("Wiederholen", self.redo,
                          [QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")], m_edit, True)
        m_edit.addSeparator()
        self.a_apply_all = act("Regler-Einstellungen auf alle Bilder übertragen",
                               self.apply_to_all, None, m_edit)
        tb.addSeparator()
        act("Einpassen", self.view.fit, QKeySequence("Ctrl+0"), m_view, True)
        act("100 %", self.view.actual_size, QKeySequence("Ctrl+1"), m_view, True)
        act("Vergrößern", lambda: self.view.zoom_by(1.25),
            [QKeySequence("Ctrl++"), QKeySequence("Ctrl+=")], m_view)
        act("Verkleinern", lambda: self.view.zoom_by(0.8), QKeySequence("Ctrl+-"), m_view)
        tb.addSeparator()
        self.btn_before = QToolButton()
        self.btn_before.setText("Vorher zeigen (Leertaste halten)")
        self.btn_before.pressed.connect(lambda: self._set_before(True))
        self.btn_before.released.connect(lambda: self._set_before(False))
        tb.addWidget(self.btn_before)

    # ================================================================ Dateien
    def open_files(self):
        start = os.path.dirname(self.entry.path) if self.entry else ""
        paths, _ = QFileDialog.getOpenFileNames(self, "Luftbilder öffnen", start, IMAGE_FILTER)
        self.add_files(paths)

    def add_files(self, paths):
        known = {e.path for e in self.entries}
        first_new = None
        for p in paths:
            p = os.path.abspath(p)
            if p in known:
                continue
            self.entries.append(ImageEntry(p))
            item = QListWidgetItem(os.path.basename(p))
            item.setToolTip(p)
            self.image_list.addItem(item)
            if first_new is None:
                first_new = len(self.entries) - 1
        if first_new is not None:
            self.image_list.setCurrentRow(first_new)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _select_image(self, row):
        if row < 0 or row >= len(self.entries):
            return
        self.entry = self.entries[row]
        self.doc = None
        self._crop_qimage = None
        self._last_geometry = None
        self.view.clear()
        self.center.setCurrentIndex(1)
        self.statusBar().showMessage(f"Lade {os.path.basename(self.entry.path)} …")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        path = self.entry.path

        def load():
            doc = P.Document.open(path)
            return doc, gray_qimage(P.display_8bit(doc.original))

        self._submit("load", load)

    def close_current(self):
        row = self.image_list.currentRow()
        if row < 0:
            return
        self.entries.pop(row)
        self.image_list.takeItem(row)
        if not self.entries:
            self.entry = self.doc = None
            self.view.clear()
            self.center.setCurrentIndex(0)
            self._update_enabled()

    # ================================================================ Hintergrund
    def _submit(self, kind, fn):
        self._gen[kind] = self._gen.get(kind, 0) + 1
        if kind in self._busy:
            self._busy[kind] = True
        self.pool.start(_Job(self.sig, kind, self._gen[kind], fn))

    def _on_result(self, kind, gen, res):
        if kind.startswith("error:"):
            base = kind[6:]
            if base in self._busy:
                self._busy[base] = False
            if base in ("load", "export"):
                QApplication.restoreOverrideCursor()
                if self._progress:
                    self._progress.close()
                    self._progress = None
                QMessageBox.critical(self, "Fehler", f"{res}")
                self.statusBar().clearMessage()
            return
        if kind in self._busy:
            self._busy[kind] = False
            if self._pending[kind]:
                self._pending[kind] = False
                (self._render_preview if kind == "preview" else self._render_detail)()
        if gen != self._gen.get(kind):
            return  # veraltet
        getattr(self, "_done_" + kind)(res)

    def _done_load(self, res):
        QApplication.restoreOverrideCursor()
        self.doc, self._crop_qimage = res
        w, h = self.doc.size
        for sb in self.spin_crop:
            sb.blockSignals(True)
        self.spin_crop[0].setRange(0, w - 1)
        self.spin_crop[1].setRange(0, h - 1)
        self.spin_crop[2].setRange(1, w)
        self.spin_crop[3].setRange(1, h)
        for sb in self.spin_crop:
            sb.blockSignals(False)
        bits = f"{self.doc.bits} Bit"
        dpi = f", {self.doc.dpi:.0f} dpi" if self.doc.dpi else ""
        self.statusBar().showMessage(
            f"{os.path.basename(self.entry.path)}: {w} × {h} px, {bits}{dpi}", 8000)
        self._sync_controls()
        self._update_enabled()
        self._on_tab(self.tabs.currentIndex())
        self._t_thumbs.start(0)

    # ================================================================ Rendern
    def _effective(self) -> P.Settings:
        s = self.entry.settings
        return P.neutral(s) if self._show_before else s

    def _render_preview(self):
        if not self.doc or self.tabs.currentIndex() != 1:
            return
        if self._busy["preview"]:
            self._pending["preview"] = True
            return
        doc, s = self.doc, self._effective()
        geometry = (s.rotation, s.crop)
        if geometry != self._last_geometry:
            self.view.clear_detail()
            self._last_geometry = geometry

        def job():
            prep = doc.prepare(s)
            levels = P.compute_levels(prep, s)
            scale = min(1.0, PREVIEW_LONG_SIDE / prep.long_side)
            img = P.render(prep, s, None, scale, levels)
            return dict(qimg=gray_qimage(P.to_uint(img, 8)), size=prep.size, scale=scale,
                        before=P.histogram(prep.stats), after=P.histogram(img),
                        levels=levels)

        self._submit("preview", job)
        self._t_detail.start(0)

    def _done_preview(self, r):
        if self.tabs.currentIndex() != 1:
            return
        self._preview_scale = r["scale"]
        self.view.set_image(r["qimg"], r["size"], r["scale"])
        self.histogram.set_data(r["before"], r["after"], r["levels"])
        self._update_zoom_label()

    def _render_detail(self):
        if not self.doc or self.tabs.currentIndex() != 1 or self.view.mode != "edit":
            return
        z = self.view.zoom()
        if z <= self._preview_scale * 1.15:
            self._gen["detail"] += 1
            self.view.clear_detail()
            return
        if self._busy["detail"]:
            self._pending["detail"] = True
            return
        vr = self.view.visible_rect()
        if vr.isEmpty():
            return
        vr = vr.adjusted(-vr.width() * 0.1, -vr.height() * 0.1, vr.width() * 0.1,
                         vr.height() * 0.1).intersected(self.view.sceneRect())
        x, y = int(vr.left()), int(vr.top())
        w = int(np.ceil(vr.right())) - x
        h = int(np.ceil(vr.bottom())) - y
        if w < 1 or h < 1:
            return
        scale = min(1.0, z)
        doc, s = self.doc, self._effective()

        def job():
            prep = doc.prepare(s)
            levels = P.compute_levels(prep, s)
            img = P.render(prep, s, (x, y, w, h), scale, levels)
            return dict(qimg=gray_qimage(P.to_uint(img, 8)), x=x, y=y,
                        scale=img.shape[1] / w, geometry=(s.rotation, s.crop))

        self._submit("detail", job)

    def _done_detail(self, r):
        if self.tabs.currentIndex() != 1 or r["geometry"] != self._last_geometry:
            return
        self.view.set_detail(r["qimg"], r["x"], r["y"], r["scale"])

    def _render_thumbs(self):
        if not self.doc:
            return
        doc, base = self.doc, self.entry.settings
        presets = self._all_presets()

        def job():
            geo = P.Settings(rotation=base.rotation, crop=base.crop)
            prep = doc.prepare(geo)
            scale = min(1.0, THUMB_LONG_SIDE / prep.long_side)
            out = {}
            for name, values in presets.items():
                s = P.with_tonal(geo, values)
                out[name] = gray_qimage(P.to_uint(P.render(prep, s, None, scale), 8))
            return out

        self._submit("thumbs", job)

    def _done_thumbs(self, thumbs):
        for i in range(self.preset_list.count()):
            item = self.preset_list.item(i)
            q = thumbs.get(item.data(Qt.UserRole))
            if q is not None:
                item.setIcon(QIcon(QPixmap.fromImage(q)))

    def _on_view_changed(self):
        self._update_zoom_label()
        if self.view.mode == "edit":
            self._t_detail.start()

    # ================================================================ Einstellungen
    def _set_settings(self, s: P.Settings, history=True, sync=False):
        if not self.entry:
            return
        geo_changed = (s.rotation, s.crop) != (self.entry.settings.rotation,
                                               self.entry.settings.crop)
        self.entry.settings = s
        if sync:
            self._sync_controls()
        if history:
            self._t_history.start()
        if geo_changed:
            self._t_thumbs.start()
        self._t_render.start()

    def _set_value(self, key, value):
        if not self.entry:
            return
        if getattr(self.entry.settings, key) == value:
            return
        self.preset_list.clearSelection()
        self._set_settings(replace(self.entry.settings, **{key: value}))
        if key == "stretch":
            self._update_enabled()

    def _apply_tonal(self, values):
        if self.entry:
            self._set_settings(P.with_tonal(self.entry.settings, values), sync=True)
            self._t_history.start(0)

    def _sync_controls(self):
        s = self.entry.settings if self.entry else P.Settings()
        for key, sl in self.sliders.items():
            sl.setValue(getattr(s, key))
        for chk, key in ((self.chk_stretch, "stretch"), (self.chk_invert, "invert")):
            chk.blockSignals(True)
            chk.setChecked(getattr(s, key))
            chk.blockSignals(False)
        self.sl_rotation.setValue(s.rotation)
        self._sync_crop_controls()
        self._update_enabled()

    def _sync_crop_controls(self):
        if not self.doc:
            return
        w, h = self.doc.size
        crop = P.clamp_crop(self.entry.settings.crop, w, h)
        for sb, v in zip(self.spin_crop, crop):
            sb.blockSignals(True)
            sb.setValue(v)
            sb.blockSignals(False)
        if self.view.mode == "crop":
            self.view.set_crop(crop)
            self.view.set_display_rotation(self.entry.settings.rotation)
        self.lbl_size.setText(f"Ausschnitt {crop[2]} × {crop[3]} px")

    def _update_enabled(self):
        has = self.doc is not None
        for w in (self.tabs, self.btn_export, self.btn_save_preset, self.preset_list,
                  self.btn_before):
            w.setEnabled(has)
        for a in (self.a_export, self.a_export_all, self.a_apply_all, self.a_close):
            a.setEnabled(has)
        e = self.entry
        self.a_undo.setEnabled(bool(e and e.hist_idx > 0))
        self.a_redo.setEnabled(bool(e and e.hist_idx < len(e.history) - 1))
        stretch = bool(e and e.settings.stretch)
        for k in ("clip_black", "clip_white"):
            self.sliders[k].setEnabled(stretch)
        item = self.preset_list.currentItem()
        self.btn_del_preset.setEnabled(
            bool(item and item.isSelected() and item.data(Qt.UserRole) in self.user_presets))

    # ---- Verlauf
    def _commit_history(self):
        e = self.entry
        if not e or e.history[e.hist_idx] == e.settings:
            return
        del e.history[e.hist_idx + 1:]
        e.history.append(e.settings)
        e.hist_idx = len(e.history) - 1
        if len(e.history) > 200:
            e.history.pop(0)
            e.hist_idx -= 1
        self._update_enabled()

    def undo(self):
        self._commit_history()
        e = self.entry
        if e and e.hist_idx > 0:
            e.hist_idx -= 1
            self._set_settings(e.history[e.hist_idx], history=False, sync=True)

    def redo(self):
        e = self.entry
        if e and e.hist_idx < len(e.history) - 1:
            e.hist_idx += 1
            self._set_settings(e.history[e.hist_idx], history=False, sync=True)

    # ---- Zuschneiden
    def _on_tab(self, idx):
        if not self.doc:
            return
        if idx == 0:
            self._gen["preview"] += 1
            self._gen["detail"] += 1
            self.view.clear_detail()
            self.view.set_mode("crop")
            self.view.set_image(self._crop_qimage, self.doc.size, 1.0,
                                self.entry.settings.rotation)
            self.view.fit()
            self._sync_crop_controls()
        else:
            self.view.set_mode("edit")
            self._last_geometry = None
            self.view.clear()
            self.statusBar().showMessage("Berechne Vorschau …", 2000)
            self._render_preview()
        self._update_zoom_label()

    def _set_crop(self, crop):
        if not self.doc:
            return
        w, h = self.doc.size
        if crop is not None:
            crop = P.clamp_crop(crop, w, h)
            if crop == (0, 0, w, h):
                crop = None
        self._set_settings(replace(self.entry.settings, crop=crop))
        self._sync_crop_controls()

    def _on_view_crop(self, crop):
        self._set_crop(crop)

    def _on_crop_spin(self, _):
        crop = tuple(sb.value() for sb in self.spin_crop)
        a = self.view.aspect
        if a and self.sender() in (self.spin_crop[2], self.spin_crop[3]):
            x, y, w, h = crop
            if self.sender() is self.spin_crop[2]:
                h = round(w / a)
            else:
                w = round(h * a)
            crop = (x, y, w, h)
        self._set_crop(crop)

    def _on_aspect(self, _):
        val = self.aspect_box.currentData()
        if val == "orig" and self.doc:
            w, h = self.doc.size
            val = w / h
        self.view.aspect = val
        if val and self.doc:
            x, y, w, h = P.clamp_crop(self.entry.settings.crop, *self.doc.size)
            if w / h > val:
                nw, nh = round(h * val), h
            else:
                nw, nh = w, round(w / val)
            self._set_crop((x + (w - nw) // 2, y + (h - nh) // 2, nw, nh))

    def _on_rotation(self, v):
        self._set_value("rotation", v)
        if self.view.mode == "crop":
            self.view.set_display_rotation(v)
            if v and not self.chk_grid.isChecked():
                self.chk_grid.setChecked(True)

    def _on_grid(self, on):
        self.view.show_grid = on
        self.view.viewport().update()

    def _on_hover(self, x, y):
        self.lbl_pos.setText(f"x {x}  y {y}")

    def _update_zoom_label(self):
        self.lbl_zoom.setText(f"Zoom {self.view.zoom() * 100:.0f} %")

    # ---- Vorher / Nachher
    def _set_before(self, on):
        if self._show_before != on and self.doc:
            self._show_before = on
            self.btn_before.setDown(on)
            self._render_preview()

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.KeyPress, QEvent.KeyRelease) and e.key() == Qt.Key_Space \
                and not e.isAutoRepeat():
            fw = QApplication.focusWidget()
            if not isinstance(fw, (QAbstractSpinBox, QLineEdit)) and self.isActiveWindow():
                self._set_before(t == QEvent.KeyPress)
                return True
        return super().eventFilter(obj, e)

    # ================================================================ Voreinstellungen
    def _all_presets(self) -> dict:
        d = dict(BUILTIN_PRESETS)
        d.update(self.user_presets)
        return d

    def _fill_preset_list(self):
        self.preset_list.clear()
        for name in self._all_presets():
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, name)
            item.setTextAlignment(Qt.AlignHCenter)
            item.setSizeHint(self.preset_list.gridSize())
            if name in self.user_presets:
                item.setToolTip("Eigene Voreinstellung")
            self.preset_list.addItem(item)

    def _apply_preset_item(self, item):
        values = self._all_presets().get(item.data(Qt.UserRole))
        if values is not None and self.entry:
            self._apply_tonal(values)
            item.setSelected(True)
            self._update_enabled()

    def save_preset(self):
        if not self.entry:
            return
        name, ok = QInputDialog.getText(self, "Voreinstellung speichern",
                                        "Name der Voreinstellung:")
        name = name.strip()
        if not ok or not name:
            return
        if name in BUILTIN_PRESETS:
            QMessageBox.information(self, "Name vergeben",
                                    "Dieser Name gehört zu einer eingebauten Voreinstellung.")
            return
        self.user_presets[name] = P.tonal_dict(self.entry.settings)
        save_user_presets(self.preset_dir, self.user_presets)
        self._fill_preset_list()
        self._render_thumbs()

    def delete_preset(self):
        item = self.preset_list.currentItem()
        name = item.data(Qt.UserRole) if item else None
        if name in self.user_presets:
            del self.user_presets[name]
            save_user_presets(self.preset_dir, self.user_presets)
            self._fill_preset_list()
            self._render_thumbs()

    def apply_to_all(self):
        if not self.entry:
            return
        values = P.tonal_dict(self.entry.settings)
        for e in self.entries:
            if e is not self.entry:
                e.settings = P.with_tonal(e.settings, values)
                del e.history[e.hist_idx + 1:]
                e.history.append(e.settings)
                e.hist_idx = len(e.history) - 1
        self.statusBar().showMessage(
            f"Regler-Einstellungen auf {len(self.entries)} Bilder übertragen "
            "(Zuschnitt bleibt je Bild erhalten).", 6000)

    # ================================================================ Speichern
    def _ask_format(self, title):
        names = [v[0] for v in P.EXPORT_FORMATS.values()]
        name, ok = QInputDialog.getItem(self, title, "Dateiformat:", names, 0, False)
        if not ok:
            return None
        return next(k for k, v in P.EXPORT_FORMATS.items() if v[0] == name)

    def export_current(self):
        if not self.doc:
            return
        self._commit_history()
        stem = os.path.splitext(self.entry.path)[0]
        filters = [v[0] for v in P.EXPORT_FORMATS.values()]
        path, chosen = QFileDialog.getSaveFileName(
            self, "Bild speichern", stem + "_bearbeitet.tif", ";;".join(filters), filters[0])
        if not path:
            return
        fmt = next((k for k, v in P.EXPORT_FORMATS.items() if v[0] == chosen), "tif16")
        ext = P.EXPORT_FORMATS[fmt][1]
        if os.path.splitext(path)[1].lower() not in (ext, ".tiff", ".jpeg"):
            path += ext
        doc, s = self.doc, self.entry.settings
        self._start_progress("Bild wird in voller Auflösung gespeichert …")
        sig = self.sig

        def job():
            P.export(doc, s, path, fmt, progress=lambda i, n: sig.progress.emit(i, n))
            return [path]

        self._submit("export", job)

    def export_all(self):
        if not self.entries:
            return
        self._commit_history()
        folder = QFileDialog.getExistingDirectory(self, "Zielordner wählen",
                                                  os.path.dirname(self.entry.path))
        if not folder:
            return
        fmt = self._ask_format("Alle Bilder speichern")
        if not fmt:
            return
        ext = P.EXPORT_FORMATS[fmt][1]
        jobs = [(e.path, e.settings) for e in self.entries]
        current = self.doc
        self._start_progress(f"{len(jobs)} Bilder werden gespeichert …")
        sig = self.sig

        def job():
            written = []
            for i, (src, s) in enumerate(jobs):
                doc = current if current and current.path == src else P.Document.open(src)
                stem = os.path.splitext(os.path.basename(src))[0]
                out = os.path.join(folder, stem + "_bearbeitet" + ext)
                P.export(doc, s, out, fmt)
                written.append(out)
                sig.progress.emit(i + 1, len(jobs))
            return written

        self._submit("export", job)

    def _start_progress(self, text):
        self._progress = QProgressDialog(text, None, 0, 100, self)
        self._progress.setWindowTitle("Speichern")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setValue(0)
        QApplication.setOverrideCursor(Qt.WaitCursor)

    def _on_progress(self, i, n):
        if self._progress:
            self._progress.setValue(int(100 * i / max(n, 1)))

    def _done_export(self, paths):
        QApplication.restoreOverrideCursor()
        if self._progress:
            self._progress.close()
            self._progress = None
        msg = paths[0] if len(paths) == 1 else f"{len(paths)} Bilder in {os.path.dirname(paths[0])}"
        self.statusBar().showMessage(f"Gespeichert: {msg}", 10000)
