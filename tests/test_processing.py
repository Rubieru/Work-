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
    s = P.Settings(stretch=True, clip_black=0.5, clip_white=0.5, protect=False)
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
    s = P.with_tonal(P.Settings(), BUILTIN_PRESETS["Details / Auswertung"])
    out = P.render(doc.prepare(s), s, (100, 50, 200, 120), 0.5)
    assert out.shape == (60, 100)


def test_rotation_keeps_canvas():
    doc = make_doc()
    s = P.Settings(rotation=2.5)
    assert doc.prepare(s).size == doc.size


def test_save_and_load_roundtrip(tmp_path):
    doc = make_doc()
    s = P.with_tonal(P.Settings(crop=(0, 0, 300, 200)), BUILTIN_PRESETS["Standard (ausgewogen)"])
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


def test_dust_removal_removes_spots():
    doc = make_doc()
    img = doc.original.copy()
    img[100, 100] = 1.0
    img[200:202, 300:302] = 0.0
    doc = P.Document(P.LoadedImage(img, 16, None))
    s = P.Settings(dust=0.8)
    out = P.render(doc.prepare(s), s)
    assert abs(out[100, 100] - img[99, 100]) < 0.1
    assert abs(out[200, 300] - img[198, 300]) < 0.1


def test_equalize_increases_local_contrast():
    doc = make_doc()
    s = P.Settings(stretch=True, equalize=1.0)
    prep = doc.prepare(s)
    out = P.render(prep, s)
    base = P.render(prep, P.Settings(stretch=True))
    assert out.std() > 0.9 * base.std()
    assert 0.0 <= out.min() and out.max() <= 1.0


def test_soft_clip_keeps_midtones_and_highlight_detail():
    x = np.linspace(-0.3, 1.3, 1601, dtype=np.float32)
    y = P.soft_clip(x)
    mid = (x > 0.15) & (x < 0.85)
    np.testing.assert_allclose(y[mid], x[mid], atol=1e-6)
    assert np.all(np.diff(y) >= 0)          # Reihenfolge der Helligkeiten bleibt
    assert y.max() < 1.0 and y.min() > 0.0  # kein hartes Abschneiden


def test_auto_adapts_to_dark_and_flat_scans():
    from tests.sample import synthetic_aerial
    base = synthetic_aerial(800, 600)[60:-60, 60:-60]  # ohne Scanrahmen
    variants = {"normal": base, "dunkel": base * 0.4, "flau_hell": 0.55 + base * 0.3}
    means = {}
    for name, img in variants.items():
        doc = P.Document(P.LoadedImage(np.clip(img, 0, 1).astype(np.float32), 16, None))
        prep = doc.prepare(P.Settings())
        values = P.auto_tonal(prep)
        out = P.render(prep, P.with_tonal(P.Settings(), values))
        means[name] = float(out.mean())
        assert np.mean(out > 0.99) < 0.01 and np.mean(out < 0.01) < 0.01
    assert max(means.values()) - min(means.values()) < 0.08


def test_auto_preset_values():
    from luftbild_editor.presets import AUTO, preset_values
    doc = make_doc()
    assert preset_values(AUTO) is None
    v = preset_values(AUTO, doc.prepare(P.Settings()))
    assert v["stretch"] and 0.15 <= v["equalize"] <= 0.7
