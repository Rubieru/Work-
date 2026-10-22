"""Hauptfenster des Luftbild-Editors."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
from PySide6.QtCore import (QEvent, QObject, QRunnable, QSettings, QSize, QStandardPaths, Qt,
                            QUrl,
                            QThreadPool, QTimer, Signal)
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox,
                               QComboBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenuBar, QMessageBox,
                               QProgressDialog,
                               QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QStackedWidget, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from . import __version__
from . import processing as P
from . import theme
from .presets import (AUTO, BUILTIN_PRESETS, PRESET_INFO, load_user_presets,
                      save_user_presets)
from .widgets import CurveWidget, HistogramWidget, ImageView, LabeledSlider

PREVIEW_LONG_SIDE = 2000
CROP_DISPLAY_LONG_SIDE = 4096
log = logging.getLogger("luftbild_editor")
LOG_FILE = ""
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
        except BaseException as exc:  # Fehler im Hintergrund an die Oberfläche melden
            log.exception("Fehler bei Hintergrundaufgabe '%s'", self.kind)
            if isinstance(exc, MemoryError):
                exc = MemoryError("Nicht genug Arbeitsspeicher für dieses Bild.")
            self.signals.result.emit("error:" + self.kind, self.gen, exc)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(theme.APP_NAME)
        self.resize(1500, 950)
        self.setAcceptDrops(True)

        self.entries: list[ImageEntry] = []
        self.entry: ImageEntry | None = None
        self.doc: P.Document | None = None
        self._crop_qimage = None
        self._crop_scale = 1.0
        self._crop_levels = (0.0, 1.0)
        self._show_before = False
        self._gen = {"preview": 0, "detail": 0, "thumbs": 0, "load": 0}
        self._busy = {"preview": False, "detail": False, "thumbs": False}
        self._pending = {"preview": False, "detail": False, "thumbs": False}
        self._thumbs_geometry = None
        self._preview_scale = 1.0
        self._last_geometry = None
        self._progress = None
        self._errors_shown = set()
        self._auto_cache = None
        self._last_preset_auto = False
        self._last_crop_size = None

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
        self._t_loading = QTimer(self)
        self._t_loading.setInterval(1000)
        self._t_loading.timeout.connect(self._tick_loading)
        self.view.viewChanged.connect(self._on_view_changed)

        self._on_lock_size(self.chk_lock_size.isChecked())
        QApplication.instance().installEventFilter(self)
        self._update_enabled()

    def _timer(self, ms, fn):
        t = QTimer(self)
        t.setSingleShot(True)
        t.setInterval(ms)
        t.timeout.connect(fn)
        return t

    # ================================================================ UI
    def _build_header(self):
        """Kopfzeile im Stil der Website: Logo, Firmenname, Programmname, Menü."""
        box = QWidget()
        box.setObjectName("header")
        box.setStyleSheet(f"QWidget#header {{ background: {theme.BASE}; }}")
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        row = QHBoxLayout()
        row.setContentsMargins(14, 8, 14, 6)
        if theme.logo_path():
            logo = QLabel()
            logo.setPixmap(QPixmap(theme.logo_path()).scaled(
                52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            row.addWidget(logo)
            row.addSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        company = QLabel(theme.COMPANY)
        company.setStyleSheet(f"color: {theme.ACCENT}; font-size: 22px; font-weight: bold; "
                              "font-style: italic;")
        sub = QLabel(theme.SUBTITLE + " – Zuschneiden, Kontrast und Schärfe "
                     "für historische Luftbilder")
        sub.setStyleSheet(f"color: {theme.GREY_TEXT}; font-size: 13px; font-weight: bold; "
                          "font-style: italic;")
        titles.addWidget(company)
        titles.addWidget(sub)
        row.addLayout(titles)
        row.addStretch(1)
        v.addLayout(row)
        stripe = QWidget()
        stripe.setFixedHeight(4)
        stripe.setStyleSheet(f"background: {theme.ACCENT};")
        v.addWidget(stripe)
        self._menubar = QMenuBar()
        v.addWidget(self._menubar)
        self.setMenuWidget(box)

    def _build_ui(self):
        self._build_header()
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
        hint.setObjectName("hint")
        ll.addWidget(hint)
        self.preset_list = QListWidget()
        self.preset_list.setViewMode(QListWidget.IconMode)
        self.preset_list.setIconSize(QSize(THUMB_LONG_SIDE, THUMB_LONG_SIDE * 3 // 4))
        self.preset_list.setGridSize(QSize(THUMB_LONG_SIDE + 12, THUMB_LONG_SIDE * 3 // 4 + 44))
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
        self.view.cornerStep.connect(self._on_corner_step)
        self.view.pixelHovered.connect(self._on_hover)
        empty = QLabel("Luftbild hierher ziehen\noder „Bilder öffnen…“ klicken")
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet(f"color: #c9d3d1; font-size: 20px; "
                            f"background: {theme.VIEW_BACKGROUND};")
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
        self.btn_export.setObjectName("primary")
        self.btn_export.clicked.connect(self.export_current)
        rl.addWidget(self.btn_export)
        self.btn_batch = QPushButton("Stapelverarbeitung (mehrere Bilder) …")
        self.btn_batch.setToolTip("Die aktuellen Einstellungen auf viele Bilder anwenden "
                                  "und alle auf einmal speichern.")
        self.btn_batch.clicked.connect(self.batch)
        rl.addWidget(self.btn_batch)
        self.chk_nodata = QCheckBox("Grauwerte 1–255 (kein Wert 0 – für Photogrammetrie/GIS)")
        self.chk_nodata.setToolTip(
            "Schwarz wird als 1 statt 0 gespeichert. Viele GIS-Programme werten 0 als "
            "„keine Daten“ und würden dunkle Bildstellen sonst durchsichtig darstellen.")
        self.chk_nodata.setChecked(
            QSettings().value("nodata_safe", True, type=bool))
        self.chk_nodata.toggled.connect(lambda v: QSettings().setValue("nodata_safe", v))
        rl.addWidget(self.chk_nodata)

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
        lbl.setObjectName("heading")
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
        lbl.setObjectName("hint")
        return lbl

    def _build_crop_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(self._info(
            "Schnellster Weg: „Ecken setzen“ klicken, dann obere linke und untere rechte "
            "Ecke anklicken (die Lupe hilft beim genauen Treffen).\n"
            "Danach Ecken oder Kanten ziehen – die gegenüberliegende Seite bleibt fest. "
            "Neuen Rahmen aufziehen: Umschalttaste + ziehen.\n"
            "Strg + Mausrad: zum Mauszeiger zoomen. Mausrad: hoch/runter, seitlich kippen: "
            "links/rechts. Rechte Maustaste: Bild verschieben. Der Zuschnitt gilt "
            "sofort, ohne Bestätigen."))

        g0 = QGroupBox("Rahmen")
        l0 = QVBoxLayout(g0)
        self.btn_corners = QPushButton("Ecken setzen (2 Klicks)")
        self.btn_corners.setObjectName("primary")
        self.btn_corners.setCheckable(True)
        self.btn_corners.toggled.connect(self._on_corner_mode)
        l0.addWidget(self.btn_corners)
        self.btn_prev_size = QPushButton("Größe vom vorigen Bild übernehmen")
        self.btn_prev_size.setToolTip(
            "Übernimmt Breite und Höhe des Rahmens aus dem zuletzt zugeschnittenen Bild. "
            "Danach den Rahmen nur noch an die richtige Stelle schieben – alle Bilder "
            "haben so exakt dieselbe Pixelgröße (wichtig für die Photogrammetrie).")
        self.btn_prev_size.clicked.connect(self._apply_previous_size)
        l0.addWidget(self.btn_prev_size)
        self.chk_lock_size = QCheckBox("Größe festhalten (Rahmen nur verschieben)")
        self.chk_lock_size.setChecked(QSettings().value("lock_crop_size", False, type=bool))
        self.chk_lock_size.toggled.connect(self._on_lock_size)
        l0.addWidget(self.chk_lock_size)
        lay.addWidget(g0)

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
        btn_full.clicked.connect(lambda: self._set_crop(None) if not self.chk_lock_size.isChecked()
                                 else None)
        form.addRow(btn_full)
        lay.addWidget(g)

        g2 = QGroupBox("Ausrichten")
        l2 = QVBoxLayout(g2)
        self.sl_rotation = LabeledSlider(
            "Drehen", -180, 180, 0.05, 0.0, decimals=2, suffix=" °",
            tooltip="Positive Werte drehen im Uhrzeigersinn (−180° bis +180° = volle "
                    "Umdrehung). Für kleine Korrekturen den Wert direkt eintippen oder mit "
                    "den Pfeiltasten in 0,05°-Schritten ändern. Mit dem Raster lassen sich "
                    "Straßen oder Feldgrenzen gerade ausrichten.")
        self.sl_rotation.valueChanged.connect(self._on_rotation)
        l2.addWidget(self.sl_rotation)
        row = QHBoxLayout()
        for text, delta, tip in (("↺ 90°", -90, "90° gegen den Uhrzeigersinn"),
                                 ("↻ 90°", 90, "90° im Uhrzeigersinn"),
                                 ("180°", 180, "Auf den Kopf drehen")):
            b = QPushButton(text)
            b.setToolTip(tip + " (verlustfrei)")
            b.clicked.connect(lambda _=False, d=delta: self._rotate_by(d))
            row.addWidget(b)
        l2.addLayout(row)
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
            "Leertaste gedrückt halten = Original zeigen. Strg + Mausrad = zoomen. Zum "
            "Beurteilen der Schärfe hineinzoomen – vergrößerte Ausschnitte werden in voller Auflösung berechnet."))

        # ---- Umschalter Einfach / Erweitert
        row = QHBoxLayout()
        self.btn_simple = QPushButton("Einfach")
        self.btn_adv = QPushButton("Erweitert")
        group = QButtonGroup(self)
        for b in (self.btn_simple, self.btn_adv):
            b.setCheckable(True)
            b.setMinimumHeight(30)
            group.addButton(b)
            row.addWidget(b)
        group.setExclusive(True)
        self.btn_simple.setObjectName("mode")
        self.btn_adv.setObjectName("mode")
        lay.addLayout(row)

        # ---- Einfach: drei Regler
        self.simple_panel = QWidget()
        sl = QVBoxLayout(self.simple_panel)
        sl.setContentsMargins(0, 4, 0, 0)
        self.simple_sliders = {
            "contrast": LabeledSlider(
                "Kontrast", 0, 1, 0.01, 0.0, decimals=0, suffix=" %", display_factor=100,
                tooltip="Streckt das Histogramm und gleicht den Kontrast je Bildbereich aus. "
                        "0 % = Original."),
            "sharpness": LabeledSlider(
                "Schärfe", 0, 1, 0.01, 0.0, decimals=0, suffix=" %", display_factor=100,
                tooltip="Betont Kanten. Filmkorn wird dabei geschont."),
            "brightness": LabeledSlider(
                "Helligkeit", -1, 1, 0.01, 0.0, decimals=0, display_factor=100,
                tooltip="Hellt die mittleren Grautöne auf (+) oder dunkelt sie ab (−)."),
        }
        for key, w_ in self.simple_sliders.items():
            w_.valueChanged.connect(lambda v, k=key: self._set_simple(k, v))
            sl.addWidget(w_)
        sl.addWidget(self._info(
            "Tipp: Links eine Voreinstellung wählen und hier nachregeln. "
            "Unter „Erweitert“ gibt es alle Einstellungen einzeln."))
        lay.addWidget(self.simple_panel)

        # ---- Erweitert: alle Regler
        self.adv_panel = QWidget()
        adv_lay = QVBoxLayout(self.adv_panel)
        adv_lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.adv_panel)

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
        self.chk_keep = QCheckBox("Helligkeit des Originals beibehalten")
        self.chk_keep.setToolTip(
            "Nach dem Strecken wird die Bildmitte per Gamma wieder auf die Helligkeit des "
            "Originals gelegt. Das Bild wird kontrastreicher, aber nicht heller – helle "
            "Flächen leuchten nicht aus. Schwarz- und Weißpunkt (1–255) bleiben.")
        self.chk_keep.toggled.connect(lambda v: self._set_value("keep_brightness", v))
        gl.addWidget(self.chk_keep)
        add(gl, "hist_flat", "Histogramm abflachen", 0, 1, 0.01, 0.0, decimals=0,
            suffix=" %", display_factor=100,
            tooltip="Verteilt die Grauwerte breiter über den ganzen Bereich und flacht die "
                    "Spitze im Histogramm ab (globaler Histogrammausgleich).")
        self.flat_mode = QComboBox()
        for key, label in P.HIST_MODES.items():
            self.flat_mode.addItem(label, key)
        self.flat_mode.setToolTip(
            "Weich: Glockenform, flacher als das Original – passt für die meisten Bilder.\n"
            "Stark: annähernd gleich viele Pixel je Grauwert – maximaler Kontrast.\n"
            "Schonend: nur die Spitze wird gekappt – für Bilder mit großen gleichmäßigen "
            "Flächen (Wasser, Wiesen).")
        self.flat_mode.currentIndexChanged.connect(
            lambda _: self._set_value("hist_mode", self.flat_mode.currentData()))
        gl.addWidget(self.flat_mode)
        add(gl, "clip_black", "Schwarz abschneiden", 0, 5, 0.05, 0.5, suffix=" %",
            tooltip="Anteil der dunkelsten Pixel, die ganz schwarz werden.")
        add(gl, "clip_white", "Weiß abschneiden", 0, 5, 0.05, 0.5, suffix=" %",
            tooltip="Anteil der hellsten Pixel, die ganz weiß werden.")
        add(gl, "stretch_buffer", "Puffer (Detailschutz)", 0, 0.3, 0.01, 0.0, decimals=0,
            suffix=" %", display_factor=100,
            tooltip="Streckt etwas weniger hart, damit in sehr hellen und sehr dunklen "
                    "Bereichen keine Details verloren gehen (Gemini-Skript: 10 %).")
        self.chk_center = QCheckBox("Schwarzen Rand und Beschriftung nicht mitzählen")
        self.chk_center.setToolTip(
            "Der schwarze Scanrahmen mit Messmarken und Bildnummer wird erkannt und für "
            "Histogramm und Streckung ignoriert.")
        self.chk_center.toggled.connect(lambda v: self._set_value("stats_center", v))
        gl.addWidget(self.chk_center)
        add(gl, "equalize", "Kontrastausgleich je Bildbereich", 0, 1, 0.01, 0.0,
            decimals=0, suffix=" %", display_factor=100,
            tooltip="Histogrammausgleich in einzelnen Bildbereichen mit weichen Übergängen "
                    "(CLAHE / „Multidodging“). Holt Details aus flauen und dunklen Partien – "
                    "das wichtigste Werkzeug für kontrastarme Weltkriegs-Luftbilder.")
        add(gl, "midtones", "Mitteltöne  (− dunkler / + heller)", -1, 1, 0.01, 0.0,
            decimals=0, display_factor=100,
            tooltip="Hellt die mittleren Grautöne auf oder dunkelt sie ab, "
                    "ohne Schwarz und Weiß zu verändern.")
        add(gl, "clarity", "Lokaler Kontrast", 0, 1, 0.01, 0.0, decimals=0, suffix=" %",
            display_factor=100,
            tooltip="Verstärkt Kontraste zwischen benachbarten Flächen "
                    "(Felder, Wald, Bebauung).")
        adv_lay.addWidget(g)

        g = QGroupBox("Gradationskurve (Histogramm von Hand)")
        gl = QVBoxLayout(g)
        self.curve = CurveWidget()
        self.curve.curveChanged.connect(lambda pts: self._set_value("curve", pts))
        gl.addWidget(self.curve)
        btn = QPushButton("Kurve zurücksetzen")
        btn.clicked.connect(lambda: (self.curve.set_points(()), self._set_value("curve", ())))
        gl.addWidget(btn)
        adv_lay.addWidget(g)

        g = QGroupBox("Hochpassfilter (Schärfen)")
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
        adv_lay.addWidget(g)

        g = QGroupBox("Glätten und Bildfehler")
        gl = QVBoxLayout(g)
        row = QHBoxLayout()
        lbl = QLabel("Medianfilter")
        lbl.setToolTip("Ersetzt jeden Pixel durch den Median seiner Nachbarn: entfernt Körnung "
                       "und Störpixel, Kanten bleiben erhalten (nichtlinearer Filter).")
        row.addWidget(lbl)
        row.addStretch(1)
        self.median_box = QComboBox()
        for label, k in (("Aus", 0), ("3 × 3", 3), ("5 × 5", 5)):
            self.median_box.addItem(label, k)
        self.median_box.setToolTip(lbl.toolTip())
        self.median_box.currentIndexChanged.connect(
            lambda _: self._set_value("median", self.median_box.currentData()))
        row.addWidget(self.median_box)
        gl.addLayout(row)
        add(gl, "lowpass", "Tiefpassfilter (Weichzeichnen)", 0, 3, 0.05, 0.0, decimals=2,
            suffix=" px",
            tooltip="Mittelwert der Umgebung (Gauß, Radius in Pixeln): glättet Rauschen, "
                    "feine Details werden dabei weicher (linearer Filter).")
        add(gl, "dust", "Staub & Kratzer entfernen", 0, 1, 0.01, 0.0, decimals=0,
            suffix=" %", display_factor=100,
            tooltip="Entfernt kleine helle oder dunkle Punkte und feine Kratzer vom Scan.")
        add(gl, "denoise", "Filmkorn reduzieren (kantenerhaltend)", 0, 1, 0.01, 0.0, decimals=0, suffix=" %",
            display_factor=100, tooltip="Glättet Korn und Rauschen, Kanten bleiben erhalten.")
        add(gl, "flatten", "Ausleuchtung ausgleichen", 0, 1, 0.01, 0.0, decimals=0,
            suffix=" %", display_factor=100,
            tooltip="Gleicht dunkle Bildränder und Helligkeitsverläufe "
                    "(Vignettierung des Objektivs) aus.")
        self.chk_invert = QCheckBox("Negativ umkehren")
        self.chk_invert.toggled.connect(lambda v: self._set_value("invert", v))
        gl.addWidget(self.chk_invert)
        adv_lay.addWidget(g)

        reset = QPushButton("Alle Regler zurücksetzen")
        reset.clicked.connect(lambda: self._apply_tonal({}))
        lay.addWidget(reset)

        advanced = QSettings().value("advanced_mode", False, type=bool)
        (self.btn_adv if advanced else self.btn_simple).setChecked(True)
        self._show_mode(advanced)
        self.btn_adv.toggled.connect(self._show_mode)
        lay.addStretch(1)
        return w

    def _build_actions(self):
        tb = self.addToolBar("Werkzeuge")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        mb = self._menubar
        m_file = mb.addMenu("&Datei")
        m_edit = mb.addMenu("&Bearbeiten")
        m_view = mb.addMenu("&Ansicht")
        m_help = mb.addMenu("&Hilfe")
        a_log = QAction("Protokoll-Ordner öffnen", self)
        a_log.triggered.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(os.path.dirname(LOG_FILE) if LOG_FILE else theme.log_dir())))
        m_help.addAction(a_log)
        a_about = QAction(f"Über {theme.APP_NAME}", self)
        a_about.triggered.connect(lambda: QMessageBox.about(
            self, theme.APP_NAME,
            f"<b>{theme.APP_NAME}</b><br>Version {__version__}<br><br>"
            "Zuschneiden, Kontrast und Schärfe für historische Luftbilder."))
        m_help.addAction(a_about)

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
        self.a_export_all = act("Stapelverarbeitung…", self.batch, QKeySequence("Ctrl+B"),
                                m_file)
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
        if self.entry is not None and self.entry.settings.crop:
            self._last_crop_size = tuple(self.entry.settings.crop[2:])
        self.entry = self.entries[row]
        self.doc = None
        self._auto_cache = None
        self._thumbs_geometry = None
        self._crop_qimage = None
        self._last_geometry = None
        self.view.clear()
        self.center.setCurrentIndex(1)
        self._load_started = time.time()
        self._load_name = os.path.basename(self.entry.path)
        self._t_loading.start()
        self._tick_loading()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        path = self.entry.path

        def load():
            t0 = time.time()
            doc = P.Document.open(path)
            t1 = time.time()
            # verkleinerte Ansicht zum Zuschneiden (volle Auflösung erst beim Zoomen)
            disp, scale = P._resize_long(doc.raw, CROP_DISPLAY_LONG_SIDE)
            disp = P.as_float(disp)
            lo, hi = np.percentile(disp[::2, ::2], [0.2, 99.8])
            levels = (float(lo), float(max(hi, lo + 1e-4)))
            qimg = gray_qimage(P.to_uint((disp - levels[0]) / (levels[1] - levels[0]), 8))
            log.info("Geladen %s: %d x %d px, %d Bit, Lesen %.1f s, Ansicht %.1f s",
                     path, *doc.size, doc.bits, t1 - t0, time.time() - t1)
            return doc, qimg, scale, levels

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
            if base in ("load", "export", "batch"):
                self._t_loading.stop()
                self._close_progress()
                self.statusBar().clearMessage()
            self._show_error(base, res)
            return
        if kind in self._busy:
            self._busy[kind] = False
            if self._pending[kind]:
                self._pending[kind] = False
                {"preview": self._render_preview, "detail": self._render_detail,
                 "thumbs": self._render_thumbs}[kind]()
        if gen != self._gen.get(kind):
            return  # veraltet
        try:
            getattr(self, "_done_" + kind)(res)
        except Exception as exc:
            log.exception("Fehler beim Anzeigen von '%s'", kind)
            self._show_error(kind, exc)

    def _show_error(self, kind, exc):
        names = {"load": "Laden", "export": "Speichern", "batch": "Stapelverarbeitung",
                 "preview": "Vorschau", "detail": "Detailansicht", "thumbs": "Vorschaubilder"}
        text = f"Fehler bei {names.get(kind, kind)}: {type(exc).__name__}: {exc}"
        self.statusBar().showMessage(text, 30000)
        if kind not in self._errors_shown:  # jede Fehlerart nur einmal als Fenster
            self._errors_shown.add(kind)
            QMessageBox.critical(
                self, "Fehler", f"{text}\n\nDetails stehen in der Protokolldatei:\n{LOG_FILE}")

    def _tick_loading(self):
        secs = int(time.time() - self._load_started)
        self.statusBar().showMessage(f"Lade {self._load_name} … {secs} s")

    def _done_load(self, res):
        self._t_loading.stop()
        log.info("Bild angezeigt nach %.1f s", time.time() - self._load_started)
        QApplication.restoreOverrideCursor()
        self.doc, self._crop_qimage, self._crop_scale, self._crop_levels = res
        w, h = self.doc.size
        bits = f"{self.doc.bits} Bit"
        dpi = f", {self.doc.dpi:.0f} dpi" if self.doc.dpi else ""
        geo = ", georeferenziert (bleibt beim Speichern erhalten)" if self.doc.geo else ""
        self.statusBar().showMessage(
            f"{os.path.basename(self.entry.path)}: {w} × {h} px, {bits}{dpi}{geo}", 12000)
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
            t0 = time.time()
            prep = doc.prepare(s)
            t1 = time.time()
            glob = P.compute_globals(prep, s)
            scale = min(1.0, PREVIEW_LONG_SIDE / prep.long_side)
            img = P.render(prep, s, None, scale, glob)
            if t1 - t0 > 0.5:
                log.info("Vorschau: Vorbereiten %.1f s, Berechnen %.1f s", t1 - t0,
                         time.time() - t1)
            before = P.histogram(P.stats_sample(prep, s, prep.stats))
            valid = prep.__dict__.get("_valid") if s.stats_center else None
            if valid is not None:
                vm = cv2.resize(valid.astype(np.uint8), (img.shape[1], img.shape[0]),
                                interpolation=cv2.INTER_NEAREST).astype(bool)
                after = P.histogram(img[vm])
            else:
                after = P.histogram(img)
            return dict(qimg=gray_qimage(P.to_uint(img, 8)), size=prep.size, scale=scale,
                        before=before, after=after, levels=glob.levels)

        self._submit("preview", job)
        self._t_detail.start(0)

    def _done_preview(self, r):
        if self.tabs.currentIndex() != 1:
            return
        self._preview_scale = r["scale"]
        self.view.set_image(r["qimg"], r["size"], r["scale"])
        self.histogram.set_data(r["before"], r["after"], r["levels"])
        self.curve.set_histogram(r["after"])
        self._update_zoom_label()

    def _render_detail(self):
        if self.doc and self.tabs.currentIndex() == 0 and self.view.mode == "crop":
            return self._render_crop_detail()
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
            glob = P.compute_globals(prep, s)
            img = P.render(prep, s, (x, y, w, h), scale, glob)
            return dict(qimg=gray_qimage(P.to_uint(img, 8)), x=x, y=y,
                        scale=img.shape[1] / w, geometry=(s.rotation, s.crop))

        self._submit("detail", job)

    def _render_crop_detail(self):
        """Zuschneiden: beim Hineinzoomen den sichtbaren Bereich in voller Auflösung zeigen."""
        z = self.view.zoom()
        if z <= self._crop_scale * 1.15:
            self._gen["detail"] += 1
            self.view.clear_detail()
            return
        if self._busy["detail"]:
            self._pending["detail"] = True
            return
        W, H = self.doc.size
        r = self.view.visible_source_rect()
        x = max(0, int(r.left() - r.width() * 0.1))
        y = max(0, int(r.top() - r.height() * 0.1))
        x1 = min(W, int(np.ceil(r.right() + r.width() * 0.1)))
        y1 = min(H, int(np.ceil(r.bottom() + r.height() * 0.1)))
        if x1 <= x or y1 <= y:
            return
        scale = min(1.0, z)
        raw, (lo, hi) = self.doc.raw, self._crop_levels

        def job():
            tile = P.as_float(raw[y:y1, x:x1])
            if scale < 0.999:
                tile = cv2.resize(tile, (max(1, round((x1 - x) * scale)),
                                         max(1, round((y1 - y) * scale))),
                                  interpolation=cv2.INTER_AREA)
            img = P.to_uint((tile - lo) / (hi - lo), 8)
            return dict(qimg=gray_qimage(img), x=x, y=y, scale=img.shape[1] / (x1 - x),
                        mode="crop")

        self._submit("detail", job)

    def _done_detail(self, r):
        if r.get("mode") == "crop":
            if self.tabs.currentIndex() == 0:
                self.view.set_detail(r["qimg"], r["x"], r["y"], r["scale"],
                                     self.entry.settings.rotation)
            return
        if self.tabs.currentIndex() != 1 or r["geometry"] != self._last_geometry:
            return
        self.view.set_detail(r["qimg"], r["x"], r["y"], r["scale"])

    def _render_thumbs(self):
        if not self.doc:
            return
        if self._busy["thumbs"]:
            self._pending["thumbs"] = True
            return
        doc, base = self.doc, self.entry.settings
        geometry = (base.rotation, base.crop)
        if geometry == self._thumbs_geometry:
            return  # schon aktuell
        self._thumbs_geometry = geometry
        presets = self._all_presets()

        def job():
            t0 = time.time()
            geo = P.Settings(rotation=base.rotation, crop=base.crop)
            prep = doc.prepare(geo)
            scale = min(1.0, THUMB_LONG_SIDE / prep.long_side)
            auto = P.auto_tonal(prep)
            out = {}
            for name, values in presets.items():
                if name == AUTO:
                    values = auto
                s = P.with_tonal(geo, values)
                out[name] = gray_qimage(P.to_uint(P.render(prep, s, None, scale), 8))
            log.info("Vorschaukacheln und Automatik in %.1f s", time.time() - t0)
            return dict(thumbs=out, auto=auto, geometry=(geo.rotation, geo.crop))

        self._submit("thumbs", job)

    def _done_thumbs(self, res):
        thumbs = res["thumbs"]
        self._auto_cache = (res["geometry"], res["auto"])
        for i in range(self.preset_list.count()):
            item = self.preset_list.item(i)
            q = thumbs.get(item.data(Qt.UserRole))
            if q is not None:
                item.setIcon(QIcon(QPixmap.fromImage(q)))

    def _on_view_changed(self):
        self._update_zoom_label()
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
        if geo_changed and self.tabs.currentIndex() == 1:
            # Vorschaukacheln erst im Schritt "Verbessern" neu berechnen – beim
            # Ziehen des Zuschneiderahmens würde sonst jede Mausbewegung eine
            # Berechnung über das ganze Bild auslösen.
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
        self._sync_simple()

    def _apply_tonal(self, values):
        if self.entry:
            self._set_settings(P.with_tonal(self.entry.settings, values), sync=True)
            self._t_history.start(0)

    # ---- Einfach-Modus
    def _show_mode(self, advanced):
        self.simple_panel.setVisible(not advanced)
        self.adv_panel.setVisible(advanced)
        QSettings().setValue("advanced_mode", bool(advanced))

    def _set_simple(self, key, v):
        if not self.entry:
            return
        s = self.entry.settings
        if key == "contrast":
            if v <= 0:
                new = replace(s, stretch=False, equalize=0.0)
            else:
                # Streckung + Histogramm abflachen (Glockenform), wenig lokaler Ausgleich
                new = replace(s, stretch=True, clip_black=round(0.1 + 0.3 * v, 2),
                              clip_white=round(0.05 + 0.1 * v, 2),
                              hist_flat=round(v, 3), equalize=round(0.15 * v, 3))
        elif key == "sharpness":
            new = replace(s, sharpen_amount=round(1.6 * v, 3),
                          sharpen_threshold=s.sharpen_threshold or 0.01)
        else:
            new = replace(s, midtones=v)
        self.preset_list.clearSelection()
        self._set_settings(new)
        self._sync_controls(simple=False)

    def _sync_simple(self):
        s = self.entry.settings if self.entry else P.Settings()
        contrast = 0.0
        if s.stretch:
            contrast = max(s.hist_flat, s.equalize / 0.15 if s.hist_flat == 0 else 0, 0.01)
        values = {"contrast": min(contrast, 1.0),
                  "sharpness": min(s.sharpen_amount / 1.6, 1.0),
                  "brightness": s.midtones}
        for k, w_ in self.simple_sliders.items():
            w_.setValue(values[k])

    def _sync_controls(self, simple=True):
        s = self.entry.settings if self.entry else P.Settings()
        if simple:
            self._sync_simple()
        for box, value in ((self.flat_mode, s.hist_mode), (self.median_box, s.median)):
            box.blockSignals(True)
            box.setCurrentIndex(max(0, box.findData(value)))
            box.blockSignals(False)
        self.curve.set_points(s.curve)
        for key, sl in self.sliders.items():
            sl.setValue(getattr(s, key))
        for chk, key in ((self.chk_stretch, "stretch"), (self.chk_invert, "invert"),
                         (self.chk_center, "stats_center"), (self.chk_keep, "keep_brightness")):
            chk.blockSignals(True)
            chk.setChecked(getattr(s, key))
            chk.blockSignals(False)
        self.sl_rotation.setValue(s.rotation)
        self._sync_crop_controls()
        self._update_enabled()

    def _sync_crop_controls(self):
        if not self.doc:
            return
        w, h = self._canvas()
        crop = P.clamp_crop(self.entry.settings.crop, w, h)
        for sb, lo, hi, v in zip(self.spin_crop, (0, 0, 1, 1), (w - 1, h - 1, w, h), crop):
            sb.blockSignals(True)
            sb.setRange(lo, hi)
            sb.setValue(v)
            sb.blockSignals(False)
        if self.view.mode == "crop":
            self.view.set_crop(crop)
            self.view.set_display_rotation(self.entry.settings.rotation)
        self.lbl_size.setText(f"Ausschnitt {crop[2]} × {crop[3]} px")

    def _update_enabled(self):
        has = self.doc is not None
        for w in (self.tabs, self.btn_export, self.btn_batch, self.btn_save_preset,
                  self.preset_list, self.btn_before):
            w.setEnabled(has)
        for a in (self.a_export, self.a_export_all, self.a_apply_all, self.a_close):
            a.setEnabled(has)
        e = self.entry
        self.a_undo.setEnabled(bool(e and e.hist_idx > 0))
        self.a_redo.setEnabled(bool(e and e.hist_idx < len(e.history) - 1))
        stretch = bool(e and e.settings.stretch)
        for k in ("clip_black", "clip_white", "stretch_buffer"):
            self.sliders[k].setEnabled(stretch)
        self.chk_center.setEnabled(stretch)
        self.chk_keep.setEnabled(stretch)
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
            self.view.set_image(self._crop_qimage, self.doc.size, self._crop_scale,
                                self.entry.settings.rotation)
            self._t_detail.start()
            self.view.fit()
            self._sync_crop_controls()
        else:
            self.view.set_mode("edit")
            self._last_geometry = None
            self._t_thumbs.start(300)
            self.view.clear()
            self.statusBar().showMessage("Berechne Vorschau …", 2000)
            self._render_preview()
        self._update_zoom_label()

    def _on_corner_mode(self, on):
        self.view.set_corner_mode(on)
        if on:
            self.statusBar().showMessage("Obere linke Ecke anklicken …")

    def _on_corner_step(self, step):
        if step == 1:
            self.statusBar().showMessage("Jetzt die untere rechte Ecke anklicken …")
        else:
            self.btn_corners.setChecked(False)
            self.statusBar().showMessage("Rahmen gesetzt – Ecken und Kanten lassen sich "
                                         "noch fein verschieben.", 6000)

    def _on_lock_size(self, on):
        QSettings().setValue("lock_crop_size", bool(on))
        self.view.size_locked = on
        self.btn_corners.setEnabled(not on)
        for sb in self.spin_crop[2:]:
            sb.setEnabled(not on)
        self.aspect_box.setEnabled(not on)
        self.view.viewport().update()

    def _previous_crop_size(self):
        """Breite/Höhe aus dem zuletzt bearbeiteten anderen Bild (mit Zuschnitt)."""
        if self._last_crop_size:
            return self._last_crop_size
        idx = self.entries.index(self.entry) if self.entry in self.entries else -1
        others = self.entries[:idx][::-1] + self.entries[idx + 1:]
        for e in others:
            if e.settings.crop:
                return e.settings.crop[2], e.settings.crop[3]
        return None

    def _apply_previous_size(self):
        if not self.doc:
            return
        size = self._previous_crop_size()
        if not size:
            QMessageBox.information(self, "Keine Vorlage",
                                    "Es wurde noch kein anderes Bild zugeschnitten.")
            return
        w, h = self._canvas()
        cw, ch = min(size[0], w), min(size[1], h)
        x, y, ow, oh = P.clamp_crop(self.entry.settings.crop, w, h)
        # Mittelpunkt des bisherigen Rahmens beibehalten, dann in das Bild schieben
        nx = min(max(0, x + ow // 2 - cw // 2), w - cw)
        ny = min(max(0, y + oh // 2 - ch // 2), h - ch)
        self._set_crop((nx, ny, cw, ch))
        if (cw, ch) != tuple(size):
            QMessageBox.warning(self, "Bild zu klein",
                                f"Die Vorlage ({size[0]} × {size[1]} px) ist größer als dieses "
                                f"Bild – der Rahmen wurde auf {cw} × {ch} px verkleinert.")
        else:
            self.chk_lock_size.setChecked(True)
            self.statusBar().showMessage(
                f"Rahmen {cw} × {ch} px übernommen – jetzt an die richtige Stelle schieben.",
                8000)

    def _set_crop(self, crop):
        if not self.doc:
            return
        w, h = self._canvas()
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
            w, h = self._canvas()
            val = w / h
        self.view.aspect = val
        if val and self.doc:
            x, y, w, h = P.clamp_crop(self.entry.settings.crop, *self._canvas())
            if w / h > val:
                nw, nh = round(h * val), h
            else:
                nw, nh = w, round(w / val)
            self._set_crop((x + (w - nw) // 2, y + (h - nh) // 2, nw, nh))

    def _canvas(self) -> tuple[int, int]:
        """Größe des gedrehten Bildes – darauf bezieht sich der Zuschnitt."""
        return self.doc.canvas_size(self.entry.settings.rotation)

    def _rotate_by(self, delta):
        self.sl_rotation.setValue(P.normalize_angle(self.sl_rotation.value() + delta))
        self._on_rotation(self.sl_rotation.value())

    def _on_rotation(self, v):
        v = P.normalize_angle(v)
        if not self.entry or self.entry.settings.rotation == v:
            return
        s = self.entry.settings
        crop = P.follow_rotation(s.crop, self.doc.size, s.rotation, v) if self.doc else s.crop
        self.preset_list.clearSelection()
        self._set_settings(replace(s, rotation=v, crop=crop))
        self._sync_crop_controls()
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
            elif name in PRESET_INFO:
                item.setToolTip(PRESET_INFO[name])
            self.preset_list.addItem(item)

    def _auto_values(self) -> dict:
        """Automatische Werte für das aktuelle Bild (aus dem Zwischenspeicher)."""
        s = self.entry.settings
        geometry = (s.rotation, s.crop)
        if self._auto_cache and self._auto_cache[0] == geometry:
            return self._auto_cache[1]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            prep = self.doc.prepare(P.Settings(rotation=s.rotation, crop=s.crop))
            values = P.auto_tonal(prep)
        finally:
            QApplication.restoreOverrideCursor()
        self._auto_cache = (geometry, values)
        return values

    def _apply_preset_item(self, item):
        name = item.data(Qt.UserRole)
        if name == AUTO and self.doc:
            values = self._auto_values()
            self._last_preset_auto = True
        else:
            values = self._all_presets().get(name)
            self._last_preset_auto = False
        if values is not None and self.entry:
            self._apply_tonal(values)
            if self.tabs.currentIndex() != 1:
                self.tabs.setCurrentIndex(1)  # Ergebnis sofort sichtbar machen
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
        self._thumbs_geometry = None
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
        nodata_safe = self.chk_nodata.isChecked()
        self._start_progress("Bild wird in voller Auflösung gespeichert …")
        sig = self.sig

        def job():
            P.export(doc, s, path, fmt, progress=lambda i, n: sig.progress.emit(i, n),
                     nodata_safe=nodata_safe)
            return [path]

        self._submit("export", job)

    def batch(self):
        if not self.entry:
            return
        self._commit_history()
        from .batch import BatchDialog, run_batch
        # das aktuelle Bild zuerst, dann alle anderen geöffneten Bilder
        files = [self.entry.path] + [e.path for e in self.entries if e is not self.entry]
        dlg = BatchDialog(self, os.path.basename(self.entry.path), files,
                          os.path.dirname(self.entry.path), auto=self._last_preset_auto)
        if dlg.exec() != BatchDialog.Accepted:
            return
        job = dlg.job()
        settings, current = self.entry.settings, self.doc
        # Einstellungen auch für die Bilder in der Liste übernehmen
        chosen = {os.path.normcase(os.path.abspath(f)) for f in job.files}
        for e in self.entries:
            if e is not self.entry and os.path.normcase(os.path.abspath(e.path)) in chosen:
                e.settings = settings if job.use_geometry else P.with_tonal(
                    e.settings, P.tonal_dict(settings))
                del e.history[e.hist_idx + 1:]
                e.history.append(e.settings)
                e.hist_idx = len(e.history) - 1
        self._start_progress(f"{len(job.files)} Bilder werden bearbeitet …", cancel=True)
        sig, nodata_safe = self.sig, self.chk_nodata.isChecked()
        self._batch_cancel = False

        def job_fn():
            return run_batch(job, settings, nodata_safe=nodata_safe,
                             progress=lambda i, n: sig.progress.emit(i, n),
                             cancelled=lambda: self._batch_cancel, current_doc=current)

        self._submit("batch", job_fn)

    def _done_batch(self, res):
        written, errors = res
        cancelled = self._batch_cancel
        self._close_progress()
        text = f"{len(written)} Bilder gespeichert."
        if cancelled:
            text += " (abgebrochen)"
        if written:
            text += f"\nZielordner: {os.path.dirname(written[0])}"
        if errors:
            text += "\n\nNicht bearbeitet:\n" + "\n".join(
                f"• {os.path.basename(p)}: {e}" for p, e in errors[:15])
            QMessageBox.warning(self, "Stapelverarbeitung", text)
        else:
            QMessageBox.information(self, "Stapelverarbeitung", text)

    def _start_progress(self, text, cancel=False):
        self._progress = QProgressDialog(text, "Abbrechen" if cancel else None, 0, 100, self)
        self._progress.setWindowTitle("Bitte warten")
        self._progress.setWindowModality(Qt.WindowModal)
        self._progress.setMinimumDuration(0)
        self._progress.setAutoClose(False)
        self._progress.setAutoReset(False)
        if cancel:
            self._progress.canceled.connect(self._cancel_batch)
        self._progress.setValue(0)
        QApplication.setOverrideCursor(Qt.WaitCursor)

    def _cancel_batch(self):
        self._batch_cancel = True
        if self._progress:
            self._progress.setLabelText("Wird nach dem aktuellen Bild abgebrochen …")
            self._progress.show()

    def _close_progress(self):
        QApplication.restoreOverrideCursor()
        if self._progress:
            # close() löst bei QProgressDialog "canceled" aus – das ist kein Abbruch
            try:
                self._progress.canceled.disconnect()
            except (RuntimeError, TypeError):
                pass
            self._progress.close()
            self._progress = None

    def _on_progress(self, i, n):
        if self._progress:
            self._progress.setValue(int(100 * i / max(n, 1)))

    def _done_export(self, paths):
        self._close_progress()
        msg = paths[0] if len(paths) == 1 else f"{len(paths)} Bilder in {os.path.dirname(paths[0])}"
        self.statusBar().showMessage(f"Gespeichert: {msg}", 10000)
