"""Dialog für die Stapelverarbeitung: Einstellungen eines Bildes auf viele anwenden."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QVBoxLayout)

from . import processing as P

IMAGE_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".jp2")


def path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


@dataclass
class BatchJob:
    files: list
    folder: str
    fmt: str
    suffix: str
    use_geometry: bool   # Zuschnitt und Drehung des aktuellen Bildes für alle
    # Regler je Bild automatisch bestimmen: Variante (z. B. "streckung"), True = "standard"
    auto: bool | str | None = False
    # eigener Zuschnitt/Drehung je Bild: path_key -> (rotation, crop)
    geometries: dict | None = None
    # eigene Regler je Bild: path_key -> (Regler-Werte als dict, Automatik-Variante oder None)
    per_image: dict | None = None
    overwrite: bool = False   # Originale ersetzen (gleicher Ordner, Name und Format)
    backup: bool = True       # vorher Sicherungskopie in BACKUP_DIR


BACKUP_DIR = "Originale_Sicherung"
_TMP_DIR = ".luftbild_editor_tmp"


def format_for(path: str, bits: int) -> str | None:
    """Speicherformat, das zum Original passt (für "Originale ersetzen")."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".tif", ".tiff"):
        return "tif16" if bits == 16 else "tif8"
    if ext == ".png":
        return "png"
    if ext in (".jpg", ".jpeg"):
        return "jpg"
    return None


def _companions(path: str) -> list[str]:
    """Begleitdateien mit gleichem Namensstamm (World-File, .prj, .aux.xml)."""
    folder, name = os.path.split(path)
    stem = os.path.splitext(name)[0]
    out = []
    for other in os.listdir(folder or "."):
        full = os.path.join(folder, other)
        if other != name and os.path.isfile(full) and (
                os.path.splitext(other)[0] == stem or other.startswith(name + ".")):
            out.append(full)
    return out


def replace_original(doc: P.Document, s: P.Settings, src: str, nodata_safe: bool,
                     backup: bool) -> str:
    """Bearbeitetes Bild an die Stelle des Originals schreiben.

    Erst wird vollständig in einen Zwischenordner geschrieben, dann werden die Dateien
    ausgetauscht – bricht etwas ab, bleibt das Original unverändert.
    """
    fmt = format_for(src, doc.bits)
    if fmt is None:
        raise ValueError("Dieses Dateiformat kann nicht ersetzt werden – bitte in einen "
                         "neuen Ordner speichern")
    folder, name = os.path.split(os.path.abspath(src))
    if backup:
        bdir = os.path.join(folder, BACKUP_DIR)
        os.makedirs(bdir, exist_ok=True)
        for f in [src] + _companions(src):
            target = os.path.join(bdir, os.path.basename(f))
            if not os.path.exists(target):        # erste Sicherung bleibt erhalten
                shutil.copy2(f, target)
    tmp = os.path.join(folder, _TMP_DIR)
    os.makedirs(tmp, exist_ok=True)
    try:
        out = os.path.join(tmp, name)
        P.export(doc, s, out, fmt, nodata_safe=nodata_safe)
        for f in os.listdir(tmp):
            os.replace(os.path.join(tmp, f), os.path.join(folder, f))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return os.path.join(folder, name)


TONAL_MODES = (
    ("own", "Jedes Bild mit seiner eigenen Voreinstellung"),
    ("same", "Einstellungen dieses Bildes für alle"),
)

GEOMETRY_MODES = (
    ("own", "Jedes Bild mit eigenem Rahmen (✂) und eigener Drehung"),
    ("same", "Rahmen und Drehung dieses Bildes für alle"),
    ("none", "Nicht zuschneiden"),
)


class BatchDialog(QDialog):
    def __init__(self, parent, source_name: str, files: list[str], start_dir: str,
                 auto: bool | str | None = False, geometries: dict | None = None,
                 per_image: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Stapelverarbeitung")
        self.resize(640, 560)
        self._start_dir = start_dir
        self._geometries = geometries or {}
        self._per_image = per_image or {}
        self._auto = auto

        lay = QVBoxLayout(self)
        self._source_name = source_name
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        for f in files:
            self._add(f)
        row = QHBoxLayout()
        for text, slot in (("Bilder hinzufügen…", self._add_files),
                           ("Ordner hinzufügen…", self._add_folder),
                           ("Alle an", lambda: self._check_all(True)),
                           ("Alle aus", lambda: self._check_all(False))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)

        form = QFormLayout()
        self.tonal_mode = QComboBox()
        for key, label in TONAL_MODES:
            self.tonal_mode.addItem(label, key)
        self.tonal_mode.setToolTip(
            "Eigene Voreinstellung: jedes Bild so, wie es im Programm eingestellt ist – "
            "Automatik-Voreinstellungen werden für jedes Bild einzeln gemessen. Bilder, die "
            "nur hier hinzugefügt wurden, bekommen die Einstellungen des aktuellen Bildes.\n"
            "Dieses Bild für alle: die Einstellungen des aktuellen Bildes (bei einer "
            "Automatik: dieselbe Automatik, je Bild gemessen).")
        form.addRow("Filter", self.tonal_mode)
        self.geo_mode = QComboBox()
        for key, label in GEOMETRY_MODES:
            self.geo_mode.addItem(label, key)
        self.geo_mode.setToolTip(
            "Eigener Rahmen: jedes Bild wird so zugeschnitten und gedreht, wie es im "
            "Schritt 1 eingestellt wurde (Bilder ohne Rahmen bleiben ganz).\n"
            "Rahmen dieses Bildes: sinnvoll, wenn alle Scans exakt gleich liegen.")
        form.addRow("Zuschnitt/Drehung", self.geo_mode)
        self.target = QComboBox()
        self.target.addItem("In einen neuen Ordner speichern", "folder")
        self.target.addItem("Originale ersetzen (gleicher Ordner, Name und Format)", "replace")
        self.target.setToolTip(
            "Originale ersetzen: jedes Bild wird unter seinem eigenen Namen im eigenen Ordner "
            "überschrieben (TIFF bleibt TIFF, JPG bleibt JPG). Nicht rückgängig zu machen – "
            "deshalb standardmäßig mit Sicherungskopie.")
        self.target.currentIndexChanged.connect(self._on_target)
        form.addRow("Speichern", self.target)
        self.chk_backup = QCheckBox(f"Vorher Sicherungskopie der Originale anlegen "
                                    f"(Unterordner „{BACKUP_DIR}“)")
        self.chk_backup.setChecked(True)
        form.addRow(self.chk_backup)
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(os.path.join(start_dir, "bearbeitet") if start_dir else "")
        btn = QPushButton("…")
        btn.setFixedWidth(32)
        btn.clicked.connect(self._pick_folder)
        folder_row.addWidget(self.folder)
        folder_row.addWidget(btn)
        form.addRow("Zielordner", folder_row)
        self.fmt = QComboBox()
        for key, (label, _) in P.EXPORT_FORMATS.items():
            self.fmt.addItem(label.split(" (")[0], key)
        form.addRow("Dateiformat", self.fmt)
        self.suffix = QLineEdit("_bearbeitet")
        form.addRow("Zusatz im Dateinamen", self.suffix)
        self._folder_widgets = [self.folder, btn, self.fmt, self.suffix]
        lay.addLayout(form)
        self._on_target()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Starten")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _on_target(self, *_):
        replace = self.target.currentData() == "replace"
        self.info.setText(
            "Alle angehakten Bilder werden in voller Auflösung bearbeitet und gespeichert, "
            "eine Georeferenzierung wird übernommen. ✂ = Bild hat einen eigenen "
            "Zuschnitt-Rahmen. "
            + ("<b>Die Originaldateien werden ersetzt.</b>" if replace else
               "Die Originale bleiben unverändert."))
        for w in self._folder_widgets:
            w.setEnabled(not replace)
        self.chk_backup.setVisible(replace)

    def _add(self, path):
        path = os.path.abspath(path)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == path:
                return
        geo = self._geometries.get(path_key(path))
        crop = geo[1] if geo else None
        item = QListWidgetItem(("✂ " if crop else "") + os.path.basename(path))
        item.setToolTip(path + (f"\nZuschnitt {crop[2]} × {crop[3]} px" if crop else ""))
        item.setData(Qt.UserRole, path)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        self.list.addItem(item)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Bilder hinzufügen", self._start_dir,
            "Bilder (*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.jp2)")
        for p in paths:
            self._add(p)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner hinzufügen", self._start_dir)
        if d:
            for name in sorted(os.listdir(d)):
                if name.lower().endswith(IMAGE_EXTS):
                    self._add(os.path.join(d, name))

    def _check_all(self, on):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Zielordner wählen", self.folder.text())
        if d:
            self.folder.setText(d)

    def _accept(self):
        if not self.job().files:
            self.list.setFocus()
            return
        replace = self.target.currentData() == "replace"
        if not replace and not self.folder.text().strip():
            self._pick_folder()
            if not self.folder.text().strip():
                return
        if self.geo_mode.currentData() == "own":
            files = self.job().files
            missing = [os.path.basename(f) for f in files
                       if not (self._geometries.get(path_key(f)) or (0, None))[1]]
            # nur warnen, wenn ein Teil zugeschnitten ist und ein Teil nicht – wer nur dreht
            # oder nur Filter anwendet, braucht keinen Rahmen
            if missing and len(missing) < len(files):
                names = "\n".join("• " + n for n in missing[:10])
                more = f"\n… und {len(missing) - 10} weitere" if len(missing) > 10 else ""
                answer = QMessageBox.question(
                    self, "Bilder ohne Rahmen",
                    f"{len(missing)} Bild(er) haben noch keinen Zuschnitt-Rahmen und werden "
                    f"ungeschnitten gespeichert:\n\n{names}{more}\n\nTrotzdem starten?")
                if answer != QMessageBox.Yes:
                    return
        if replace:
            n = len(self.job().files)
            text = (f"{n} Originaldatei(en) werden mit dem bearbeiteten Bild überschrieben.\n\n"
                    + (f"Die Originale werden vorher in den Unterordner „{BACKUP_DIR}“ kopiert."
                       if self.chk_backup.isChecked() else
                       "ACHTUNG: Es wird KEINE Sicherungskopie angelegt – die Originale sind "
                       "danach unwiederbringlich verändert.")
                    + "\n\nFortfahren?")
            if QMessageBox.question(self, "Originale ersetzen", text) != QMessageBox.Yes:
                return
        self.accept()

    def job(self) -> BatchJob:
        files = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                 if self.list.item(i).checkState() == Qt.Checked]
        mode = self.geo_mode.currentData()
        own = self.tonal_mode.currentData() == "own"
        return BatchJob(files, self.folder.text().strip(), self.fmt.currentData(),
                        self.suffix.text(), mode == "same", self._auto,
                        self._geometries if mode == "own" else None,
                        self._per_image if own else None,
                        overwrite=self.target.currentData() == "replace",
                        backup=self.chk_backup.isChecked())


def run_batch(job: BatchJob, settings: P.Settings, progress=None, cancelled=None,
              current_doc: P.Document | None = None,
              nodata_safe: bool = False) -> tuple[list, list]:
    """Bearbeitet alle Bilder. Gibt (geschriebene Dateien, Fehler) zurück."""
    if not job.overwrite:
        os.makedirs(job.folder, exist_ok=True)
    ext = P.EXPORT_FORMATS[job.fmt][1]
    written, errors = [], []
    tonal = P.Settings(**P.tonal_dict(settings))
    for i, src in enumerate(job.files):
        if cancelled and cancelled():
            break
        try:
            same = current_doc and current_doc.path and \
                os.path.normcase(os.path.abspath(current_doc.path)) == os.path.normcase(src)
            doc = current_doc if same else P.Document.open(src)
            stem = os.path.splitext(os.path.basename(src))[0]
            out = os.path.join(job.folder, stem + job.suffix + ext)
            if os.path.abspath(out) == os.path.abspath(src):
                out = os.path.join(job.folder, stem + job.suffix + "_1" + ext)
            if job.use_geometry:
                s = settings
            elif job.geometries and path_key(src) in job.geometries:
                rotation, crop = job.geometries[path_key(src)]
                s = replace(tonal, rotation=rotation, crop=crop)
            else:
                s = tonal
            variant = "standard" if job.auto is True else (job.auto or None)
            if job.per_image and path_key(src) in job.per_image:
                values, variant = job.per_image[path_key(src)]
                s = P.with_tonal(s, values)
            s_img = s
            if variant:
                geo = P.Settings(rotation=s.rotation, crop=s.crop)
                s_img = P.with_tonal(s, P.auto_tonal(doc.prepare(geo), variant))
            if job.overwrite:
                written.append(replace_original(doc, s_img, src, nodata_safe, job.backup))
            else:
                P.export(doc, s_img, out, job.fmt, nodata_safe=nodata_safe)
                written.append(out)
        except Exception as exc:  # ein defektes Bild soll den Stapel nicht abbrechen
            errors.append((src, f"{type(exc).__name__}: {exc}"))
        if progress:
            progress(i + 1, len(job.files))
    return written, errors
