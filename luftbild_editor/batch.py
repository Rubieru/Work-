"""Dialog für die Stapelverarbeitung: Einstellungen eines Bildes auf viele anwenden."""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout)

from . import processing as P

IMAGE_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".jp2")


@dataclass
class BatchJob:
    files: list
    folder: str
    fmt: str
    suffix: str
    use_geometry: bool


class BatchDialog(QDialog):
    def __init__(self, parent, source_name: str, files: list[str], start_dir: str):
        super().__init__(parent)
        self.setWindowTitle("Stapelverarbeitung")
        self.resize(640, 560)
        self._start_dir = start_dir

        lay = QVBoxLayout(self)
        info = QLabel(
            f"Die Einstellungen von <b>{source_name}</b> werden auf alle angehakten Bilder "
            "angewendet. Die Bilder werden in voller Auflösung bearbeitet und gespeichert. "
            "Die Originale bleiben unverändert, eine Georeferenzierung wird übernommen.")
        info.setWordWrap(True)
        lay.addWidget(info)

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
        self.chk_geo = QCheckBox("Zuschnitt und Drehung ebenfalls übernehmen")
        self.chk_geo.setToolTip(
            "Sinnvoll, wenn alle Scans gleich groß sind und denselben Rand haben. "
            "Sonst bleibt jedes Bild ungeschnitten.")
        form.addRow(self.chk_geo)
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
        lay.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Starten")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add(self, path):
        path = os.path.abspath(path)
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == path:
                return
        item = QListWidgetItem(os.path.basename(path))
        item.setToolTip(path)
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
        if not self.folder.text().strip():
            self._pick_folder()
            if not self.folder.text().strip():
                return
        self.accept()

    def job(self) -> BatchJob:
        files = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                 if self.list.item(i).checkState() == Qt.Checked]
        return BatchJob(files, self.folder.text().strip(), self.fmt.currentData(),
                        self.suffix.text(), self.chk_geo.isChecked())


def run_batch(job: BatchJob, settings: P.Settings, progress=None, cancelled=None,
              current_doc: P.Document | None = None,
              nodata_safe: bool = False) -> tuple[list, list]:
    """Bearbeitet alle Bilder. Gibt (geschriebene Dateien, Fehler) zurück."""
    os.makedirs(job.folder, exist_ok=True)
    ext = P.EXPORT_FORMATS[job.fmt][1]
    written, errors = [], []
    s = settings if job.use_geometry else P.Settings(**P.tonal_dict(settings))
    for i, src in enumerate(job.files):
        if cancelled and cancelled():
            break
        try:
            doc = current_doc if current_doc and current_doc.path == src else P.Document.open(src)
            stem = os.path.splitext(os.path.basename(src))[0]
            out = os.path.join(job.folder, stem + job.suffix + ext)
            if os.path.abspath(out) == os.path.abspath(src):
                out = os.path.join(job.folder, stem + job.suffix + "_1" + ext)
            P.export(doc, s, out, job.fmt, nodata_safe=nodata_safe)
            written.append(out)
        except Exception as exc:  # ein defektes Bild soll den Stapel nicht abbrechen
            errors.append((src, f"{type(exc).__name__}: {exc}"))
        if progress:
            progress(i + 1, len(job.files))
    return written, errors
