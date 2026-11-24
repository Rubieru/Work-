"""Originale ersetzen: sicher, im richtigen Format, mit Sicherungskopie."""
import filecmp
import os

import numpy as np
import pytest

from luftbild_editor import batch as B
from luftbild_editor import processing as P
from tests.sample import synthetic_aerial


def make_folder(tmp_path):
    img = synthetic_aerial(300, 200)
    files = {}
    for name, fmt, bits in (("a.tif", "tif8", 8), ("b.tif", "tif16", 16),
                            ("c.png", "png", 8), ("d.jpg", "jpg", 8)):
        path = tmp_path / name
        P.save_image(str(path), P.to_uint(img, bits), fmt)
        files[name] = str(path)
    return files


def job_for(files, rotation=90.0, crop=None, backup=True):
    geos = {B.path_key(f): (rotation, crop) for f in files}
    return B.BatchJob(list(files), "", "tif16", "", False, None, geos, None,
                      overwrite=True, backup=backup)


def test_replace_originals_keeps_name_format_and_makes_backup(tmp_path):
    files = make_folder(tmp_path)
    before = {n: P.load_image(p) for n, p in files.items()}
    written, errors = B.run_batch(job_for(files.values()), P.Settings())
    assert not errors and sorted(written) == sorted(files.values())
    for name, path in files.items():
        after = P.load_image(path)
        assert after.data.shape == (300, 200)                 # um 90° gedreht
        assert after.bits == before[name].bits                 # 8 bleibt 8, 16 bleibt 16
        # Inhalt: gedrehtes Original (JPG verlustbehaftet -> grob vergleichen)
        ref = P.as_float(np.rot90(before[name].data, -1))
        assert np.abs(P.as_float(after.data) - ref).mean() < 0.03
        # Sicherung byte-genau
        backup = tmp_path / B.BACKUP_DIR / name
        assert backup.exists()
        np.testing.assert_array_equal(P.load_image(str(backup)).data, before[name].data)
    assert not (tmp_path / B._TMP_DIR).exists()               # keine Zwischendateien
    assert sorted(os.listdir(tmp_path)) == sorted(list(files) + [B.BACKUP_DIR])


def test_second_save_keeps_first_backup(tmp_path):
    files = make_folder(tmp_path)
    orig = tmp_path / "orig_a.tif"
    orig.write_bytes((tmp_path / "a.tif").read_bytes())
    B.run_batch(job_for([files["a.tif"]]), P.Settings())
    B.run_batch(job_for([files["a.tif"]]), P.Settings())    # nochmal ersetzen
    # die Sicherung ist weiterhin das echte Original, nicht der Zwischenstand
    assert filecmp.cmp(tmp_path / B.BACKUP_DIR / "a.tif", orig, shallow=False)


def test_failed_export_leaves_original_untouched(tmp_path, monkeypatch):
    files = make_folder(tmp_path)
    original = (tmp_path / "a.tif").read_bytes()

    def broken_export(doc, s, path, fmt, **kw):
        with open(path, "wb") as f:
            f.write(b"halb geschrieben")
        raise OSError("Datenträger voll")
    monkeypatch.setattr(P, "export", broken_export)
    written, errors = B.run_batch(job_for([files["a.tif"]], backup=False), P.Settings())
    assert not written and errors and "Datenträger voll" in errors[0][1]
    assert (tmp_path / "a.tif").read_bytes() == original
    assert not (tmp_path / B._TMP_DIR).exists()


def test_unsupported_format_is_reported_not_changed(tmp_path):
    path = tmp_path / "e.bmp"
    P.save_image(str(path), P.to_uint(synthetic_aerial(120, 80), 8), "png")  # Inhalt egal
    data = path.read_bytes()
    written, errors = B.run_batch(job_for([str(path)]), P.Settings())
    assert not written and "nicht ersetzt" in errors[0][1]
    assert path.read_bytes() == data


def test_replace_world_file_georeference(tmp_path):
    path = tmp_path / "geo.png"
    P.save_image(str(path), P.to_uint(synthetic_aerial(200, 100), 8), "png")
    (tmp_path / "geo.pgw").write_text("2\n0\n0\n-2\n1001\n2001\n")
    B.run_batch(job_for([str(path)], rotation=0.0, crop=(10, 20, 100, 50)), P.Settings())
    vals = [float(v) for v in (tmp_path / "geo.pgw").read_text().split()]
    # neue obere linke Pixelmitte: 1000 + 2*(10+0.5), 2002 - 2*(20+0.5)
    assert vals[4] == pytest.approx(1021.0) and vals[5] == pytest.approx(1961.0)
    assert (tmp_path / B.BACKUP_DIR / "geo.pgw").exists()
