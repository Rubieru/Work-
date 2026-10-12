# -*- coding: utf-8 -*-
"""
DGM 1945 – Rekonstruktion der Geländeoberkante 1945 (GOK45)
===========================================================

Automatisierte Prozesskette:

  1. Excel/CSV-Tabelle mit Höhen 1945 einlesen (Kilometrierung, Name
     Sohle/Berme/Deich, Höhe in m ü. NHN, optional Seite links/rechts).
  2. Kilometrierung auf die Flussachse (Linien-Shape) übertragen und die
     Höhenpunkte auf den passenden Linien (Sohle/Berme/Deich) setzen.
  3. Punkte entlang der Linien verdichten (Bruchkanten-Wirkung).
  4. Triangulation (Delaunay-TIN, linear) -> GOK45.tif
  5. Differenz DGM1 - GOK45 -> Veränderung seit 1945
  6. GOK45 - 8 m -> maximale Bomben-Eindringtiefe (m ü. NHN)
     und Sondiertiefe unter heutiger GOK (DGM1 - (GOK45 - 8 m)).

Benötigt nur GDAL/OGR (Python-Bindings) und numpy – beides ist in jeder
QGIS-Installation (OSGeo4W) enthalten. openpyxl wird genutzt, falls
vorhanden (flexiblere Kopfzeilen-Erkennung), sonst liest GDAL die Excel-Datei.

Nutzung ohne QGIS-Oberfläche:
    python dgm1945_kern.py konfiguration.ini
"""

from __future__ import annotations

import configparser
import csv
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()
osr.UseExceptions()

NODATA = -9999.0


# --------------------------------------------------------------------------
# Einstellungen
# --------------------------------------------------------------------------
@dataclass
class Einstellungen:
    excel: str                      # Excel (.xlsx/.xls) oder CSV mit Höhen 1945
    linien: str                     # Linien-Layer (Shape/GPKG) mit Achse + Sohle/Berme/Deich
    ausgabe_ordner: str

    # Linien-Layer
    linien_typ_feld: str = "typ"    # Attributfeld mit Achse/Sohle/Berme/Deich
    linien_seite_feld: str = ""     # optional: Feld mit links/rechts
    achse_wert: str = "Achse"       # Wert im Typ-Feld für die Flussachse
    achse_datei: str = ""           # optional: Achse als eigener Layer

    # Kilometrierung
    km_start: Optional[float] = None    # km am Anfang (1. Stützpunkt) der Achse
    km_steigend: bool = True            # km nimmt in Digitalisierrichtung zu
    km_punkte: str = ""                 # optional: Punkt-Layer mit km-Steinen (Kalibrierung)
    km_punkte_feld: str = "km"
    km_einheit_m: float = 1000.0        # Meter je Einheit der Tabellen-Station (km -> 1000)
    fliessrichtung_km_steigend: bool = True  # für links/rechts (in Fließrichtung)

    # Tabelle
    excel_blatt: str = ""
    spalte_km: str = ""
    spalte_name: str = ""
    spalte_hoehe: str = ""
    spalte_seite: str = ""

    # Punktsetzung
    methode: str = "lot"            # "lot" (Querprofil senkrecht zur Achse) oder "entlang"
    suchweite: float = 1000.0       # halbe Länge der Profil-Lote in m
    tangenten_fenster: float = 20.0 # Glättung der Achsrichtung in m
    verdichtung: float = 5.0        # Punktabstand für Verdichtung entlang der Linien (0 = aus)

    # Raster
    dgm1: list = field(default_factory=list)   # DGM1-Kacheln (GeoTIFF, XYZ, ASC ...) oder Ordner
    pixelgroesse: float = 1.0       # nur falls kein DGM1 angegeben
    maske: str = ""                 # optional: Polygon zum Zuschneiden des TIN
    auto_maske: bool = True         # TIN auf Korridor zwischen äußersten Linien begrenzen
    masken_klasse: float = 10.0     # Stationsabstand (m) für die automatische Korridormaske
    epsg: int = 0                   # optional: CRS erzwingen (z.B. 25832)
    eindringtiefe: float = 8.0      # m


Logger = Callable[[str], None]


# --------------------------------------------------------------------------
# Hilfsfunktionen Text / Zahlen
# --------------------------------------------------------------------------
def norm(text) -> str:
    """Kleinschreibung, Umlaute ersetzen, Sonderzeichen -> Leerzeichen."""
    if text is None:
        return ""
    t = str(text).strip().lower()
    t = t.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def zahl(wert) -> Optional[float]:
    """Zahl aus Zelle lesen: 12.5 / '12,5' / ' 12.5 m' -> float."""
    if wert is None:
        return None
    if isinstance(wert, (int, float)):
        return None if (isinstance(wert, float) and math.isnan(wert)) else float(wert)
    s = str(wert).strip().replace("−", "-").replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    m = re.search(r"-?\d+(\.\d+)?", s)
    return float(m.group(0)) if m else None


def station_km(wert, einheit_m: float) -> Optional[float]:
    """Stationierung lesen. '12+345' -> 12345 m, 12.345 -> 12.345*einheit_m. Ergebnis in m."""
    if wert is None:
        return None
    if isinstance(wert, str) and "+" in wert:
        a, b = wert.replace(" ", "").split("+", 1)
        a, b = zahl(a), zahl(b)
        if a is None or b is None:
            return None
        return a * 1000.0 + b
    z = zahl(wert)
    return None if z is None else z * einheit_m


def seite_aus_text(text) -> str:
    t = norm(text)
    if not t:
        return ""
    if t.startswith("l"):
        return "links"
    if t.startswith("r"):
        return "rechts"
    return ""


_SEITE_TOKEN = {"l": "links", "li": "links", "lks": "links", "links": "links", "lu": "links",
                "r": "rechts", "re": "rechts", "rts": "rechts", "rechts": "rechts", "ru": "rechts"}


def name_zerlegen(name) -> tuple:
    """'Deich links' -> ('deich', 'links'); 'Sohle' -> ('sohle', '')."""
    tokens = norm(name).split()
    seite = ""
    rest = []
    for tok in tokens:
        if tok in _SEITE_TOKEN and not seite:
            seite = _SEITE_TOKEN[tok]
        else:
            rest.append(tok)
    return " ".join(rest), seite


def typ_passt(tabellen_typ: str, linien_typ: str) -> bool:
    if not tabellen_typ or not linien_typ:
        return False
    return (tabellen_typ == linien_typ or tabellen_typ.startswith(linien_typ)
            or linien_typ.startswith(tabellen_typ))


# --------------------------------------------------------------------------
# Tabelle einlesen
# --------------------------------------------------------------------------
_KW = {
    "km": ["km", "station", "stat", "kilometr", "fluss km", "stationierung"],
    "hoehe": ["hoehe", "hohe", "nhn", "nn", "z", "kote", "hoehen"],
    "seite": ["seite", "ufer"],
    "name": ["name", "bezeichnung", "typ", "art", "objekt", "element"],
}


def _passt_kw(kopf: str, kws) -> bool:
    h = norm(kopf)
    if not h:
        return False
    for kw in kws:
        if h == kw or h.split()[0] == kw:
            return True
        if len(kw) >= 4 and kw in h:
            return True
    return False


def _spalten_finden(kopf: list, vorgabe: dict) -> dict:
    """Ordnet Spaltenindizes zu (km/name/hoehe/seite)."""
    res = {}
    benutzt = set()
    for key, name in vorgabe.items():
        if name:
            for i, k in enumerate(kopf):
                if norm(k) == norm(name):
                    res[key] = i
                    benutzt.add(i)
                    break
            else:
                raise ValueError(f"Spalte '{name}' nicht in Tabelle gefunden. Vorhanden: {kopf}")
    for key in ("seite", "km", "hoehe", "name"):
        if key in res:
            continue
        for i, k in enumerate(kopf):
            if i not in benutzt and _passt_kw(k, _KW[key]):
                res[key] = i
                benutzt.add(i)
                break
    return res


def _zeilen_lesen(pfad: str, blatt: str) -> list:
    ext = os.path.splitext(pfad)[1].lower()
    if ext in (".csv", ".txt"):
        with open(pfad, newline="", encoding="utf-8-sig") as f:
            probe = f.read(4096)
            f.seek(0)
            try:
                dialekt = csv.Sniffer().sniff(probe, delimiters=";,\t")
            except csv.Error:
                dialekt = csv.excel
                dialekt.delimiter = ";"
            return [list(r) for r in csv.reader(f, dialekt)]
    if ext in (".xlsx", ".xlsm"):
        try:
            import openpyxl  # noqa
            wb = openpyxl.load_workbook(pfad, data_only=True, read_only=True)
            ws = wb[blatt] if blatt else wb.worksheets[0]
            return [list(r) for r in ws.iter_rows(values_only=True)]
        except ImportError:
            pass
    # Fallback: GDAL (XLSX/XLS/ODS), erste Zeile = Kopfzeile
    gdal.SetConfigOption("OGR_XLSX_HEADERS", "DISABLE")
    gdal.SetConfigOption("OGR_XLS_HEADERS", "DISABLE")
    gdal.SetConfigOption("OGR_ODS_HEADERS", "DISABLE")
    gdal.SetConfigOption("OGR_XLSX_FIELD_TYPES", "STRING")
    ds = ogr.Open(pfad)
    lyr = ds.GetLayerByName(blatt) if blatt else ds.GetLayer(0)
    zeilen = []
    for f in lyr:
        zeilen.append([f.GetField(i) for i in range(f.GetFieldCount())])
    return zeilen


@dataclass
class Messwert:
    station_m: float
    km_text: str
    name: str
    typ: str
    seite: str
    hoehe: float
    zeile: int


def tabelle_lesen(e: Einstellungen, log: Logger) -> list:
    zeilen = _zeilen_lesen(e.excel, e.excel_blatt)
    vorgabe = {"km": e.spalte_km, "name": e.spalte_name, "hoehe": e.spalte_hoehe, "seite": e.spalte_seite}
    kopf_idx, spalten = None, None
    for i, z in enumerate(zeilen[:40]):
        kopf = ["" if v is None else str(v) for v in z]
        try:
            sp = _spalten_finden(kopf, vorgabe)
        except ValueError:
            continue
        if {"km", "hoehe", "name"} <= sp.keys():
            kopf_idx, spalten = i, sp
            break
    if kopf_idx is None:
        raise ValueError("Kopfzeile der Tabelle nicht erkannt (benötigt Spalten für km, Name, Höhe). "
                         "Spaltennamen ggf. in den Einstellungen angeben. Erste Zeile: "
                         f"{zeilen[0] if zeilen else '-'}")
    kopf = zeilen[kopf_idx]
    log("Tabelle: Kopfzeile %d, Spalten: %s" % (
        kopf_idx + 1, ", ".join(f"{k}='{kopf[v]}'" for k, v in spalten.items())))

    werte = []
    for nr, z in enumerate(zeilen[kopf_idx + 1:], start=kopf_idx + 2):
        def zelle(k):
            i = spalten.get(k)
            return z[i] if i is not None and i < len(z) else None
        st = station_km(zelle("km"), e.km_einheit_m)
        h = zahl(zelle("hoehe"))
        name = zelle("name")
        if st is None or h is None or not name:
            if any(v not in (None, "") for v in z):
                log(f"  Zeile {nr} übersprungen (unvollständig): {z}")
            continue
        typ, seite_n = name_zerlegen(name)
        seite = seite_aus_text(zelle("seite")) or seite_n
        werte.append(Messwert(st, str(zelle("km")), str(name), typ, seite, h, nr))
    log(f"Tabelle: {len(werte)} Höhenwerte gelesen "
        f"({', '.join(sorted({w.typ + (' ' + w.seite if w.seite else '') for w in werte}))})")
    if not werte:
        raise ValueError("Keine gültigen Höhenwerte in der Tabelle gefunden.")
    return werte


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------
class Polylinie:
    """2D-Linie als numpy-Array mit kumulierter Länge."""

    def __init__(self, coords, typ="", seite="", fid=-1, typ_original=""):
        c = np.asarray(coords, dtype=float)[:, :2]
        keep = np.r_[True, np.any(np.abs(np.diff(c, axis=0)) > 1e-9, axis=1)]
        self.c = c[keep]
        seg = np.hypot(*np.diff(self.c, axis=0).T)
        self.cum = np.r_[0.0, np.cumsum(seg)]
        self.laenge = float(self.cum[-1])
        self.typ, self.seite, self.fid, self.typ_original = typ, seite, fid, typ_original
        self.geom = ogr.Geometry(ogr.wkbLineString)
        for x, y in self.c:
            self.geom.AddPoint_2D(float(x), float(y))

    def punkt(self, m):
        m = np.clip(m, 0, self.laenge)
        return np.array([np.interp(m, self.cum, self.c[:, 0]), np.interp(m, self.cum, self.c[:, 1])])

    def richtung(self, m, fenster):
        a, b = self.punkt(m - fenster / 2), self.punkt(m + fenster / 2)
        d = b - a
        n = np.hypot(*d)
        if n < 1e-9:
            i = min(max(np.searchsorted(self.cum, m) - 1, 0), len(self.c) - 2)
            d = self.c[i + 1] - self.c[i]
            n = np.hypot(*d)
        return d / n

    def projektion(self, p):
        """Liefert (Station m, Abstand) des Lotfußpunktes von p."""
        a, b = self.c[:-1], self.c[1:]
        ab = b - a
        l2 = np.maximum((ab ** 2).sum(1), 1e-12)
        t = np.clip(((p - a) * ab).sum(1) / l2, 0, 1)
        fp = a + ab * t[:, None]
        d = np.hypot(*(fp - p).T)
        i = int(np.argmin(d))
        return float(self.cum[i] + t[i] * math.sqrt(l2[i])), float(d[i])

    def stationen_versatz(self, pts):
        """Für viele Punkte: Station entlang der Linie und Versatz (+ links / - rechts
        der Digitalisierrichtung)."""
        pts = np.asarray(pts, dtype=float)
        a, ab = self.c[:-1], np.diff(self.c, axis=0)
        l2 = np.maximum((ab ** 2).sum(1), 1e-12)
        m_out = np.empty(len(pts))
        d_out = np.empty(len(pts))
        for k in range(0, len(pts), 2000):
            p = pts[k:k + 2000, None, :]
            t = np.clip(((p - a) * ab).sum(2) / l2, 0, 1)
            fp = a + ab * t[..., None]
            dist = np.hypot(*(p - fp).transpose(2, 0, 1))
            i = np.argmin(dist, axis=1)
            r = np.arange(len(i))
            rel = p[r, 0] - fp[r, i]
            kreuz = ab[i, 0] * rel[:, 1] - ab[i, 1] * rel[:, 0]
            m_out[k:k + 2000] = self.cum[i] + t[r, i] * np.sqrt(l2[i])
            d_out[k:k + 2000] = np.where(kreuz >= 0, 1.0, -1.0) * dist[r, i]
        return m_out, d_out


def _vektor_oeffnen(pfad: str):
    """Öffnet 'datei.gpkg|layername=x' oder 'datei.shp'."""
    teile = pfad.split("|")
    ds = ogr.Open(teile[0])
    if ds is None:
        raise IOError(f"Kann Vektordatei nicht öffnen: {teile[0]}")
    lyr = ds.GetLayer(0)
    for t in teile[1:]:
        if t.lower().startswith("layername="):
            lyr = ds.GetLayerByName(t.split("=", 1)[1])
        elif t.lower().startswith("layerid="):
            lyr = ds.GetLayer(int(t.split("=", 1)[1]))
    return ds, lyr


def _linien_aus_geom(g):
    g = g.Clone()
    g.FlattenTo2D()
    name = g.GetGeometryName()
    if name == "LINESTRING":
        yield g.GetPoints()
    elif name in ("MULTILINESTRING", "GEOMETRYCOLLECTION"):
        for i in range(g.GetGeometryCount()):
            yield from _linien_aus_geom(g.GetGeometryRef(i))


def linien_lesen(e: Einstellungen, log: Logger):
    ds, lyr = _vektor_oeffnen(e.linien)
    srs = lyr.GetSpatialRef()
    defn = lyr.GetLayerDefn()
    felder = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    typ_feld = next((f for f in felder if f.lower() == e.linien_typ_feld.lower()), None)
    if typ_feld is None:
        raise ValueError(f"Typ-Feld '{e.linien_typ_feld}' nicht im Linien-Layer. Felder: {felder}")
    seite_feld = next((f for f in felder if e.linien_seite_feld and f.lower() == e.linien_seite_feld.lower()), None)

    linien, achs_teile = [], []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        typ_o = f.GetField(typ_feld)
        typ = norm(typ_o)
        seite = seite_aus_text(f.GetField(seite_feld)) if seite_feld else ""
        for pts in _linien_aus_geom(g):
            if len(pts) < 2:
                continue
            if not e.achse_datei and typ == norm(e.achse_wert):
                achs_teile.append(pts)
            else:
                linien.append(Polylinie(pts, typ, seite, f.GetFID(), str(typ_o)))

    if e.achse_datei:
        ds2, lyr2 = _vektor_oeffnen(e.achse_datei)
        for f in lyr2:
            if f.GetGeometryRef() is not None:
                achs_teile.extend(p for p in _linien_aus_geom(f.GetGeometryRef()) if len(p) >= 2)
    if not achs_teile:
        raise ValueError(f"Keine Flussachse gefunden (Typ = '{e.achse_wert}' bzw. Achs-Datei).")
    achse = Polylinie(_teile_verketten(achs_teile), "achse")
    log(f"Linien: Achse {achse.laenge:.1f} m, {len(linien)} weitere Linien "
        f"({', '.join(sorted({l.typ_original for l in linien}))})")
    return achse, linien, (srs.Clone() if srs else None)


def _teile_verketten(teile, tol=0.05):
    if len(teile) == 1:
        return teile[0]
    rest = [np.asarray(t)[:, :2] for t in teile]
    kette = rest.pop(0)
    while rest:
        for i, t in enumerate(rest):
            if np.hypot(*(t[0] - kette[-1])) < tol:
                kette = np.vstack([kette, t[1:]])
            elif np.hypot(*(t[-1] - kette[-1])) < tol:
                kette = np.vstack([kette, t[::-1][1:]])
            elif np.hypot(*(t[-1] - kette[0])) < tol:
                kette = np.vstack([t[:-1], kette])
            elif np.hypot(*(t[0] - kette[0])) < tol:
                kette = np.vstack([t[::-1][:-1], kette])
            else:
                continue
            rest.pop(i)
            break
        else:
            raise ValueError("Die Flussachse besteht aus mehreren nicht zusammenhängenden Teilen. "
                             "Bitte zu einer durchgehenden Linie zusammenführen.")
    return kette


# --------------------------------------------------------------------------
# Kilometrierung -> Achsstation
# --------------------------------------------------------------------------
def km_abbildung(e: Einstellungen, achse: Polylinie, log: Logger) -> Callable:
    """Liefert Funktion: Tabellen-Station (m) -> Länge entlang der Achse (m)."""
    if e.km_punkte:
        ds, lyr = _vektor_oeffnen(e.km_punkte)
        paare = []
        for f in lyr:
            g = f.GetGeometryRef()
            km = station_km(f.GetField(e.km_punkte_feld), e.km_einheit_m)
            if g is None or km is None:
                continue
            m, d = achse.projektion(np.array([g.GetX(), g.GetY()]))
            paare.append((km, m))
        paare.sort()
        if len(paare) < 2:
            raise ValueError("Mindestens 2 km-Punkte für die Kalibrierung nötig.")
        s = np.array([p[0] for p in paare])
        m = np.array([p[1] for p in paare])
        log(f"Kilometrierung: kalibriert über {len(paare)} km-Punkte "
            f"(km {s[0]/1000:.3f} – {s[-1]/1000:.3f})")

        def f(st):
            # linear, an den Rändern linear extrapoliert
            if st <= s[0]:
                k = (m[1] - m[0]) / (s[1] - s[0])
                return m[0] + (st - s[0]) * k
            if st >= s[-1]:
                k = (m[-1] - m[-2]) / (s[-1] - s[-2])
                return m[-1] + (st - s[-1]) * k
            return float(np.interp(st, s, m))
        return f

    if e.km_start is None:
        raise ValueError("km_start (km am Anfangspunkt der Achse) oder ein km-Punkte-Layer muss "
                         "angegeben werden.")
    start = e.km_start * 1000.0
    vz = 1.0 if e.km_steigend else -1.0
    log(f"Kilometrierung: Achsanfang = km {e.km_start:.3f}, km "
        f"{'steigend' if e.km_steigend else 'fallend'} in Digitalisierrichtung "
        f"(Achse deckt km {e.km_start:.3f} – {(start + vz * achse.laenge)/1000:.3f})")
    return lambda st: (st - start) * vz


# --------------------------------------------------------------------------
# Punkte setzen
# --------------------------------------------------------------------------
@dataclass
class Punkt:
    x: float
    y: float
    hoehe: float
    km: float
    name: str
    seite: str
    linie: int      # Index in Linienliste
    quelle: str     # "tabelle" / "verdichtet"
    m_linie: float = 0.0


def _schnittpunkte(g_linie, g_lot):
    s = g_linie.Intersection(g_lot)
    if s is None or s.IsEmpty():
        return []
    pts = []

    def sammeln(g):
        n = g.GetGeometryName()
        if n == "POINT":
            pts.append((g.GetX(), g.GetY()))
        else:
            for i in range(g.GetGeometryCount()):
                sammeln(g.GetGeometryRef(i))
    sammeln(s)
    return pts


def punkte_setzen(e: Einstellungen, werte, achse: Polylinie, linien, km2m, log: Logger):
    punkte = []
    fehlend = 0
    tol = 1.0
    fluss_vz = 1.0 if (e.km_steigend == e.fliessrichtung_km_steigend) else -1.0
    for w in werte:
        kandidaten = [i for i, l in enumerate(linien) if typ_passt(w.typ, l.typ)]
        if w.seite:
            kandidaten = [i for i in kandidaten if not linien[i].seite or linien[i].seite == w.seite]
        if not kandidaten:
            log(f"  Zeile {w.zeile}: keine Linie für '{w.name}' gefunden")
            fehlend += 1
            continue
        m = km2m(w.station_m)
        if m < -tol or m > achse.laenge + tol:
            log(f"  Zeile {w.zeile}: km {w.station_m/1000:.3f} liegt außerhalb der Achse")
            fehlend += 1
            continue
        gesetzt = 0
        if e.methode == "entlang":
            for i in kandidaten:
                l = linien[i]
                if -tol <= m <= l.laenge + tol:
                    x, y = l.punkt(m)
                    punkte.append(Punkt(x, y, w.hoehe, w.station_m, w.name, w.seite, i, "tabelle"))
                    gesetzt += 1
        else:
            p = achse.punkt(m)
            t = achse.richtung(m, e.tangenten_fenster)
            n_links = np.array([-t[1], t[0]])          # links in Digitalisierrichtung
            n_fluss = n_links * fluss_vz              # links in Fließrichtung
            a, b = p - n_links * e.suchweite, p + n_links * e.suchweite
            lot = ogr.Geometry(ogr.wkbLineString)
            lot.AddPoint_2D(*map(float, a))
            lot.AddPoint_2D(*map(float, b))
            for i in kandidaten:
                l = linien[i]
                sp = _schnittpunkte(l.geom, lot)
                if w.seite and not l.seite:
                    sig = 1.0 if w.seite == "links" else -1.0
                    sp = [q for q in sp if np.dot(np.array(q) - p, n_fluss) * sig > 0]
                if not sp:
                    continue
                q = min(sp, key=lambda q: math.hypot(q[0] - p[0], q[1] - p[1]))
                punkte.append(Punkt(q[0], q[1], w.hoehe, w.station_m, w.name, w.seite, i, "tabelle"))
                gesetzt += 1
        if not gesetzt:
            log(f"  Zeile {w.zeile}: '{w.name}' km {w.station_m/1000:.3f} – keine passende Linie "
                f"geschnitten")
            fehlend += 1
    log(f"Punkte: {len(punkte)} Punkte aus {len(werte)} Tabellenwerten gesetzt"
        + (f", {fehlend} Werte ohne Lage (siehe oben)" if fehlend else ""))
    return punkte


def verdichten(punkte, linien, abstand: float, log: Logger):
    """Interpoliert entlang jeder Linie zwischen den gesetzten Punkten (Bruchkanten)."""
    if abstand <= 0:
        return []
    neu = []
    for i, l in enumerate(linien):
        eig = [p for p in punkte if p.linie == i]
        if len(eig) < 2:
            continue
        ms = {}
        for p in eig:
            m, _ = l.projektion(np.array([p.x, p.y]))
            ms.setdefault(round(m, 3), []).append(p.hoehe)
        m_s = np.array(sorted(ms))
        z_s = np.array([np.mean(ms[k]) for k in sorted(ms)])
        if len(m_s) < 2:
            continue
        stuetz = l.cum[(l.cum > m_s[0]) & (l.cum < m_s[-1])]
        raster = np.arange(m_s[0], m_s[-1], abstand)[1:]
        alle = np.unique(np.r_[stuetz, raster])
        # nicht doppelt zu vorhandenen Punkten
        alle = alle[np.min(np.abs(alle[:, None] - m_s[None, :]), axis=1) > 0.01]
        z = np.interp(alle, m_s, z_s)
        for m, h in zip(alle, z):
            x, y = l.punkt(m)
            neu.append(Punkt(x, y, float(h), float("nan"), l.typ_original, l.seite, i, "verdichtet", m))
    log(f"Verdichtung: {len(neu)} Zusatzpunkte entlang der Linien (Abstand {abstand:g} m)")
    return neu


def korridor_polygon(punkte, achse: Polylinie, klasse: float, puffer: float):
    """Polygon zwischen den jeweils äußersten Punkten links und rechts der Achse.

    Verhindert, dass die Delaunay-Triangulation (konvexe Hülle) Flächen außerhalb
    des Deichkorridors – z.B. die Innenseite von Flussbögen – überbrückt.
    """
    xy = np.array([[p.x, p.y] for p in punkte])
    m, d = achse.stationen_versatz(xy)
    klasse = max(klasse, 1e-3)
    k = np.floor(m / klasse).astype(int)
    links, rechts = [], []
    for kk in np.unique(k):
        sel = np.where(k == kk)[0]
        il = sel[np.argmax(d[sel])]
        ir = sel[np.argmin(d[sel])]
        links.append((m[il], xy[il]))
        rechts.append((m[ir], xy[ir]))
    links.sort(key=lambda t: t[0])
    rechts.sort(key=lambda t: t[0])
    ring = ogr.Geometry(ogr.wkbLinearRing)
    for _, q in links + rechts[::-1]:
        ring.AddPoint_2D(float(q[0]), float(q[1]))
    ring.AddPoint_2D(float(links[0][1][0]), float(links[0][1][1]))
    poly = ogr.Geometry(ogr.wkbPolygon)
    poly.AddGeometry(ring)
    if not poly.IsValid():
        poly = poly.MakeValid() if hasattr(poly, "MakeValid") else poly.Buffer(0)
    return poly.Buffer(puffer)


def punkte_schreiben(pfad, punkte, srs):
    if os.path.exists(pfad):
        ogr.GetDriverByName("GPKG").DeleteDataSource(pfad)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(pfad)
    lyr = ds.CreateLayer("punkte_1945", srs, ogr.wkbPoint25D)
    for name, typ in (("km", ogr.OFTReal), ("name", ogr.OFTString), ("seite", ogr.OFTString),
                      ("hoehe", ogr.OFTReal), ("quelle", ogr.OFTString)):
        lyr.CreateField(ogr.FieldDefn(name, typ))
    lyr.StartTransaction()
    for p in punkte:
        f = ogr.Feature(lyr.GetLayerDefn())
        if not math.isnan(p.km):
            f.SetField("km", round(p.km / 1000.0, 4))
        f.SetField("name", p.name)
        f.SetField("seite", p.seite)
        f.SetField("hoehe", float(p.hoehe))
        f.SetField("quelle", p.quelle)
        g = ogr.Geometry(ogr.wkbPoint25D)
        g.AddPoint(float(p.x), float(p.y), float(p.hoehe))
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    lyr.CommitTransaction()
    ds = None


def _polygon_schreiben(pfad, poly, srs):
    drv = ogr.GetDriverByName("GPKG")
    if os.path.exists(pfad):
        drv.DeleteDataSource(pfad)
    ds = drv.CreateDataSource(pfad)
    lyr = ds.CreateLayer("korridor", srs, ogr.wkbMultiPolygon)
    f = ogr.Feature(lyr.GetLayerDefn())
    f.SetGeometry(ogr.ForceToMultiPolygon(poly))
    lyr.CreateFeature(f)
    ds = None


# --------------------------------------------------------------------------
# Raster
# --------------------------------------------------------------------------
def _dgm1_quellen(liste):
    dateien = []
    for p in liste:
        p = p.strip()
        if not p:
            continue
        if os.path.isdir(p):
            for wurzel, _, fs in os.walk(p):
                for f in sorted(fs):
                    if f.lower().endswith((".tif", ".tiff", ".xyz", ".asc", ".vrt", ".img")):
                        dateien.append(os.path.join(wurzel, f))
        else:
            dateien.append(p)
    return dateien


def raster_schreiben(pfad, arr, gt, srs_wkt, beschreibung=""):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(pfad, arr.shape[1], arr.shape[0], 1, gdal.GDT_Float32,
                    ["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(srs_wkt)
    b = ds.GetRasterBand(1)
    b.SetNoDataValue(NODATA)
    b.WriteArray(np.where(np.isfinite(arr), arr, NODATA).astype(np.float32))
    if beschreibung:
        b.SetDescription(beschreibung)
    b.ComputeStatistics(False)
    ds = None


def _stat(arr):
    v = arr[np.isfinite(arr)]
    if v.size == 0:
        return "keine Werte"
    return f"min {v.min():.2f}  max {v.max():.2f}  mittel {v.mean():.2f}"


def ausfuehren(e: Einstellungen, log: Logger = print, fortschritt: Callable[[float], None] = None) -> dict:
    """Komplette Prozesskette. Gibt Dict mit den Pfaden der Ergebnisse zurück."""
    fs = fortschritt or (lambda x: None)
    os.makedirs(e.ausgabe_ordner, exist_ok=True)
    protokoll = []

    def lg(msg):
        protokoll.append(msg)
        log(msg)

    lg("=== DGM 1945 Rekonstruktion ===")
    werte = tabelle_lesen(e, lg)
    fs(5)
    achse, linien, srs = linien_lesen(e, lg)
    if e.epsg:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(e.epsg))
    if srs is None:
        raise ValueError("Linien-Layer hat kein Koordinatensystem – bitte EPSG angeben.")
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs_wkt = srs.ExportToWkt()
    km2m = km_abbildung(e, achse, lg)
    fs(10)

    punkte = punkte_setzen(e, werte, achse, linien, km2m, lg)
    if len(punkte) < 3:
        raise ValueError("Zu wenige Punkte für eine Triangulation (< 3).")
    punkte_alle = punkte + verdichten(punkte, linien, e.verdichtung, lg)
    pfad_pkt = os.path.join(e.ausgabe_ordner, "Punkte_1945.gpkg")
    punkte_schreiben(pfad_pkt, punkte_alle, srs)
    lg(f"-> {pfad_pkt}")
    fs(25)

    # ---- Rastergitter festlegen (am DGM1 ausgerichtet) ----
    xs = np.array([p.x for p in punkte_alle])
    ys = np.array([p.y for p in punkte_alle])
    dgm_dateien = _dgm1_quellen(e.dgm1)
    dgm_vrt = None
    if dgm_dateien:
        vrt_pfad = os.path.join(e.ausgabe_ordner, "DGM1_mosaik.vrt")
        dgm_vrt = gdal.BuildVRT(vrt_pfad, dgm_dateien)
        gt0 = dgm_vrt.GetGeoTransform()
        px, py = gt0[1], abs(gt0[5])
        ox, oy = gt0[0], gt0[3]
        dgm_vrt = None
        lg(f"DGM1: {len(dgm_dateien)} Datei(en), Pixel {px:g} m")
    else:
        px = py = float(e.pixelgroesse)
        ox = oy = 0.0
    xmin = ox + math.floor((xs.min() - ox) / px) * px
    xmax = ox + math.ceil((xs.max() - ox) / px) * px
    ymax = oy - math.floor((oy - ys.max()) / py) * py
    ymin = oy - math.ceil((oy - ys.min()) / py) * py
    breite = int(round((xmax - xmin) / px))
    hoehe = int(round((ymax - ymin) / py))
    gt = (xmin, px, 0.0, ymax, 0.0, -py)
    lg(f"Rastergitter: {breite} x {hoehe} Pixel à {px:g} m")

    # ---- Triangulation (Delaunay, linear) ----
    tmp_tin = os.path.join(e.ausgabe_ordner, "_tin_tmp.tif")
    gdal.Grid(tmp_tin, pfad_pkt, format="GTiff", outputType=gdal.GDT_Float32,
              outputBounds=[xmin, ymax, xmax, ymin], width=breite, height=hoehe,
              algorithm=f"linear:radius=0:nodata={NODATA}", zfield="hoehe",
              layers=["punkte_1945"], outputSRS=srs_wkt)
    ds = gdal.Open(tmp_tin)
    gok = ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
    ds = None
    gdal.GetDriverByName("GTiff").Delete(tmp_tin)
    gok[gok == NODATA] = np.nan

    if e.maske or e.auto_maske:
        mem = gdal.GetDriverByName("MEM").Create("", breite, hoehe, 1, gdal.GDT_Byte)
        mem.SetGeoTransform(gt)
        mem.SetProjection(srs_wkt)
        if e.maske:
            mds, mlyr = _vektor_oeffnen(e.maske)
            lg("Maske: vorgegebenes Polygon")
        else:
            poly = korridor_polygon(punkte_alle, achse, e.masken_klasse, 0.5 * px)
            mds = gdal.GetDriverByName("Memory").Create("", 0, 0, 0, gdal.GDT_Unknown)
            mlyr = mds.CreateLayer("korridor", srs, ogr.wkbPolygon)
            f = ogr.Feature(mlyr.GetLayerDefn())
            f.SetGeometry(poly)
            mlyr.CreateFeature(f)
            _polygon_schreiben(os.path.join(e.ausgabe_ordner, "Korridor_Maske.gpkg"), poly, srs)
            lg("Maske: automatischer Korridor zwischen äußersten Linien -> Korridor_Maske.gpkg")
        gdal.RasterizeLayer(mem, [1], mlyr, burn_values=[1])
        gok[mem.GetRasterBand(1).ReadAsArray() == 0] = np.nan
    fs(60)

    erg = {"punkte": pfad_pkt}
    p_gok = os.path.join(e.ausgabe_ordner, "GOK45.tif")
    raster_schreiben(p_gok, gok, gt, srs_wkt, "GOK 1945 [m ue. NHN]")
    erg["gok45"] = p_gok
    lg(f"-> {p_gok}   ({_stat(gok)})")

    bombe = gok - float(e.eindringtiefe)
    p_bombe = os.path.join(e.ausgabe_ordner, f"GOK45_minus_{e.eindringtiefe:g}m_Eindringtiefe.tif")
    raster_schreiben(p_bombe, bombe, gt, srs_wkt,
                     f"max. Bomben-Eindringtiefe (GOK45 - {e.eindringtiefe:g} m) [m ue. NHN]")
    erg["eindringtiefe"] = p_bombe
    lg(f"-> {p_bombe}   ({_stat(bombe)})")
    fs(70)

    if dgm_dateien:
        src = gdal.Open(os.path.join(e.ausgabe_ordner, "DGM1_mosaik.vrt"))
        src_srs = src.GetProjection() or srs_wkt
        dgm_ds = gdal.Warp("", src, format="MEM", outputBounds=(xmin, ymin, xmax, ymax),
                           xRes=px, yRes=py, srcSRS=src_srs, dstSRS=srs_wkt,
                           resampleAlg="bilinear", dstNodata=NODATA, outputType=gdal.GDT_Float32)
        src = None
        dgm = dgm_ds.GetRasterBand(1).ReadAsArray().astype(np.float64)
        dgm[dgm == NODATA] = np.nan
        dgm_ds = None
        fs(85)

        diff = dgm - gok
        p_diff = os.path.join(e.ausgabe_ordner, "Differenz_DGM1_minus_GOK45.tif")
        raster_schreiben(p_diff, diff, gt, srs_wkt,
                         "DGM1 - GOK45 [m] (+ Auftrag / - Abtrag seit 1945)")
        erg["differenz"] = p_diff
        lg(f"-> {p_diff}   ({_stat(diff)})")
        v = diff[np.isfinite(diff)]
        if v.size:
            fl = px * py
            lg(f"   Auftrag seit 1945: {v[v > 0].sum() * fl:,.0f} m³   "
               f"Abtrag: {abs(v[v < 0].sum()) * fl:,.0f} m³   Fläche: {v.size * fl:,.0f} m²")

        sonde = dgm - bombe
        p_sonde = os.path.join(e.ausgabe_ordner, "Sondiertiefe_unter_DGM1.tif")
        raster_schreiben(p_sonde, sonde, gt, srs_wkt,
                         "Tiefe der max. Bomben-Eindringtiefe unter heutiger GOK [m]")
        erg["sondiertiefe"] = p_sonde
        lg(f"-> {p_sonde}   ({_stat(sonde)})")
    else:
        lg("Kein DGM1 angegeben – Differenzraster wird nicht berechnet.")

    fs(100)
    lg("=== fertig ===")
    with open(os.path.join(e.ausgabe_ordner, "Protokoll.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(protokoll) + "\n")
    return erg


# --------------------------------------------------------------------------
# Kommandozeile (INI-Datei)
# --------------------------------------------------------------------------
def _bool(s):
    return str(s).strip().lower() in ("1", "true", "ja", "yes", "j", "y", "wahr")


def einstellungen_aus_ini(pfad: str) -> Einstellungen:
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.read(pfad, encoding="utf-8")
    basis = os.path.dirname(os.path.abspath(pfad))
    werte = {}
    for sek in cp.sections():
        werte.update({k: v for k, v in cp[sek].items() if v.strip() != ""})

    def pfad_abs(p):
        return p if os.path.isabs(p.split("|")[0]) else os.path.join(basis, p)

    felder = Einstellungen.__dataclass_fields__
    kw = {}
    for k, v in werte.items():
        if k not in felder:
            raise ValueError(f"Unbekannte Einstellung '{k}' in {pfad}")
        typ = felder[k].type
        if k in ("excel", "linien", "ausgabe_ordner", "achse_datei", "km_punkte", "maske"):
            kw[k] = pfad_abs(v)
        elif k == "dgm1":
            kw[k] = [pfad_abs(x.strip()) for x in re.split(r"[;\n]", v) if x.strip()]
        elif "bool" in str(typ):
            kw[k] = _bool(v)
        elif "float" in str(typ):
            kw[k] = float(v.replace(",", "."))
        elif "int" in str(typ):
            kw[k] = int(v)
        else:
            kw[k] = v
    return Einstellungen(**kw)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        print("Aufruf: python dgm1945_kern.py konfiguration.ini [weitere.ini ...]")
        return 1
    for ini in argv:
        e = einstellungen_aus_ini(ini)
        ausfuehren(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
