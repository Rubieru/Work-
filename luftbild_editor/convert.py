"""Ordner umwandeln: alle Bilder eines Ordners (mit Unterordnern) in ein anderes Format,
ohne sie zu bearbeiten – z. B. TIFF -> JPEG, um Speicherplatz zu sparen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from . import processing as P

SKIP_DIRS = {"Originale_Sicherung", ".luftbild_editor_tmp"}
SOURCE_EXTS = {"tif": (".tif", ".tiff"), "png": (".png",)}
TARGET_EXT = {"jpg": ".jpg", "png": ".png", "tif8": ".tif"}


@dataclass
class ConvertJob:
    root: str
    sources: tuple = ("tif",)          # welche Formate umgewandelt werden
    target: str = "jpg"                # jpg | png | tif8
    quality: int = 92                  # JPEG-Qualität
    recursive: bool = True
    delete_source: bool = False        # Quelldatei nach erfolgreicher Umwandlung löschen
    overwrite: bool = False            # vorhandene Zieldatei überschreiben


@dataclass
class ConvertResult:
    written: list = field(default_factory=list)
    skipped: list = field(default_factory=list)     # (Pfad, Grund)
    errors: list = field(default_factory=list)      # (Pfad, Fehler)
    bytes_before: int = 0
    bytes_after: int = 0


def find_files(job: ConvertJob) -> list[str]:
    target_ext = TARGET_EXT[job.target]
    exts = tuple(e for s in job.sources for e in SOURCE_EXTS.get(s, ()) if e != target_ext)
    out = []
    for folder, dirs, files in os.walk(job.root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        if not job.recursive:
            dirs[:] = []
        for name in sorted(files):
            low = name.lower()
            if low.endswith(exts) and ".tmp." not in low:
                out.append(os.path.join(folder, name))
    return out


def to_8bit(data: np.ndarray) -> np.ndarray:
    """Grauwerte/Farben unverändert nach 8 Bit. War Wert 0 im Original nicht belegt
    (z. B. Ausgabe 1–255 für die Photogrammetrie), bleibt das auch in 8 Bit so."""
    if data.dtype == np.uint8:
        return data
    out = P.to_uint(P.as_float(data), 8)
    if data.size and data.min() > 0:
        np.maximum(out, 1, out=out)
    return out


def convert_file(src: str, job: ConvertJob) -> str:
    loaded = P.load_image(src)
    data = loaded.color if loaded.color is not None else loaded.data
    if job.target in ("jpg", "tif8") or data.dtype != np.uint16:
        data = to_8bit(data)
    dst = os.path.splitext(src)[0] + TARGET_EXT[job.target]
    if os.path.abspath(dst) == os.path.abspath(src):
        raise ValueError("Quelle und Ziel sind dieselbe Datei")
    # erst in eine Zwischendatei, dann austauschen – nie eine halbe Datei hinterlassen
    tmp = dst + ".tmp" + TARGET_EXT[job.target]
    affine = loaded.geo.affine if loaded.geo is not None else None
    try:
        P.save_image(tmp, data, job.target, loaded.dpi, loaded.geo, affine,
                     jpeg_quality=job.quality)
        check = P.load_image(tmp)                         # lässt sich das Ergebnis lesen?
        if check.data.shape[:2] != data.shape[:2]:
            raise IOError("geschriebene Datei ist unvollständig")
        os.replace(tmp, dst)
        stem_tmp, stem_dst = os.path.splitext(tmp)[0], os.path.splitext(dst)[0]
        folder = os.path.dirname(dst) or "."
        for f in os.listdir(folder):                      # World-File/.prj umbenennen
            full = os.path.join(folder, f)
            if f.startswith(os.path.basename(stem_tmp) + ".") and full != dst:
                os.replace(full, stem_dst + f[len(os.path.basename(stem_tmp)):])
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return dst


def run_convert(job: ConvertJob, files=None, progress=None, cancelled=None) -> ConvertResult:
    files = find_files(job) if files is None else files
    res = ConvertResult()
    for i, src in enumerate(files):
        if cancelled and cancelled():
            break
        dst = os.path.splitext(src)[0] + TARGET_EXT[job.target]
        try:
            if os.path.exists(dst) and not job.overwrite:
                res.skipped.append((src, f"{os.path.basename(dst)} gibt es schon"))
            else:
                size = os.path.getsize(src)
                out = convert_file(src, job)
                res.written.append(out)
                res.bytes_before += size
                res.bytes_after += os.path.getsize(out)
                if job.delete_source:
                    os.remove(src)
                    for ext in (".tfw", ".tifw", ".aux.xml"):
                        side = os.path.splitext(src)[0] + ext
                        if os.path.exists(side):
                            os.remove(side)
        except Exception as exc:   # eine defekte Datei soll den Rest nicht aufhalten
            res.errors.append((src, f"{type(exc).__name__}: {exc}"))
        if progress:
            progress(i + 1, len(files))
    return res


# --------------------------------------------------------------------------- Dialog
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,  # noqa: E402
                               QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout)


class ConvertDialog(QDialog):
    def __init__(self, parent, start_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Ordner umwandeln")
        self.resize(620, 360)
        lay = QVBoxLayout(self)
        info = QLabel(
            "Wandelt alle Bilder eines Ordners – auf Wunsch mit allen Unterordnern – in ein "
            "anderes Format um, z. B. TIFF in JPEG, um Speicherplatz zu sparen. Die Bilder "
            "werden dabei <b>nicht</b> bearbeitet (kein Filter, kein Zuschnitt). "
            "Georeferenz (World-File, .prj) wird übernommen. Ordner „Originale_Sicherung“ "
            "werden übersprungen.")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        row = QHBoxLayout()
        self.folder = QLineEdit(start_dir)
        b = QPushButton("…")
        b.setFixedWidth(32)
        b.clicked.connect(self._pick)
        row.addWidget(self.folder)
        row.addWidget(b)
        form.addRow("Ordner", row)
        self.chk_rec = QCheckBox("Unterordner einbeziehen")
        self.chk_rec.setChecked(True)
        form.addRow(self.chk_rec)
        self.source = QComboBox()
        self.source.addItem("TIFF (.tif, .tiff)", ("tif",))
        self.source.addItem("TIFF und PNG", ("tif", "png"))
        form.addRow("Umwandeln von", self.source)
        self.target = QComboBox()
        self.target.addItem("JPEG (klein, verlustbehaftet)", "jpg")
        self.target.addItem("PNG (verlustfrei)", "png")
        self.target.currentIndexChanged.connect(
            lambda _: self.quality.setEnabled(self.target.currentData() == "jpg"))
        form.addRow("in", self.target)
        self.quality = QSpinBox()
        self.quality.setRange(60, 100)
        self.quality.setValue(92)
        self.quality.setToolTip("92: kaum sichtbarer Unterschied, sehr viel kleiner als TIFF. "
                                "Höher = besser und größer.")
        form.addRow("JPEG-Qualität", self.quality)
        self.chk_delete = QCheckBox("Ursprüngliche Datei nach erfolgreicher Umwandlung löschen "
                                    "(spart den Platz)")
        form.addRow(self.chk_delete)
        self.chk_over = QCheckBox("Vorhandene Datei mit gleichem Namen überschreiben")
        self.chk_over.setToolTip("Sonst wird ein Bild übersprungen, wenn es die Zieldatei "
                                 "(z. B. bild.jpg neben bild.tif) schon gibt.")
        form.addRow(self.chk_over)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Umwandeln")
        buttons.button(QDialogButtonBox.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner wählen", self.folder.text())
        if d:
            self.folder.setText(d)

    def job(self) -> ConvertJob:
        return ConvertJob(self.folder.text().strip(), self.source.currentData(),
                          self.target.currentData(), self.quality.value(),
                          self.chk_rec.isChecked(), self.chk_delete.isChecked(),
                          self.chk_over.isChecked())

    def _accept(self):
        if not os.path.isdir(self.folder.text().strip()):
            self._pick()
            if not os.path.isdir(self.folder.text().strip()):
                return
        files = find_files(self.job())
        if not files:
            QMessageBox.information(self, "Keine Dateien",
                                    "In diesem Ordner wurden keine passenden Bilder gefunden.")
            return
        size = sum(os.path.getsize(f) for f in files) / 1e6
        text = (f"{len(files)} Datei(en) ({size:,.0f} MB) werden umgewandelt.".replace(",", ".")
                + ("\n\nDie ursprünglichen Dateien werden danach GELÖSCHT (nur wenn die "
                   "Umwandlung fehlerfrei war)." if self.chk_delete.isChecked() else
                   "\n\nDie ursprünglichen Dateien bleiben erhalten.")
                + "\n\nFortfahren?")
        if QMessageBox.question(self, "Ordner umwandeln", text) != QMessageBox.Yes:
            return
        self._files = files
        self.accept()
