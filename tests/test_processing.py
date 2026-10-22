import numpy as np
from dataclasses import replace
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
    s = P.with_tonal(P.Settings(), BUILTIN_PRESETS["Helligkeitsausgleich (Vignettierung)"])
    prep = doc.prepare(s)
    full = P.render(prep, s)
    small = P.render(prep, s, scale=0.25)
    assert small.shape == (250, 350)
    import cv2
    down = cv2.resize(full, (350, 250), interpolation=cv2.INTER_AREA)
    assert abs(float(down.mean()) - float(small.mean())) < 0.03


def test_region_render_shape():
    doc = make_doc()
    s = P.with_tonal(P.Settings(), BUILTIN_PRESETS["Medianfilter + Hochpassfilter"])
    out = P.render(doc.prepare(s), s, (100, 50, 200, 120), 0.5)
    assert out.shape == (60, 100)


def test_rotation_grows_canvas_and_keeps_whole_image():
    doc = make_doc()                       # 700 x 500
    assert doc.prepare(P.Settings(rotation=90)).size == (500, 700)
    assert doc.prepare(P.Settings(rotation=-180)).size == (700, 500)
    w, h = doc.prepare(P.Settings(rotation=45)).size
    assert w > 840 and h > 840             # Ecken werden nicht abgeschnitten
    np.testing.assert_array_equal(doc.prepare(P.Settings(rotation=90)).base,
                                  np.rot90(doc.raw, -1))   # verlustfrei, im Uhrzeigersinn
    np.testing.assert_array_equal(doc.prepare(P.Settings(rotation=-270)).base,
                                  doc.prepare(P.Settings(rotation=90)).base)


def test_crop_follows_rotation():
    doc = make_doc()
    img = doc.raw.copy()
    img[100:110, 200:230] = 1.0            # Markierung
    doc = P.Document(P.LoadedImage(img, 16, None))
    crop = (190, 90, 50, 30)
    for angle in (90, 180, -90, 30):
        c = P.follow_rotation(crop, doc.size, 0, angle)
        base = doc.prepare(P.Settings(rotation=angle, crop=c)).base
        assert base.mean() > 0.4, angle    # Markierung liegt weiter im Rahmen
    assert P.follow_rotation(crop, doc.size, 0, 90)[2:] == (30, 50)


def test_save_and_load_roundtrip(tmp_path):
    doc = make_doc()
    s = P.with_tonal(P.Settings(crop=(0, 0, 300, 200)), BUILTIN_PRESETS["Streckung + Hochpassfilter"])
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
    x = np.linspace(-0.1, 1.1, 1201, dtype=np.float32)
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
        means[name] = float(np.median(out))
        assert np.mean(out > 0.99) < 0.01 and np.mean(out < 0.01) < 0.01
    # Helligkeit des Originals bleibt erhalten, sehr dunkle Scans werden angehoben
    assert means["dunkel"] >= P.KEEP_MEDIAN[0] - 0.02
    assert all(m <= P.KEEP_MEDIAN[1] + 0.02 for m in means.values())


def test_auto_preset_values():
    from luftbild_editor.presets import AUTO, preset_values
    doc = make_doc()
    assert preset_values(AUTO) is None
    v = preset_values(AUTO, doc.prepare(P.Settings()))
    assert v["stretch"] and 0.0 <= v["hist_flat"] <= 0.5 and v["stats_center"]


def test_histogram_accepts_large_1d_samples():
    h = P.histogram(np.random.default_rng(0).random(3_000_000).astype(np.float32))
    assert h.sum() > 0 and len(h) == 256


def test_frame_mask_ignores_black_scan_frame():
    from tests.sample import synthetic_aerial
    img = synthetic_aerial(900, 700)[60:-60, 60:-60].copy()
    img = np.pad(img, 60, constant_values=0.02)        # schwarzer Scanrahmen
    img[10:30, 300:380] = 0.95                         # weiße Bildnummer im Rahmen
    valid = P.frame_mask(img)
    assert not valid[20, 340] and not valid[5, 5]      # Rahmen + Beschriftung
    assert valid[350, 450]                             # Bildinhalt
    assert 0.6 < valid.mean() < 0.9


def test_flatten_makes_histogram_flatter():
    from tests.sample import synthetic_aerial
    img = 0.4 + 0.2 * synthetic_aerial(800, 600)[50:-50, 50:-50]
    doc = P.Document(P.LoadedImage(img.astype(np.float32), 16, None))
    prep = doc.prepare(P.Settings())

    def peak(s):
        o = P.render(prep, s)
        h, _ = np.histogram(o, bins=64, range=(0, 1))
        return np.convolve(h / h.sum(), np.ones(16), "valid").max()

    base = P.Settings(stretch=True, clip_black=0.1, clip_white=0.05)
    assert peak(replace(base, hist_flat=0.9, hist_mode="gleichmaessig")) < peak(base) - 0.05
    assert peak(replace(base, hist_flat=0.8, hist_mode="glocke")) < peak(base)


def test_curve_lut_is_monotone_and_hits_points():
    lut = P.curve_lut(((0.25, 0.4), (0.75, 0.8)), 1001)
    assert abs(lut[250] - 0.4) < 1e-3 and abs(lut[750] - 0.8) < 1e-3
    assert np.all(np.diff(lut) >= -1e-6)
    s = P.Settings(curve=((0.5, 0.7),))
    assert P.settings_from_json(P.settings_to_json(s)) == s


def test_nodata_safe_output_has_no_zero():
    out = P.to_uint(np.array([[0.0, 0.5, 1.0]], np.float32), 8, nodata_safe=True)
    assert out.min() == 1 and out.max() == 255


def test_flatten_keeps_brightness_and_highlight_detail():
    # Gipfel in den Mitteltönen, wenige helle Pixel (Dächer, Schnee)
    rng = np.random.default_rng(1)
    vals = np.concatenate([rng.normal(0.5, 0.06, 90_000), rng.uniform(0.75, 1.0, 10_000)])
    img = np.clip(vals, 0, 1).astype(np.float32).reshape(250, 400)
    doc = P.Document(P.LoadedImage(img, 16, None))
    prep = doc.prepare(P.Settings())
    base = P.Settings(stretch=True, clip_black=0.1, clip_white=0.05, stats_center=False)
    lin = P.render(prep, base)
    for mode in ("glocke", "begrenzt"):   # "gleichmaessig" ist bewusst stark
        out = P.render(prep, replace(base, hist_flat=1.0, hist_mode=mode))
        assert abs(float(np.median(out)) - float(np.median(lin))) < 0.1
        hl = vals.reshape(250, 400) > 0.8      # Zeichnung in den Lichtern bleibt
        assert out[hl].std() > 0.5 * lin[hl].std()


def test_median_and_lowpass_filters():
    doc = make_doc()
    img = doc.raw.copy()
    img[100, 100] = 1.0                          # Störpixel
    doc = P.Document(P.LoadedImage(img, 16, None))
    out = P.render(doc.prepare(P.Settings()), P.Settings(median=3))
    assert abs(out[100, 100] - img[99, 100]) < 0.05
    smooth = P.render(doc.prepare(P.Settings()), P.Settings(lowpass=1.5))
    assert smooth.std() < img.std()
    for s in (P.Settings(median=5), P.Settings(lowpass=2.0)):   # nahtlose Kacheln
        whole = P.to_uint(P.render(doc.prepare(s), s), 16)
        diff = np.abs(whole.astype(int) - P.render_full(doc, s, bits=16, tile=128).astype(int))
        assert diff.max() <= 2


def test_keep_brightness_stretches_without_brightening():
    # Schatten schon fast schwarz, Lichter nur bis ~0,8 – wie viele Scans
    rng = np.random.default_rng(3)
    img = np.clip(0.02 + rng.gamma(3.0, 0.12, (400, 500)), 0, 0.8).astype(np.float32)
    doc = P.Document(P.LoadedImage(img, 16, None))
    prep = doc.prepare(P.Settings())
    base = P.Settings(stretch=True, clip_black=0.1, clip_white=0.05, stats_center=False)
    lin = P.render(prep, base)
    keep = P.render(prep, replace(base, keep_brightness=True))
    med = float(np.median(img))
    assert 0.3 < med < 0.5
    assert np.median(lin) > med + 0.03                  # lineare Streckung hellt auf
    assert abs(float(np.median(keep)) - med) < 0.02     # Helligkeit bleibt
    assert keep.max() > 0.95 and keep.min() < 0.05      # trotzdem ganzer Bereich
