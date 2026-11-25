"""Ordner umwandeln (z. B. TIFF -> JPEG) mit Unterordnern."""
import os

import numpy as np

from luftbild_editor import convert as C
from luftbild_editor import processing as P
from tests.sample import synthetic_aerial


def make_tree(tmp_path):
    img = synthetic_aerial(400, 300)
    (tmp_path / "A" / "B").mkdir(parents=True)
    (tmp_path / "Originale_Sicherung").mkdir()
    files = {
        "eins.tif": (tmp_path / "eins.tif", P.to_uint(img, 16, nodata_safe=True), "tif16"),
        "A/zwei.tif": (tmp_path / "A" / "zwei.tif", P.to_uint(img, 8), "tif8"),
        "A/B/drei.tiff": (tmp_path / "A" / "B" / "drei.tiff", P.to_uint(img, 8), "tif8"),
        "Originale_Sicherung/alt.tif": (tmp_path / "Originale_Sicherung" / "alt.tif",
                                        P.to_uint(img, 8), "tif8"),
    }
    rgb = np.dstack([img, img * 0.8, img * 0.5])
    files["A/farbe.tif"] = (tmp_path / "A" / "farbe.tif", P.to_uint(rgb, 8), "tif8")
    for path, arr, fmt in files.values():
        P.save_image(str(path), arr, fmt)
    return files


def test_convert_tree_to_jpeg_and_delete_sources(tmp_path):
    files = make_tree(tmp_path)
    job = C.ConvertJob(str(tmp_path), delete_source=True)
    found = C.find_files(job)
    assert len(found) == 4                                     # Sicherung übersprungen
    res = C.run_convert(job, found)
    assert not res.errors and len(res.written) == 4
    assert res.bytes_after < res.bytes_before / 3              # deutlich kleiner
    for rel in ("eins", "A/zwei", "A/B/drei", "A/farbe"):
        jpg = tmp_path / (rel + ".jpg")
        assert jpg.exists()
        assert not (tmp_path / (rel + ".tif")).exists() and not (tmp_path / (rel + ".tiff")).exists()
        back = P.load_image(str(jpg))
        assert back.data.shape == (300, 400) and back.bits == 8
    assert (tmp_path / "Originale_Sicherung" / "alt.tif").exists()
    assert P.load_image(str(tmp_path / "A" / "farbe.jpg")).color is not None   # Farbe bleibt
    # Inhalt unverändert (bis auf JPEG-Verluste), 1–255 bleibt 1–255
    a = P.as_float(P.load_image(str(tmp_path / "eins.jpg")).data)
    ref = P.as_float(files["eins.tif"][1])
    assert np.abs(a - ref).mean() < 0.02
    assert not any(n.endswith(".tmp.jpg") for _, _, fs in os.walk(tmp_path) for n in fs)


def test_existing_target_is_skipped_unless_overwrite(tmp_path):
    make_tree(tmp_path)
    (tmp_path / "eins.jpg").write_bytes(b"alt")
    res = C.run_convert(C.ConvertJob(str(tmp_path), recursive=False))
    assert res.skipped and (tmp_path / "eins.jpg").read_bytes() == b"alt"
    assert (tmp_path / "eins.tif").exists()                     # nichts gelöscht
    res = C.run_convert(C.ConvertJob(str(tmp_path), recursive=False, overwrite=True))
    assert res.written and P.load_image(str(tmp_path / "eins.jpg")).data.shape == (300, 400)


def test_16bit_without_zero_stays_without_zero():
    data = np.array([[1, 200, 65535]], np.uint16)
    out = C.to_8bit(data)
    assert out.min() == 1 and out.max() == 255


def test_failed_conversion_keeps_source(tmp_path, monkeypatch):
    make_tree(tmp_path)
    src = tmp_path / "eins.tif"
    data = src.read_bytes()

    def broken(*a, **k):
        raise OSError("Datenträger voll")
    monkeypatch.setattr(P, "save_image", broken)
    res = C.run_convert(C.ConvertJob(str(tmp_path), recursive=False, delete_source=True))
    assert res.errors and src.read_bytes() == data and not (tmp_path / "eins.jpg").exists()


def test_world_file_carried_over(tmp_path):
    img = P.to_uint(synthetic_aerial(200, 100), 8)
    P.save_image(str(tmp_path / "geo.tif"), img, "tif8")
    (tmp_path / "geo.tfw").write_text("2\n0\n0\n-2\n1001\n2001\n")
    res = C.run_convert(C.ConvertJob(str(tmp_path), delete_source=True))
    assert not res.errors
    vals = [float(v) for v in (tmp_path / "geo.jgw").read_text().split()]
    assert vals[0] == 2 and vals[4] == 1001 and vals[5] == 2001
    assert not (tmp_path / "geo.tfw").exists() and not (tmp_path / "geo.tif").exists()
