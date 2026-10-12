# -*- coding: utf-8 -*-
"""Erzeugt synthetische Testdaten: gekrümmter Fluss, Linien, Excel, DGM1-Kacheln.

Querprofil 1945 (symmetrisch, Abstand von der Achse -> Höhe):
  Sohle   +-20 m : 40.0 - 0.0002*s   (s = Station in m)
  Berme   +-35 m : 44.0 - 0.0002*s
  Deich   +-50 m : 48.0 - 0.0002*s   (links 0.5 m höher)
Heutiges DGM1 = Profil 1945 + 1.0 m (überall Auftrag 1 m).
"""
import os
import sys

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()

ABST = {"Sohle": 20.0, "Berme": 35.0, "Deich": 50.0}
BASIS = {"Sohle": 40.0, "Berme": 44.0, "Deich": 48.0}
GEFAELLE = 0.0002
X0, Y0 = 400000.0, 5700000.0


def achse_xy(n=400, laenge=2000.0):
    x = np.linspace(0, laenge, n)
    y = 80 * np.sin(x / 300.0)
    return np.c_[x + X0, y + Y0]


def versatz(c, d):
    t = np.gradient(c, axis=0)
    t /= np.hypot(*t.T)[:, None]
    n = np.c_[-t[:, 1], t[:, 0]]   # links der Digitalisierrichtung
    return c + n * d


def soll_hoehe(typ, s, seite):
    h = BASIS[typ] - GEFAELLE * s
    if typ == "Deich" and seite == "links":
        h += 0.5
    return h


def main(ordner):
    os.makedirs(ordner, exist_ok=True)
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25832)
    ach = achse_xy()

    # Linien-Shape
    pfad = os.path.join(ordner, "linien.shp")
    drv = ogr.GetDriverByName("ESRI Shapefile")
    if os.path.exists(pfad):
        drv.DeleteDataSource(pfad)
    ds = drv.CreateDataSource(pfad)
    lyr = ds.CreateLayer("linien", srs, ogr.wkbLineString)
    lyr.CreateField(ogr.FieldDefn("typ", ogr.OFTString))

    def linie(c, typ):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("typ", typ)
        g = ogr.Geometry(ogr.wkbLineString)
        for x, y in c:
            g.AddPoint_2D(float(x), float(y))
        f.SetGeometry(g)
        lyr.CreateFeature(f)

    linie(ach, "Achse")
    for typ, d in ABST.items():
        linie(versatz(ach, d), typ)    # links
        linie(versatz(ach, -d), typ)   # rechts
    ds = None

    # Excel: km 10.000 ... 12.000 (Achsanfang = km 10.000), Station "10+250" & Dezimal gemischt
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Hoehen 1945"
    ws.append(["Höhenaufnahme 1945 – Testdaten"])
    ws.append([])
    ws.append(["Fluss-km", "Bezeichnung", "Höhe [m NHN]", "Seite"])
    for i, s in enumerate(np.arange(0, 2001, 100)):
        km = 10 + s / 1000
        kmw = f"{int(km)}+{int(round((km % 1) * 1000)):03d}" if i % 2 else round(km, 3)
        ws.append([kmw, "Sohle", round(soll_hoehe("Sohle", s, ""), 3), None])
        ws.append([kmw, "Berme", round(soll_hoehe("Berme", s, ""), 3), None])
        ws.append([kmw, "Deich", round(soll_hoehe("Deich", s, "links"), 3), "links"])
        ws.append([kmw, "Deich re.", round(soll_hoehe("Deich", s, "rechts"), 3), None])
    ws.append([99.0, "Sohle", 1.0, None])   # außerhalb -> Warnung
    wb.save(os.path.join(ordner, "hoehen_1945.xlsx"))

    # DGM1 als 2 Kacheln (1 m), = Profil + 1 m
    xs = np.arange(X0 - 100, X0 + 2100, 1.0) + 0.5
    ys = np.arange(Y0 + 200, Y0 - 200, -1.0) - 0.5
    gx, gy = np.meshgrid(xs, ys)
    # Abstand zur Achse/Station näherungsweise über dichte Achse
    dicht = achse_xy(4000)
    from scipy.spatial import cKDTree  # noqa  (nur für Testdaten)
    tree = cKDTree(dicht)
    dist, idx = tree.query(np.c_[gx.ravel(), gy.ravel()])
    seg = np.r_[0, np.cumsum(np.hypot(*np.diff(dicht, axis=0).T))]
    s = seg[idx]
    t = np.gradient(dicht, axis=0)[idx]
    rel = np.c_[gx.ravel(), gy.ravel()] - dicht[idx]
    links = (t[:, 0] * rel[:, 1] - t[:, 1] * rel[:, 0]) > 0
    profil_d = np.array([0, 20, 35, 50])
    z = np.empty_like(dist)
    for lr in (True, False):
        m = links == lr
        zs = np.array([[BASIS["Sohle"] - 0.0 , BASIS["Sohle"], BASIS["Berme"],
                        BASIS["Deich"] + (0.5 if lr else 0)]])
        z[m] = np.interp(dist[m], profil_d, zs[0]) - GEFAELLE * s[m]
    z = z.reshape(gx.shape) + 1.0
    z = np.where(np.isfinite(z), z, 30.0)
    hal = z.shape[1] // 2
    for k, (sl, x0) in enumerate(((slice(0, hal), xs[0] - 0.5), (slice(hal, None), xs[hal] - 0.5))):
        p = os.path.join(ordner, "dgm1", f"dgm1_{k}.tif")
        os.makedirs(os.path.dirname(p), exist_ok=True)
        a = z[:, sl]
        d = gdal.GetDriverByName("GTiff").Create(p, a.shape[1], a.shape[0], 1, gdal.GDT_Float32)
        d.SetGeoTransform((x0, 1, 0, ys[0] + 0.5, 0, -1))
        d.SetProjection(srs.ExportToWkt())
        d.GetRasterBand(1).WriteArray(a)
        d = None

    with open(os.path.join(ordner, "test.ini"), "w", encoding="utf-8") as f:
        f.write("[eingabe]\nexcel = hoehen_1945.xlsx\nlinien = linien.shp\n"
                "dgm1 = dgm1\n[kilometrierung]\nkm_start = 10.0\nkm_steigend = ja\n"
                "[ausgabe]\nausgabe_ordner = ergebnis\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "testdaten")
