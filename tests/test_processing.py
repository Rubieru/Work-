import numpy as np
import pytest

from luftbild_editor import processing as P
from luftbild_editor.presets import BUILTIN_PRESETS


def make_doc(w=700, h=500, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    img = 0.35 + 0.1 * np.sin(xx / 13.0) * np.cos(yy / 17.0)
    img += 0.08 * (xx / w)  # ungleiche Ausleuchtung
    img += rng.normal(0, 0.01, img.shape)
    return P.Document(P.LoadedImage(np.clip(img, 0, 1).astype(np.float32), 16, 300.0))


def test_crop_is_pixel_exact():
    doc = make_doc()
    s = P.Settings(crop=(13, 27, 101, 57))
    prep = doc.prepare(s)
    assert prep.size == (101, 57)
    np.testing.assert_array_equal(prep.base, doc.original[27:84, 13:114])
    out = P.render(prep, s)
    np.testing.assert_allclose(out, doc.original[27:84, 13:114], atol=1e-6)


def test_crop_is_clamped():
    assert P.clamp_crop((-5, -5, 5000, 5000), 100, 80) == (0, 0, 100, 80)
    assert P.clamp_crop(None, 100, 80) == (0, 0, 100, 80)


def test_histogram_stretch_fills_range():
    doc = make_doc()
    s = P.Settings(stretch=True, clip_black=0.5, clip_white=0.5)
    out = P.render(doc.prepare(s), s)
    assert out.min() == 0.0 and out.max() == 1.0
    assert np.percentile(out, 5) < 0.15 and np.percentile(out, 95) > 0.85


def test_neutral_settings_change_nothing():
    doc = make_doc()
    out = P.render(doc.prepare(P.Settings()), P.Settings())
    np.testing.assert_allclose(out, doc.original, atol=1e-6)


@pytest.mark.parametrize("name", list(BUILTIN_PRESETS))
def test_tiled_render_matches_whole_image(name):
    doc = make_doc()
    s = P.with_tonal(P.Settings(crop=(10, 5, 650, 480)), BUILTIN_PRESETS[name])
    whole = P.to_uint(P.render(doc.prepare(s), s), 16)
    tiled = P.render_full(doc, s, bits=16, tile=128)
    diff = np.abs(whole.astype(int) - tiled.astype(int))
    assert diff.max() <= 2


def test_preview_approximates_full_resolution():
    doc = make_doc(1400, 1000)
    s = P.with_tonal(P.Settings(), BUILTIN_PRESETS["Ungleich belichtet"])
    prep = doc.prepare(s)
    full = P.render(prep, s)
    small = P.render(prep, s, scale=0.25)
    assert small.shape == (250, 350)
    import cv2
    down = cv2.resize(full, (350, 250), interpolation=cv2.INTER_AREA)
    assert abs(float(down.mean()) - float(small.mean())) < 0.03


def test_region_render_shape():
    doc = make_doc()
    s = P.with_tonal(P.Settings(), BUILTIN_PRESETS["Kräftig"])
    out = P.render(doc.prepare(s), s, (100, 50, 200, 120), 0.5)
    assert out.shape == (60, 100)


def test_rotation_keeps_canvas():
    doc = make_doc()
    s = P.Settings(rotation=2.5)
    assert doc.prepare(s).size == doc.size


def test_save_and_load_roundtrip(tmp_path):
    doc = make_doc()
    s = P.with_tonal(P.Settings(crop=(0, 0, 300, 200)), BUILTIN_PRESETS["Standard"])
    for fmt, (_, ext) in P.EXPORT_FORMATS.items():
        path = str(tmp_path / f"Luftbild_ä_{fmt}{ext}")
        P.export(doc, s, path, fmt)
        back = P.load_image(path)
        assert back.data.shape == (200, 300)
        assert back.bits == (16 if fmt == "tif16" else 8)
        if fmt.startswith("tif"):
            assert back.dpi == pytest.approx(300.0)


def test_settings_json_roundtrip():
    s = P.Settings(rotation=1.25, crop=(1, 2, 3, 4), stretch=True, clarity=0.3)
    assert P.settings_from_json(P.settings_to_json(s)) == s
