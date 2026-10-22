import cv2
import numpy as np
import pytest

from luftbild_editor import processing as P

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import Affine  # noqa: E402


def make_geotiff(path, w=400, h=300):
    rng = np.random.default_rng(0)
    data = (rng.random((h, w)) * 200 + 20).astype(np.uint8)
    tr = Affine(0.5, 0, 412000.0, 0, -0.5, 5650000.0)  # 0,5 m Pixel, UTM 32N
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=1,
                       dtype="uint8", crs="EPSG:25832", transform=tr) as ds:
        ds.write(data, 1)
    return tr


def test_crop_keeps_georeference(tmp_path):
    src = tmp_path / "geo.tif"
    tr = make_geotiff(src)
    doc = P.Document.open(str(src))
    assert doc.geo is not None
    out = tmp_path / "aus.tif"
    s = P.Settings(crop=(40, 30, 200, 150), stretch=True)
    P.export(doc, s, str(out), "tif8")
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 25832
        assert ds.transform.almost_equals(tr * Affine.translation(40, 30))
        assert (ds.width, ds.height) == (200, 150)


@pytest.mark.parametrize("angle", [3.0, 90.0, -135.0])
def test_rotation_keeps_georeference(tmp_path, angle):
    src = tmp_path / "geo.tif"
    tr = make_geotiff(src)
    doc = P.Document.open(str(src))
    s = P.Settings(rotation=angle, crop=(50, 40, 250, 200))
    out = tmp_path / "rot.tif"
    P.export(doc, s, str(out), "tif16")
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 25832
        otr = ds.transform
    # Ausgabepixel -> Original über die Drehmatrix, beide Wege müssen gleich ankommen
    # Drehung um die Bildmitte, Leinwand wächst mit (unabhängig nachgerechnet)
    nw, nh = P.rotated_size(400, 300, angle)
    m = cv2.getRotationMatrix2D(((400 - 1) / 2, (300 - 1) / 2), -angle, 1.0)
    m[:, 2] += ((nw - 400) / 2, (nh - 300) / 2)
    inv = cv2.invertAffineTransform(m)
    for u, v in ((0, 0), (100, 50), (249, 199)):
        xr, yr = u + 50, v + 40                     # Pixelmitte im gedrehten Bild
        xo, yo = inv @ np.array([xr, yr, 1.0])      # Pixelmitte im Original
        expected = tr * (xo + 0.5, yo + 0.5)
        got = otr * (u + 0.5, v + 0.5)
        assert np.allclose(got, expected, atol=1e-6)


def test_world_file_and_nodata_safe(tmp_path):
    src = tmp_path / "scan.png"
    P.save_image(str(src), np.full((100, 120), 0, np.uint8), "png")
    (tmp_path / "scan.pgw").write_text("2\n0\n0\n-2\n1001\n2001\n")
    (tmp_path / "scan.prj").write_text("PROJCS[...]")
    doc = P.Document.open(str(src))
    out = tmp_path / "aus.png"
    P.export(doc, P.Settings(crop=(10, 20, 50, 40)), str(out), "png", nodata_safe=True)
    vals = [float(v) for v in (tmp_path / "aus.pgw").read_text().split()]
    assert vals == pytest.approx([2, 0, 0, -2, 1001 + 20, 2001 - 40])
    assert (tmp_path / "aus.prj").exists()
    assert P.load_image(str(out)).data.min() > 0  # kein Pixel mit Wert 0


def test_border_statistics_ignore_scan_frame():
    img = np.full((400, 400), 0.4, np.float32)
    img[:, :] = np.linspace(0.3, 0.6, 400)[None, :]
    img[:12, :] = 0.0       # schwarzer Scanrahmen
    img[-12:, :] = 0.0
    img[3:9, 150:250] = 1.0  # weiße Bildnummer im Rahmen
    doc = P.Document(P.LoadedImage(img, 8, None))
    s = P.Settings(stretch=True, clip_black=0.5, clip_white=0.5)
    lo, hi = P.compute_levels(doc.prepare(s), s)
    assert lo > 0.25 and hi < 0.65
    _, hi2 = P.compute_levels(doc.prepare(s), P.Settings(stretch=True, stats_center=False,
                                                          clip_white=0.1))
    assert hi2 > 0.9  # ohne Randerkennung zählt die weiße Bildnummer mit
