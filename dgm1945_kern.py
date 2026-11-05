# -*- coding: utf-8 -*-
"""
DGM 1945 – Rekonstruktion der Geländeoberkante 1945 (GOK45)
===========================================================

Automatisierte Prozesskette:

  1. Daten einlesen: Höhenpunkte 1945 (Punkt-Shape mit Höhe, z.B. HOEHE_FG –
     oder Tabelle Kilometrierung/Seite/Abstand/Höhe + Achse), Geländelinien,
     aktuelles DGM1 (fertiges Mosaik oder Einzelkacheln -> Mosaik wird gebaut),
     optional Untersuchungsgebiet und Sohldaten.
  2. Optional: Gewässersohle in das aktuelle DGM1 einbrennen (das DGM1 zeigt
     nur die Wasseroberfläche).
  3. Höhen entlang der Geländelinien zwischen den Punkten interpolieren
     (Linien mit Höheninfo "DGM"/"Sohle": Höhen aus dem DGM1).
  4. Triangulation (Delaunay-TIN, linear); Dreiecke mit Kanten > max_kante
     und Flächen ohne Daten werden mit dem DGM1 gefüllt -> GOK45.tif
  5. Differenz DGM1 - GOK45 -> Veränderung seit 1945
  6. GOK45 - 8 m -> maximale Bomben-Eindringtiefe (m ü. NHN)
     und Sondiertiefe unter heutiger GOK (DGM1 - (GOK45 - 8 m)).

Benötigt nur GDAL/OGR (Python-Bindings) und numpy – beides ist in jeder
QGIS-Installation (OSGeo4W) enthalten. openpyxl wird genutzt, falls
vorhanden (flexiblere Kopfzeilen-Erkennung), sonst liest GDAL die Excel-Datei.

Nutzung ohne QGIS-Oberfläche:
    python dgm1945_kern.py                    (Assistent fragt alle Daten ab)
    python dgm1945_kern.py konfiguration.ini  (Einstellungen aus Datei)
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
    ausgabe_ordner: str = ""

    # --- Variante A: bereits verortete Höhenpunkte + Geländelinien ---
    punkte: str = ""                # Punkt-Layer mit Höhen 1945
    punkte_hoehe_feld: str = ""     # leer = automatisch (HOEHE_FG, POINT_Z, HOEHE_BS, HOEHE ... / Z)
    gelaendelinien: str = ""        # Linien, entlang derer zwischen den Punkten interpoliert wird
    linien_hoehe_feld: str = ""     # Feld mit Höheninfo (leer = automatisch "Hoe_Inf")
    linien_dgm_werte: str = "dgm,sohle"  # Werte im Höheninfo-Feld, bei denen das DGM1 die Höhe liefert
    fang_toleranz: float = 0.1      # Punkt gilt als "auf der Linie" bis zu diesem Abstand [m]
    linien_abstand: float = 1.0     # Punktabstand entlang der Geländelinien [m]

    # --- Variante B: Tabelle (Kilometrierung/Seite/Abstand/Höhe) + Achse ---
    excel: str = ""                 # Excel (.xlsx/.xls) oder CSV mit Höhen 1945
    linien: str = ""                # Achse (ggf. mit Sohle/Berme/Deich-Linien)

    # Linien-Layer (bei Methode "abstand" genügt die Achse allein)
    linien_typ_feld: str = "typ"    # Attributfeld mit Achse/Sohle/Berme/Deich (fehlt es: alles = Achse)
    linien_seite_feld: str = ""     # optional: Feld mit links/rechts
    achse_wert: str = "Achse"       # Wert im Typ-Feld für die Flussachse
    achse_datei: str = ""           # optional: Achse als eigener Layer

    # Kilometrierung
    km_start: Optional[float] = None    # km am Anfang (1. Stützpunkt) der Achse; leer = aus DBF
    achse_km_feld: str = ""             # DBF-Feld mit Start-km/-m der Achse (leer = automatisch)
    km_steigend: Optional[bool] = None  # km nimmt in Digitalisierrichtung zu (None = automatisch)
    km_punkte: str = ""                 # optional: Punkt-Layer mit km-Steinen (Kalibrierung)
    km_punkte_feld: str = "km"
    km_einheit_m: float = 0.0           # Meter je Einheit der Tabellen-Station (km: 1000, m: 1, 0 = auto)
    fliessrichtung_km_steigend: bool = False  # links/rechts in Blickrichtung steigender km (False: fallender km)

    # Tabelle
    excel_blatt: str = ""
    spalte_km: str = ""
    spalte_name: str = ""
    spalte_hoehe: str = ""
    spalte_seite: str = ""
    spalte_abstand: str = ""
    spalte_jahr: str = ""
    bergsenkung_spalte: str = ""    # optional: Spalte mit Bergsenkung, wird von der Höhe abgezogen
    bergsenkung_faktor: float = 0.01  # Umrechnung der Bergsenkungsspalte in m (cm -> 0.01)
    bezugsjahr: int = 1945          # mehrere Werte am selben Ort: Jahr am nächsten daran gewinnt
    duplikate: str = "jahr"         # "jahr" | "mittel" | "alle"

    # Punktsetzung
    methode: str = "auto"           # "abstand" (Station+Seite+Abstand), "lot" (Schnitt mit Linien),
                                    # "entlang" oder "auto" (abstand, wenn Spalte ABSTAND vorhanden)
    suchweite: float = 1000.0       # halbe Länge der Profil-Lote in m
    tangenten_fenster: float = 20.0 # Glättung der Achsrichtung in m
    verdichtung: float = 5.0        # Zwischenprofile bzw. Verdichtung entlang der Linien alle x m (0 = aus)

    # Raster
    dgm1: list = field(default_factory=list)   # DGM1: Mosaik oder Einzelkacheln (GeoTIFF, XYZ, ASC ...) / Ordner
    dgm1_mosaik_speichern: bool = True  # aus Einzelkacheln gebautes Mosaik als DGM1_Mosaik.tif ablegen
    untersuchungsgebiet: str = ""   # optional: Polygon – Ausdehnung der Ergebnisse
    max_kante: float = 50.0         # Dreiecke mit längerer Kante werden verworfen (dort DGM1); 0 = aus

    # Sohle ins aktuelle DGM1 einbrennen (optional)
    sohle: str = ""                 # Linien (oder Punkte) mit Sohlhöhen
    sohle_hoehe_feld: str = ""      # leer = Z-Werte der Geometrie bzw. automatisch
    sohle_puffer: float = 0.0       # halbe Sohlbreite [m] um die Sohllinie
    gewaesser: str = ""             # optional: Polygon der Wasserfläche (Böschung zur Sohle interpolieren)
    sohle_nur_tiefer: bool = True   # nur einbrennen, wo die Sohle tiefer als das DGM1 liegt
    pixelgroesse: float = 1.0       # nur falls kein DGM1 angegeben
    maske: str = ""                 # optional: Polygon zum Zuschneiden des TIN
    auto_maske: bool = True         # (Tabelle+Achse) TIN auf Korridor der Profile begrenzen
    masken_klasse: float = 10.0     # Stationsabstand (m) für die automatische Korridormaske
    dgm1_auffuellen: bool = True    # Flächen ohne Tabellenwerte mit DGM1 füllen
    auffuell_puffer: float = 20.0   # Füllbereich = Achse gepuffert um (max. Abstand + x m)
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
    "km": ["km", "station", "stat", "kilometrie", "kilometr", "fluss km", "stationierung"],
    "hoehe": ["hoehe", "hohe", "nhn", "nn", "z", "kote", "hoehen"],
    "seite": ["seite", "ufer", "flussachse", "lage zur achse"],
    "abstand": ["abstand", "offset", "entfernung", "achsabstand", "dist"],
    "jahr": ["jahr", "planjahr", "aufnahmejahr"],
    "name": ["name", "bezeichnung", "typ", "art", "objekt", "element", "schluessel"],
}
_REIHENFOLGE = ("km", "seite", "abstand", "hoehe", "jahr", "name")


def _passt_kw(kopf: str, kws, genau: bool) -> bool:
    h = norm(kopf)
    if not h:
        return False
    for kw in kws:
        if h == kw:
            return True
        if not genau and (h.split()[0] == kw or (len(kw) >= 4 and kw in h)):
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
    # erst exakte Treffer (z.B. "HOEHE" vor "HOEHE_BS"), dann Teiltreffer
    for genau in (True, False):
        for key in _REIHENFOLGE:
            if key in res:
                continue
            for i, k in enumerate(kopf):
                if i not in benutzt and _passt_kw(k, _KW[key], genau):
                    res[key] = i
                    benutzt.add(i)
                    break
    return res


def _spalte_index(kopf: list, name: str):
    for i, k in enumerate(kopf):
        if norm(k) == norm(name):
            return i
    raise ValueError(f"Spalte '{name}' nicht in Tabelle gefunden. Vorhanden: {kopf}")


def _zeilen_lesen(pfad: str, blatt: str) -> list:
    ext = os.path.splitext(pfad)[1].lower()
    if ext in (".csv", ".txt"):
        roh = open(pfad, "rb").read()
        try:
            text = roh.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = roh.decode("cp1252", errors="replace")
        erste = text.splitlines()[0] if text else ""
        trenner = max(";,\t", key=erste.count)
        return [list(r) for r in csv.reader(text.splitlines(), delimiter=trenner)]
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
    abstand: Optional[float] = None
    jahr: Optional[float] = None


def tabelle_lesen(e: Einstellungen, log: Logger) -> list:
    zeilen = _zeilen_lesen(e.excel, e.excel_blatt)
    vorgabe = {"km": e.spalte_km, "name": e.spalte_name, "hoehe": e.spalte_hoehe,
               "seite": e.spalte_seite, "abstand": e.spalte_abstand, "jahr": e.spalte_jahr}
    kopf_idx, spalten = None, None
    for i, z in enumerate(zeilen[:40]):
        kopf = ["" if v is None else str(v) for v in z]
        try:
            sp = _spalten_finden(kopf, vorgabe)
        except ValueError:
            continue
        if {"km", "hoehe"} <= sp.keys() and ("name" in sp or "abstand" in sp):
            kopf_idx, spalten = i, sp
            break
    if kopf_idx is None:
        raise ValueError("Kopfzeile der Tabelle nicht erkannt (benötigt Spalten für km, Höhe und "
                         "Abstand oder Name). "
                         "Spaltennamen ggf. in den Einstellungen angeben. Erste Zeile: "
                         f"{zeilen[0] if zeilen else '-'}")
    kopf = ["" if v is None else str(v) for v in zeilen[kopf_idx]]
    if e.bergsenkung_spalte:
        spalten["bergsenkung"] = _spalte_index(kopf, e.bergsenkung_spalte)
    log("Tabelle: Kopfzeile %d, Spalten: %s" % (
        kopf_idx + 1, ", ".join(f"{k}='{kopf[v]}'" for k, v in spalten.items())))

    # Einheit der Stationierung: km (12.345) oder m (12345)
    einheit = e.km_einheit_m
    if not einheit:
        roh = [zahl(z[spalten["km"]]) for z in zeilen[kopf_idx + 1:]
               if spalten["km"] < len(z) and not (isinstance(z[spalten["km"]], str) and "+" in z[spalten["km"]])]
        roh = [abs(v) for v in roh if v is not None]
        einheit = 1.0 if roh and max(roh) >= 1000 else 1000.0
        log(f"Tabelle: Stationierung in {'Metern' if einheit == 1.0 else 'Kilometern'} erkannt")

    werte = []
    for nr, z in enumerate(zeilen[kopf_idx + 1:], start=kopf_idx + 2):
        def zelle(k):
            i = spalten.get(k)
            return z[i] if i is not None and i < len(z) else None
        st = station_km(zelle("km"), einheit)
        h = zahl(zelle("hoehe"))
        name = zelle("name") or ""
        ab = zahl(zelle("abstand")) if "abstand" in spalten else None
        if st is None or h is None or (not name and ab is None):
            if any(v not in (None, "") for v in z):
                log(f"  Zeile {nr} übersprungen (unvollständig)")
            continue
        if "bergsenkung" in spalten:
            bs = zahl(zelle("bergsenkung"))
            if bs is not None:
                h -= bs * e.bergsenkung_faktor
        typ, seite_n = name_zerlegen(name)
        seite = seite_aus_text(zelle("seite")) or seite_n
        werte.append(Messwert(st, str(zelle("km")), str(name), typ, seite, h, nr, ab,
                              zahl(zelle("jahr")) if "jahr" in spalten else None))
    arten = sorted({w.typ + (' ' + w.seite if w.seite else '') for w in werte if w.typ})
    log(f"Tabelle: {len(werte)} Höhenwerte gelesen, Station {min(w.station_m for w in werte):.0f} – "
        f"{max(w.station_m for w in werte):.0f} m" + (f" ({', '.join(arten)})" if arten else "")
        + (" – Bergsenkung abgezogen" if "bergsenkung" in spalten else ""))
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


def _attribute(f):
    return {f.GetFieldDefnRef(i).GetName(): f.GetField(i) for i in range(f.GetFieldCount())}


def linien_lesen(e: Einstellungen, methode: str, log: Logger):
    """Liest Achse (+ ggf. Sohle/Berme/Deich-Linien). Rückgabe: achse, linien, srs, achs_attribute."""
    ds, lyr = _vektor_oeffnen(e.linien)
    srs = lyr.GetSpatialRef()
    defn = lyr.GetLayerDefn()
    felder = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
    typ_feld = next((f for f in felder if f.lower() == e.linien_typ_feld.lower()), None)
    if typ_feld is None and methode != "abstand" and not e.achse_datei:
        raise ValueError(f"Typ-Feld '{e.linien_typ_feld}' nicht im Linien-Layer. Felder: {felder}")
    seite_feld = next((f for f in felder if e.linien_seite_feld and f.lower() == e.linien_seite_feld.lower()), None)

    linien, achs_teile, attr = [], [], {}
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        typ_o = f.GetField(typ_feld) if typ_feld else e.achse_wert
        typ = norm(typ_o)
        seite = seite_aus_text(f.GetField(seite_feld)) if seite_feld else ""
        ist_achse = not e.achse_datei and typ == norm(e.achse_wert)
        if ist_achse and not attr:
            attr = _attribute(f)
        for pts in _linien_aus_geom(g):
            if len(pts) < 2:
                continue
            if ist_achse:
                achs_teile.append(pts)
            else:
                linien.append(Polylinie(pts, typ, seite, f.GetFID(), str(typ_o)))

    if e.achse_datei:
        ds2, lyr2 = _vektor_oeffnen(e.achse_datei)
        srs = srs or lyr2.GetSpatialRef()
        for f in lyr2:
            if f.GetGeometryRef() is not None:
                attr = attr or _attribute(f)
                achs_teile.extend(p for p in _linien_aus_geom(f.GetGeometryRef()) if len(p) >= 2)
    if not achs_teile:
        raise ValueError(f"Keine Flussachse gefunden (Typ = '{e.achse_wert}' bzw. Achs-Datei).")
    achse = Polylinie(_teile_verketten(achs_teile), "achse")
    log(f"Linien: Achse {achse.laenge:.1f} m" + (
        f", {len(linien)} weitere Linien ({', '.join(sorted({l.typ_original for l in linien}))})"
        if linien else ""))
    return achse, linien, (srs.Clone() if srs else None), attr


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
_START_FELDER = ["km", "km start", "start km", "kmstart", "startkm", "start m", "startm", "start",
                 "km anfang", "anfang", "anfang km", "anfang m", "station", "stat von", "station von",
                 "von km", "km von", "von m", "von", "stat anf", "km a", "m start", "start station"]
_ENDE_FELDER = ["km ende", "ende km", "kmende", "endkm", "ende m", "end m", "endm", "ende", "end",
                "bis km", "km bis", "bis m", "bis", "stat bis", "station bis", "km e", "m ende"]


def _feld_suchen(attr: dict, kandidaten, vorgabe=""):
    if vorgabe:
        for k, v in attr.items():
            if norm(k) == norm(vorgabe):
                return k, zahl(v)
        raise ValueError(f"Feld '{vorgabe}' nicht in den Attributen der Achse: {list(attr)}")
    normiert = {norm(k): k for k in attr}
    for c in kandidaten:
        if c in normiert and zahl(attr[normiert[c]]) is not None:
            k = normiert[c]
            return k, zahl(attr[k])
    return None, None


def km_abbildung(e: Einstellungen, achse: Polylinie, attr: dict, werte, log: Logger):
    """Liefert (Funktion Tabellen-Station [m] -> Länge entlang der Achse [m], km_steigend)."""
    if e.km_punkte:
        ds, lyr = _vektor_oeffnen(e.km_punkte)
        paare = []
        for f in lyr:
            g = f.GetGeometryRef()
            km = station_km(f.GetField(e.km_punkte_feld), e.km_einheit_m or 1000.0)
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
        return f, bool(m[-1] >= m[0])

    st_min = min(w.station_m for w in werte)
    st_max = max(w.station_m for w in werte)
    L = achse.laenge
    tol = max(50.0, 0.02 * L)
    ende = None
    if e.km_start is not None:
        quelle = "Einstellung"
        starts = [e.km_start * 1000.0]
    else:
        feld, wert = _feld_suchen(attr, _START_FELDER, e.achse_km_feld)
        if feld is None:
            raise ValueError("Keine Kilometrierung gefunden: km_start angeben oder Feld mit Start-km/-m "
                             f"in der DBF der Achse (vorhandene Felder: {list(attr) or 'keine'}).")
        quelle = f"DBF-Feld '{feld}' = {wert:g}"
        # Feldwert kann in km oder m sein -> beide Deutungen prüfen
        starts = [wert, wert * 1000.0] if abs(wert) < 1000 else [wert]
        if norm(feld).endswith(" m") or (norm(feld).endswith("m") and "km" not in norm(feld)):
            starts = [wert]
        _, ende = _feld_suchen(attr, _ENDE_FELDER)
        if ende is not None and ende != wert and e.km_steigend is None:
            quelle += f", Ende = {ende:g}"
    richtungen = [e.km_steigend] if e.km_steigend is not None else [True, False]
    if e.km_steigend is None and ende is not None and ende != wert:
        richtungen = [ende > wert]

    def passt(start, steig):
        lo, hi = (start, start + L) if steig else (start - L, start)
        return lo - tol <= st_min and st_max <= hi + tol

    loesungen = [(a, r) for a in starts for r in richtungen if passt(a, r)]
    if not loesungen:
        loesungen = [(starts[-1], richtungen[0])]
        log(f"WARNUNG: Tabellen-Stationen {st_min:.0f} – {st_max:.0f} m liegen nicht auf der Achse "
            f"(Start {starts[-1]:.0f} m, Länge {L:.0f} m). Kilometrierung prüfen!")
    elif len({r for _, r in loesungen}) > 1:
        log("WARNUNG: Richtung der Kilometrierung nicht eindeutig – 'km_steigend' angeben. "
            "Verwendet: steigend.")
    start, steig = loesungen[0]
    vz = 1.0 if steig else -1.0
    log(f"Kilometrierung: Achsanfang = {start:.1f} m ({quelle}), km "
        f"{'steigend' if steig else 'fallend'} in Digitalisierrichtung "
        f"(Achse deckt {start:.0f} – {start + vz * L:.0f} m)")
    return (lambda st: (st - start) * vz), steig


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
    jahr: Optional[float] = None
    versatz: Optional[float] = None   # Abstand zur Achse, + links der Digitalisierrichtung


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


def punkte_abstand(e: Einstellungen, werte, achse: Polylinie, km2m, km_steigend, log: Logger):
    """Punkt = Achspunkt bei Station + Seite * Abstand senkrecht zur Achse."""
    punkte, fehlend, tol = [], 0, 1.0
    fluss_vz = 1.0 if (km_steigend == e.fliessrichtung_km_steigend) else -1.0
    for w in werte:
        m = km2m(w.station_m)
        if m < -tol or m > achse.laenge + tol:
            log(f"  Zeile {w.zeile}: Station {w.station_m:.1f} m liegt außerhalb der Achse")
            fehlend += 1
            continue
        ab = w.abstand or 0.0
        if ab != 0 and not w.seite:
            log(f"  Zeile {w.zeile}: Abstand {ab:g} m ohne Seite (links/rechts) – übersprungen")
            fehlend += 1
            continue
        p = achse.punkt(m)
        t = achse.richtung(m, e.tangenten_fenster)
        n_links = np.array([-t[1], t[0]]) * fluss_vz      # links in Bezugsrichtung
        vers = ab * (1.0 if w.seite == "links" else -1.0)
        q = p + n_links * vers
        punkte.append(Punkt(q[0], q[1], w.hoehe, w.station_m, w.name, w.seite, -1, "tabelle",
                            m_linie=m, jahr=w.jahr, versatz=vers * fluss_vz))
    log(f"Punkte: {len(punkte)} Punkte über Station/Seite/Abstand gesetzt"
        + (f", {fehlend} Werte ohne Lage (siehe oben)" if fehlend else ""))
    return punkte


def profile_interpolieren(punkte, achse: Polylinie, abstand: float, fenster: float, log: Logger):
    """Zwischenprofile alle `abstand` m entlang der Achse.

    Für jeden Achsabstand wird die Höhe linear zwischen dem nächsten Profil davor und
    dem nächsten Profil danach interpoliert, die diesen Abstand abdecken (Lücken in
    einzelnen Profilen werden so vom nächsten vollständigen Profil überbrückt). Die
    Punkte werden senkrecht zur Achse abgesetzt – das TIN folgt damit der Flusskrümmung,
    statt weit auseinanderliegende Profile geradlinig zu verbinden.
    """
    if abstand <= 0:
        return []
    profile = {}
    for p in punkte:
        if p.versatz is not None:
            profile.setdefault(round(p.m_linie, 2), []).append(p)
    stationen = sorted(profile)
    kurven = []
    for st in stationen:
        werte = {}
        for p in profile[st]:
            werte.setdefault(round(p.versatz, 3), []).append(p.hoehe)
        d = np.array(sorted(werte))
        kurven.append((d, np.array([np.mean(werte[k]) for k in sorted(werte)])))
    if len(stationen) < 2:
        return []
    alle_d = np.unique(np.concatenate([k[0] for k in kurven]))
    # deckt[i, j]: Profil i enthält Abstand alle_d[j] (innerhalb seiner Punkte, auch einzelne Punkte)
    deckt = np.array([(alle_d >= d[0] - 1e-6) & (alle_d <= d[-1] + 1e-6) for d, _ in kurven])
    hoehen = np.array([np.interp(alle_d, d, h) for d, h in kurven])
    st_arr = np.array(stationen)

    neu = []
    for s in np.arange(st_arr[0] + abstand, st_arr[-1] - abstand / 2, abstand):
        if np.min(np.abs(st_arr - s)) < abstand / 2:
            continue                                   # zu nah an einem echten Profil
        vor = np.where(st_arr < s)[0][::-1]           # Profile davor, nächstes zuerst
        nach = np.where(st_arr > s)[0]
        pa = achse.punkt(s)
        t = achse.richtung(s, fenster)
        n = np.array([-t[1], t[0]])
        for j, d in enumerate(alle_d):
            iv = next((i for i in vor if deckt[i, j]), None)
            ina = next((i for i in nach if deckt[i, j]), None)
            if iv is None or ina is None:
                continue
            w = (s - st_arr[iv]) / (st_arr[ina] - st_arr[iv])
            z = (1 - w) * hoehen[iv, j] + w * hoehen[ina, j]
            q = pa + n * d
            neu.append(Punkt(q[0], q[1], float(z), float("nan"), "", "", -1, "interpoliert",
                             m_linie=float(s), versatz=float(d)))
    log(f"Profilinterpolation: {len(neu)} Zusatzpunkte in Zwischenprofilen alle {abstand:g} m")
    return neu


def doppelte_bereinigen(punkte, modus: str, bezugsjahr: int, log: Logger):
    """Mehrere Werte am selben Ort: Jahr am nächsten am Bezugsjahr (bei Gleichstand Mittel)."""
    if modus == "alle":
        return punkte
    gruppen = {}
    for p in punkte:
        gruppen.setdefault((round(p.x, 2), round(p.y, 2)), []).append(p)
    neu, n_mehr = [], 0
    for g in gruppen.values():
        if len(g) == 1:
            neu.append(g[0])
            continue
        n_mehr += 1
        wahl = g
        if modus == "jahr" and all(p.jahr is not None for p in g):
            dmin = min(abs(p.jahr - bezugsjahr) for p in g)
            wahl = [p for p in g if abs(p.jahr - bezugsjahr) == dmin]
        p0 = wahl[0]
        p0.hoehe = float(np.mean([p.hoehe for p in wahl]))
        neu.append(p0)
    if n_mehr:
        log(f"Punkte: {n_mehr} Orte mit mehreren Werten – "
            + (f"Wert mit Jahr am nächsten an {bezugsjahr} verwendet" if modus == "jahr" else "gemittelt"))
    return neu


def punkte_setzen(e: Einstellungen, werte, achse: Polylinie, linien, km2m, km_steigend, log: Logger):
    punkte = []
    fehlend = 0
    tol = 1.0
    fluss_vz = 1.0 if (km_steigend == e.fliessrichtung_km_steigend) else -1.0
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
                    punkte.append(Punkt(x, y, w.hoehe, w.station_m, w.name, w.seite, i, "tabelle",
                                        jahr=w.jahr))
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
                if not sp:
                    # Lot verfehlt das Linienende knapp (Linie beginnt/endet am Profil)
                    for ende in (l.c[0], l.c[-1]):
                        pe = ogr.Geometry(ogr.wkbPoint)
                        pe.AddPoint_2D(float(ende[0]), float(ende[1]))
                        if pe.Distance(lot) < 5.0:
                            sp.append((float(ende[0]), float(ende[1])))
                if w.seite and not l.seite:
                    sig = 1.0 if w.seite == "links" else -1.0
                    sp = [q for q in sp if np.dot(np.array(q) - p, n_fluss) * sig > 0]
                if not sp:
                    continue
                q = min(sp, key=lambda q: math.hypot(q[0] - p[0], q[1] - p[1]))
                punkte.append(Punkt(q[0], q[1], w.hoehe, w.station_m, w.name, w.seite, i, "tabelle",
                                    jahr=w.jahr))
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
    """Fläche, die von den Punkten benachbarter Profile aufgespannt wird.

    Vereinigung der konvexen Hüllen je dreier aufeinanderfolgender Profile.
    Verhindert, dass die Delaunay-Triangulation (konvexe Hülle über alles) Flächen
    außerhalb des Korridors – z.B. die Innenseite von Flussbögen – überbrückt.
    """
    xy = np.array([[p.x, p.y] for p in punkte])
    m, _ = achse.stationen_versatz(xy)
    # Gruppen: Tabellenpunkte nach ihrer Profilstation, Zusatzpunkte nach Stationsabschnitt
    schluessel = [("p", round(p.km, 2)) if not math.isnan(p.km) else ("v", int(mm // max(klasse, 1e-3)))
                  for p, mm in zip(punkte, m)]
    gruppen = {}
    for i, s in enumerate(schluessel):
        gruppen.setdefault(s, []).append(i)
    gruppen = sorted(gruppen.values(), key=lambda idx: float(np.mean(m[idx])))
    gruppen = [xy[idx] for idx in gruppen]
    while len(gruppen) < 3:
        gruppen = gruppen + gruppen[-1:]

    def huelle(pts):
        mp = ogr.Geometry(ogr.wkbMultiPoint)
        for x, y in pts:
            pt = ogr.Geometry(ogr.wkbPoint)
            pt.AddPoint_2D(float(x), float(y))
            mp.AddGeometry(pt)
        return mp.ConvexHull().Buffer(puffer)

    flaeche = None
    for i in range(len(gruppen) - 2):
        h = huelle(np.vstack(gruppen[i:i + 3]))
        flaeche = h if flaeche is None else flaeche.Union(h)
    return flaeche


def punkte_schreiben(pfad, punkte, srs):
    if os.path.exists(pfad):
        ogr.GetDriverByName("GPKG").DeleteDataSource(pfad)
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(pfad)
    lyr = ds.CreateLayer("punkte_1945", srs, ogr.wkbPoint25D)
    for name, typ in (("km", ogr.OFTReal), ("name", ogr.OFTString), ("seite", ogr.OFTString),
                      ("hoehe", ogr.OFTReal), ("jahr", ogr.OFTInteger), ("quelle", ogr.OFTString)):
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
        if p.jahr is not None:
            f.SetField("jahr", int(p.jahr))
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
# Variante A: Höhenpunkte (Punkt-Layer) + Geländelinien
# --------------------------------------------------------------------------
_PUNKT_HOEHEN = ["hoehe fg", "point z", "z wert", "hoehe bs", "hoehe 1945", "hoehe45", "hoehe", "hoehe nhn",
                 "z", "hohe", "nhn"]
_JAHR_FELDER = ["jahr", "planjahr", "aufnahmejahr"]
_INFO_FELDER = ["hoe inf", "hoehe inf", "hoeheninfo", "hoehen info", "hoehe info", "hoeheninformation",
                "hoehenquelle", "quelle hoehe"]


def _felder(lyr):
    d = lyr.GetLayerDefn()
    return [d.GetFieldDefn(i).GetName() for i in range(d.GetFieldCount())]


def _feld_waehlen(felder, vorgabe, kandidaten, gueltig=None):
    """Feldname nach Vorgabe (exakt) oder erster Kandidat mit überwiegend gültigen Werten."""
    if vorgabe:
        for f in felder:
            if norm(f) == norm(vorgabe):
                return f
        raise ValueError(f"Feld '{vorgabe}' nicht vorhanden. Felder: {felder}")
    normiert = {norm(f): f for f in felder}
    for k in kandidaten:
        f = normiert.get(k)
        if f and (gueltig is None or gueltig(f)):
            return f
    return None


def punkte_layer_lesen(e: Einstellungen, log: Logger):
    """Bereits verortete Höhenpunkte 1945 (Punkt-Shape/GPKG) einlesen."""
    ds, lyr = _vektor_oeffnen(e.punkte)
    srs = lyr.GetSpatialRef()
    felder = _felder(lyr)
    roh = []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        if g.GetGeometryName() == "MULTIPOINT":
            g = g.GetGeometryRef(0)
        roh.append((g.GetX(), g.GetY(), g.GetZ() if g.Is3D() else 0.0,
                    {k: f.GetField(k) for k in felder}))
    if not roh:
        raise ValueError(f"Keine Punkte in {e.punkte}")

    def gueltig(fname):
        werte = [zahl(r[3][fname]) for r in roh]
        return sum(1 for v in werte if v is not None and v != 0) >= 0.5 * len(roh)

    feld = _feld_waehlen(felder, e.punkte_hoehe_feld, _PUNKT_HOEHEN, gueltig)
    jahr_feld = _feld_waehlen(felder, "", _JAHR_FELDER)
    if feld is None and sum(1 for r in roh if r[2]) >= 0.5 * len(roh):
        quelle = "Z-Wert der Geometrie"
    elif feld is None:
        raise ValueError(f"Kein Höhenfeld in den Punkten gefunden – bitte angeben. Felder: {felder}")
    else:
        quelle = f"Feld '{feld}'"
    punkte, leer = [], 0
    for x, y, z, attr in roh:
        h = zahl(attr[feld]) if feld else z
        if h is None or h == 0:
            leer += 1
            continue
        punkte.append(Punkt(x, y, float(h), float("nan"), "", "", -1, "punkt",
                            jahr=zahl(attr[jahr_feld]) if jahr_feld else None))
    log(f"Punkte: {len(punkte)} Höhenpunkte aus {os.path.basename(e.punkte.split('|')[0])}, "
        f"Höhe aus {quelle}" + (f", {leer} ohne Höhe übersprungen" if leer else ""))
    return punkte, (srs.Clone() if srs else None)


def gelaendelinien_lesen(e: Einstellungen, log: Logger):
    ds, lyr = _vektor_oeffnen(e.gelaendelinien)
    felder = _felder(lyr)
    info_feld = _feld_waehlen(felder, e.linien_hoehe_feld, _INFO_FELDER)
    dgm_werte = [norm(v) for v in e.linien_dgm_werte.split(",") if v.strip()]
    linien, n_dgm = [], 0
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        info = str(f.GetField(info_feld) or "") if info_feld else ""
        aus_dgm = any(w in norm(info) for w in dgm_werte) if info else False
        n_dgm += aus_dgm
        for pts in _linien_aus_geom(g):
            if len(pts) >= 2:
                l = Polylinie(pts, "dgm" if aus_dgm else "", "", f.GetFID(), info)
                if l.laenge > 0:
                    linien.append(l)
    log(f"Geländelinien: {len(linien)} Linien, {sum(l.laenge for l in linien)/1000:.1f} km"
        + (f"; {n_dgm} mit Höheninfo aus DGM1 (Feld '{info_feld}')" if info_feld else
           " (kein Höheninfo-Feld gefunden)"))
    return linien


def gelaendelinien_verdichten(e: Einstellungen, punkte, linien, dgm_wert, log: Logger):
    """Punkte alle `linien_abstand` m entlang jeder Geländelinie.

    Höhe linear zwischen den Höhenpunkten, die auf der Linie liegen (Abstand < fang_toleranz);
    über das letzte Punktpaar hinaus konstant. Linien mit Höheninfo 'DGM'/'Sohle' und Linien
    ohne Höhenpunkte erhalten die Höhen aus dem (ggf. um die Sohle ergänzten) DGM1.
    """
    P = np.array([[p.x, p.y] for p in punkte])
    Z = np.array([p.hoehe for p in punkte])
    tol = e.fang_toleranz
    neu = []
    n_pkt = n_dgm = n_ohne = n_leer = 0
    for l in linien:
        ms = np.unique(np.r_[np.arange(0.0, l.laenge, max(e.linien_abstand, 0.01)), l.cum, l.laenge])
        xy = l.punkt(ms).T
        bekannt = np.empty(0)
        if l.typ == "dgm":
            z, quelle = dgm_wert(xy), "linie_dgm"
            n_dgm += 1
        else:
            x0, y0 = l.c.min(0) - tol
            x1, y1 = l.c.max(0) + tol
            kand = np.where((P[:, 0] >= x0) & (P[:, 0] <= x1) & (P[:, 1] >= y0) & (P[:, 1] <= y1))[0]
            if kand.size:
                mk, dk = l.stationen_versatz(P[kand])
                auf = np.abs(dk) < tol
                kand, mk = kand[auf], mk[auf]
            if kand.size:
                werte = {}
                for m, i in zip(mk, kand):
                    werte.setdefault(round(float(m), 3), []).append(Z[i])
                bekannt = np.array(sorted(werte))
                zk = np.array([np.mean(werte[m]) for m in bekannt])
                z, quelle = np.interp(ms, bekannt, zk), "linie"
                n_pkt += 1
            else:
                z, quelle = dgm_wert(xy), "linie_dgm"
                n_ohne += 1
        if bekannt.size:
            frei = np.min(np.abs(ms[:, None] - bekannt[None, :]), axis=1) > 0.01
        else:
            frei = np.ones(len(ms), bool)
        ok = frei & np.isfinite(z)
        if not ok.any():
            n_leer += 1
        for (x, y), h in zip(xy[ok], z[ok]):
            neu.append(Punkt(float(x), float(y), float(h), float("nan"), l.typ_original, "", -1, quelle))
    log(f"Geländelinien: {len(neu)} Zusatzpunkte (alle {e.linien_abstand:g} m) – {n_pkt} Linien zwischen "
        f"Höhenpunkten interpoliert, {n_dgm} Linien aus DGM1"
        + (f", {n_ohne} Linien ohne Höhenpunkt -> Höhen aus DGM1" if n_ohne else "")
        + (f", {n_leer} Linien ohne Werte (außerhalb DGM1?)" if n_leer else ""))
    return neu


def _datenflaeche(punkte, linien, puffer):
    """Puffer um alle Punkte und Linien; von Linien umschlossene Flächen werden gefüllt."""
    teile = ogr.Geometry(ogr.wkbMultiPolygon)

    def dazu(g):
        if g.GetGeometryName() == "MULTIPOLYGON":
            for i in range(g.GetGeometryCount()):
                teile.AddGeometry(g.GetGeometryRef(i))
        elif g.GetGeometryName() == "POLYGON":
            teile.AddGeometry(g)

    for l in linien:
        dazu(l.geom.Buffer(puffer, 4))
    mp = ogr.Geometry(ogr.wkbMultiPoint)
    for p in punkte:
        q = ogr.Geometry(ogr.wkbPoint)
        q.AddPoint_2D(p.x, p.y)
        mp.AddGeometry(q)
    dazu(mp.Buffer(puffer, 4))
    vereint = teile.UnionCascaded()
    ohne_loecher = ogr.Geometry(ogr.wkbMultiPolygon)
    polys = [vereint] if vereint.GetGeometryName() == "POLYGON" else \
        [vereint.GetGeometryRef(i) for i in range(vereint.GetGeometryCount())]
    for poly in polys:
        neu = ogr.Geometry(ogr.wkbPolygon)
        neu.AddGeometry(poly.GetGeometryRef(0).Clone())
        ohne_loecher.AddGeometry(neu)
    return ohne_loecher.UnionCascaded()


def _xy_eindeutig(punkte):
    """Doppelte Lagen (1 cm) entfernen – der erste Punkt (Höhenpunkt vor Linienpunkt) bleibt."""
    gesehen, aus = set(), []
    for p in punkte:
        k = (round(p.x, 2), round(p.y, 2))
        if k not in gesehen:
            gesehen.add(k)
            aus.append(p)
    return aus


# --------------------------------------------------------------------------
# DGM1: Mosaik, Sohle, Kantenlängen-Maske
# --------------------------------------------------------------------------
def dgm_vorbereiten(e: Einstellungen, log: Logger):
    """Mosaik aus Einzelkacheln bauen bzw. fertiges Mosaik verwenden. Rückgabe: Pfad oder None."""
    dateien = _dgm1_quellen(e.dgm1)
    if not dateien:
        return None
    if len(dateien) == 1:
        log(f"DGM1: Mosaik {os.path.basename(dateien[0])}")
        return dateien[0]
    vrt_pfad = os.path.join(e.ausgabe_ordner, "DGM1_Mosaik.vrt")
    vrt = gdal.BuildVRT(vrt_pfad, dateien)
    if vrt is None:
        raise ValueError("DGM1-Kacheln konnten nicht zu einem Mosaik zusammengefügt werden.")
    log(f"DGM1: Mosaik aus {len(dateien)} Kacheln gebaut ({vrt.RasterXSize} x {vrt.RasterYSize} Pixel)")
    if not e.dgm1_mosaik_speichern:
        vrt = None
        return vrt_pfad
    tif = os.path.join(e.ausgabe_ordner, "DGM1_Mosaik.tif")
    gdal.Translate(tif, vrt, creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=3", "TILED=YES",
                                              "BIGTIFF=IF_SAFER"])
    vrt = None
    log("-> DGM1_Mosaik.tif")
    return tif


def _layer_aus_geom(geoms, srs, typ=ogr.wkbPolygon):
    mds = gdal.GetDriverByName("Memory").Create("", 0, 0, 0, gdal.GDT_Unknown)
    ml = mds.CreateLayer("m", srs, typ)
    for g in geoms:
        f = ogr.Feature(ml.GetLayerDefn())
        f.SetGeometry(g)
        ml.CreateFeature(f)
    return mds, ml


def _punkte_ds(xyz, srs):
    """In-Memory-Punktlayer (für gdal.Grid)."""
    mds = gdal.GetDriverByName("Memory").Create("", 0, 0, 0, gdal.GDT_Unknown)
    ml = mds.CreateLayer("p", srs, ogr.wkbPoint)
    ml.CreateField(ogr.FieldDefn("z", ogr.OFTReal))
    ml.StartTransaction()
    for x, y, z in xyz:
        f = ogr.Feature(ml.GetLayerDefn())
        f.SetField("z", float(z))
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(float(x), float(y))
        f.SetGeometry(g)
        ml.CreateFeature(f)
    ml.CommitTransaction()
    return mds


def _grid(xyz, srs, algorithmus, breite, hoehe, gt):
    mds = _punkte_ds(xyz, srs)
    xmin, ymax = gt[0], gt[3]
    xmax, ymin = xmin + breite * gt[1], ymax + hoehe * gt[5]
    r = gdal.Grid("", mds, format="MEM", outputType=gdal.GDT_Float64, outputBounds=[xmin, ymax, xmax, ymin],
                  width=breite, height=hoehe, algorithm=algorithmus, zfield="z", layers=["p"])
    a = r.GetRasterBand(1).ReadAsArray()
    a[a == NODATA] = np.nan
    return a


def sohle_einbrennen(e: Einstellungen, dgm, gt, srs, srs_wkt, log: Logger):
    """Gewässersohle in das DGM1 einsetzen (DGM1 zeigt nur die Wasseroberfläche)."""
    hoehe_px, breite_px = dgm.shape
    ds, lyr = _vektor_oeffnen(e.sohle)
    felder = _felder(lyr)
    feld = _feld_waehlen(felder, e.sohle_hoehe_feld,
                         ["sohle", "sohlhoehe", "sohlenhoehe", "hoehe sohle", "sohle nhn", "z wert", "hoehe", "z"])
    pts, geoms = [], []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        fh = zahl(f.GetField(feld)) if feld else None
        name = g.GetGeometryName()
        if "POINT" in name:
            for i in range(max(g.GetGeometryCount(), 1)):
                q = g.GetGeometryRef(i) if g.GetGeometryCount() else g
                z = fh if fh is not None else (q.GetZ() if q.Is3D() else None)
                if z:
                    pts.append((q.GetX(), q.GetY(), z))
            continue
        geoms.append(g.Clone())
        for teil in (g.GetGeometryRef(i) for i in range(g.GetGeometryCount())) if "MULTI" in name else [g]:
            c = np.array(teil.GetPoints())
            l = Polylinie(c)
            if l.laenge == 0:
                continue
            z_v = c[:, 2] if c.shape[1] > 2 and np.any(c[:, 2]) else None
            if z_v is None and fh is None:
                continue
            ms = np.unique(np.r_[np.arange(0, l.laenge, 1.0), l.laenge])
            keep = np.r_[True, np.any(np.abs(np.diff(c[:, :2], axis=0)) > 1e-9, axis=1)]
            z = np.interp(ms, l.cum, z_v[keep]) if z_v is not None else np.full(len(ms), fh)
            for (x, y), h in zip(l.punkt(ms).T, z):
                pts.append((x, y, h))
    if not pts:
        raise ValueError("Sohle: keine Höhen gefunden (Z-Werte der Linien oder Höhenfeld angeben).")
    pts = np.array(pts)
    px = gt[1]
    # Sohlstreifen
    if geoms:
        streifen = [g.Buffer(max(e.sohle_puffer, 0.5 * px)) for g in geoms]
    else:
        streifen = [ogr.CreateGeometryFromWkt(f"POINT ({x} {y})").Buffer(max(e.sohle_puffer, 0.5 * px))
                    for x, y, _ in pts]
    _m, sl = _layer_aus_geom(streifen, srs)
    in_streifen = _maske_layer(sl, breite_px, hoehe_px, gt, srs_wkt)
    r = max(e.sohle_puffer, px) + px
    s = _grid(pts, srs, f"nearest:radius1={r}:radius2={r}:nodata={NODATA}", breite_px, hoehe_px, gt)
    ziel = np.where(in_streifen, s, np.nan)
    # Böschung zwischen Sohlstreifen und Rand der Wasserfläche
    if e.gewaesser:
        gds, gl = _vektor_oeffnen(e.gewaesser)
        im_gew = _maske_layer(gl, breite_px, hoehe_px, gt, srs_wkt)
        rand = []
        for f in gl:
            g = f.GetGeometryRef()
            if g is None:
                continue
            b = g.GetBoundary()
            for teil in (b.GetGeometryRef(i) for i in range(b.GetGeometryCount())) if b.GetGeometryCount() else [b]:
                l = Polylinie(np.array(teil.GetPoints()))
                if l.laenge:
                    rand.append(l.punkt(np.arange(0, l.laenge, 1.0)).T)
        if rand:
            rand = np.vstack(rand)
            c = np.floor((rand[:, 0] - gt[0]) / gt[1]).astype(int)
            rr = np.floor((rand[:, 1] - gt[3]) / gt[5]).astype(int)
            ok = (c >= 0) & (c < breite_px) & (rr >= 0) & (rr < hoehe_px)
            rz = np.full(len(rand), np.nan)
            rz[ok] = dgm[rr[ok], c[ok]]
            ok &= np.isfinite(rz)
            b = _grid(np.vstack([pts, np.c_[rand[ok], rz[ok]]]), srs, f"linear:radius=0:nodata={NODATA}",
                      breite_px, hoehe_px, gt)
            ziel = np.where(im_gew & ~in_streifen, b, ziel)
    setzen = np.isfinite(ziel) & np.isfinite(dgm)
    if e.sohle_nur_tiefer:
        setzen &= ziel < dgm
    neu = dgm.copy()
    neu[setzen] = ziel[setzen]
    if setzen.any():
        log(f"Sohle: {setzen.sum() * px * px:,.0f} m² des DGM1 durch Sohlhöhen ersetzt, "
            f"im Mittel {np.mean(dgm[setzen] - ziel[setzen]):.2f} m tiefer"
            + (" (inkl. Böschung bis Rand Wasserfläche)" if e.gewaesser else ""))
    else:
        log("WARNUNG Sohle: keine DGM1-Zelle verändert (Sohle höher als DGM1 oder außerhalb?)")
    return neu


def _maske_layer(lyr, breite, hoehe, gt, srs_wkt):
    mem = gdal.GetDriverByName("MEM").Create("", breite, hoehe, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(srs_wkt)
    gdal.RasterizeLayer(mem, [1], lyr, burn_values=[1])
    return mem.GetRasterBand(1).ReadAsArray() == 1


def lange_dreiecke_maske(punkte, max_kante, breite, hoehe, gt, srs, srs_wkt):
    """Maske der Pixel in Delaunay-Dreiecken mit einer Kante > max_kante (dort keine Interpolation)."""
    mp = ogr.Geometry(ogr.wkbMultiPoint)
    for p in punkte:
        q = ogr.Geometry(ogr.wkbPoint)
        q.AddPoint_2D(p.x, p.y)
        mp.AddGeometry(q)
    tri = mp.DelaunayTriangulation(0.0, False)
    lang = []
    for i in range(tri.GetGeometryCount()):
        t = tri.GetGeometryRef(i)
        c = np.array(t.GetGeometryRef(0).GetPoints())[:, :2]
        if np.max(np.hypot(*np.diff(c, axis=0).T)) > max_kante:
            lang.append(t.Clone())
    if not lang:
        return np.zeros((hoehe, breite), bool), 0
    _m, ml = _layer_aus_geom(lang, srs)
    return _maske_layer(ml, breite, hoehe, gt, srs_wkt), len(lang)


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


def raster_schreiben(pfad, arr, gt, srs_wkt, beschreibung="", byte=False):
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(pfad, arr.shape[1], arr.shape[0], 1, gdal.GDT_Byte if byte else gdal.GDT_Float32,
                    ["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"]
                    + ([] if byte else ["PREDICTOR=3"]))
    ds.SetGeoTransform(gt)
    ds.SetProjection(srs_wkt)
    b = ds.GetRasterBand(1)
    if byte:
        b.SetNoDataValue(0)
        b.WriteArray(arr.astype(np.uint8))
    else:
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


def _maske_rastern(geom_oder_pfad, breite, hoehe, gt, srs, srs_wkt):
    mem = gdal.GetDriverByName("MEM").Create("", breite, hoehe, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(srs_wkt)
    if isinstance(geom_oder_pfad, str):
        mds, mlyr = _vektor_oeffnen(geom_oder_pfad)
    else:
        mds = gdal.GetDriverByName("Memory").Create("", 0, 0, 0, gdal.GDT_Unknown)
        mlyr = mds.CreateLayer("m", srs, ogr.wkbPolygon)
        f = ogr.Feature(mlyr.GetLayerDefn())
        f.SetGeometry(geom_oder_pfad)
        mlyr.CreateFeature(f)
    gdal.RasterizeLayer(mem, [1], mlyr, burn_values=[1])
    return mem.GetRasterBand(1).ReadAsArray() == 1


def ausfuehren(e: Einstellungen, log: Logger = print, fortschritt: Callable[[float], None] = None) -> dict:
    """Komplette Prozesskette. Gibt Dict mit den Pfaden der Ergebnisse zurück."""
    fs = fortschritt or (lambda x: None)
    if not e.ausgabe_ordner:
        raise ValueError("Kein Ausgabeordner angegeben.")
    if not (e.punkte or e.excel):
        raise ValueError("Keine Höhendaten 1945 angegeben (Punkt-Layer oder Tabelle + Achse).")
    os.makedirs(e.ausgabe_ordner, exist_ok=True)
    protokoll = []

    def lg(msg):
        protokoll.append(msg)
        log(msg)

    lg("=== DGM 1945 Rekonstruktion ===")
    # ---------------- 1. Daten einlesen ----------------
    achse, linien, attr, werte = None, [], {}, None
    if e.punkte:
        methode = "punkte"
        punkte, srs = punkte_layer_lesen(e, lg)
    else:
        werte = tabelle_lesen(e, lg)
        methode = e.methode
        if methode == "auto":
            methode = "abstand" if any(w.abstand is not None for w in werte) else "lot"
        lg(f"Methode: {methode}")
        achse, linien, srs, attr = linien_lesen(e, methode, lg)
    gl = gelaendelinien_lesen(e, lg) if e.gelaendelinien else []
    dgm_pfad = dgm_vorbereiten(e, lg)
    fs(10)

    dgm_srs_wkt = ""
    if dgm_pfad:
        q = gdal.Open(dgm_pfad)
        gt0 = q.GetGeoTransform()
        px, py, ox, oy = gt0[1], abs(gt0[5]), gt0[0], gt0[3]
        dgm_srs_wkt = q.GetProjection()
        q = None
    else:
        px = py = float(e.pixelgroesse)
        ox = oy = 0.0
    if e.epsg:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(int(e.epsg))
    elif srs is None and dgm_srs_wkt:
        srs = osr.SpatialReference(wkt=dgm_srs_wkt)
        lg(f"Eingangsdaten ohne Koordinatensystem (.prj fehlt) – übernommen vom DGM1: {srs.GetName()}")
    if srs is None:
        raise ValueError("Kein Koordinatensystem bekannt – bitte EPSG angeben.")
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    srs_wkt = srs.ExportToWkt()

    # ---------------- 2. Höhenpunkte ----------------
    if methode != "punkte":
        km2m, km_steigend = km_abbildung(e, achse, attr, werte, lg)
        if methode == "abstand":
            punkte = punkte_abstand(e, werte, achse, km2m, km_steigend, lg)
        else:
            punkte = punkte_setzen(e, werte, achse, linien, km2m, km_steigend, lg)
    punkte = doppelte_bereinigen(punkte, e.duplikate, e.bezugsjahr, lg)
    if len(punkte) < 3:
        raise ValueError("Zu wenige Punkte für eine Triangulation (< 3).")
    fs(15)

    # ---------------- 3. Ergebnisfläche und Rastergitter ----------------
    fuell = None
    if e.untersuchungsgebiet:
        fuell = e.untersuchungsgebiet
        lg("Ergebnisfläche: Untersuchungsgebiet")
    elif e.maske:
        fuell = e.maske
    elif achse is not None and not gl:
        _, d = achse.stationen_versatz([[p.x, p.y] for p in punkte])
        breite_m = float(np.max(np.abs(d))) + e.auffuell_puffer
        fuell = achse.geom.Buffer(breite_m)
        lg(f"Ergebnisfläche: Achse ± {breite_m:.1f} m")
    else:
        fuell = _datenflaeche(punkte, gl, max(e.auffuell_puffer, px))
        lg(f"Ergebnisfläche: Punkte und Linien + {e.auffuell_puffer:g} m, umschlossene Flächen gefüllt "
           "(Untersuchungsgebiet angeben, um sie genau festzulegen)")
    if isinstance(fuell, str):
        _fds, _fl = _vektor_oeffnen(fuell)
        x0, x1, y0, y1 = _fl.GetExtent()
    else:
        _polygon_schreiben(os.path.join(e.ausgabe_ordner, "Ergebnisflaeche.gpkg"), fuell, srs)
        x0, x1, y0, y1 = fuell.GetEnvelope()
    if not e.untersuchungsgebiet:
        xs = [p.x for p in punkte]
        ys = [p.y for p in punkte]
        x0, x1, y0, y1 = min(x0, min(xs)), max(x1, max(xs)), min(y0, min(ys)), max(y1, max(ys))
    xmin = ox + math.floor((x0 - ox) / px) * px
    xmax = ox + math.ceil((x1 - ox) / px) * px
    ymax = oy - math.floor((oy - y1) / py) * py
    ymin = oy - math.ceil((oy - y0) / py) * py
    breite = int(round((xmax - xmin) / px))
    hoehe = int(round((ymax - ymin) / py))
    gt = (xmin, px, 0.0, ymax, 0.0, -py)
    in_flaeche = _maske_rastern(fuell, breite, hoehe, gt, srs, srs_wkt)
    lg(f"Rastergitter: {breite} x {hoehe} Pixel à {px:g} m (am DGM1 ausgerichtet)")

    # ---------------- 4. DGM1 laden, Abdeckung prüfen, Sohle ----------------
    dgm = None
    erg = {}
    if dgm_pfad:
        src = gdal.Open(dgm_pfad)
        gleiches_krs = not dgm_srs_wkt or osr.SpatialReference(wkt=dgm_srs_wkt).IsSame(srs)
        dgm_ds = gdal.Warp("", src, format="MEM", outputBounds=(xmin, ymin, xmax, ymax),
                           xRes=px, yRes=py, srcSRS=dgm_srs_wkt or srs_wkt, dstSRS=srs_wkt,
                           resampleAlg="near" if gleiches_krs else "bilinear",
                           dstNodata=NODATA, outputType=gdal.GDT_Float64)
        src = None
        dgm = dgm_ds.GetRasterBand(1).ReadAsArray()
        dgm_ds = None
        dgm[(dgm == NODATA) | (dgm == 0)] = np.nan
        luecke = in_flaeche & ~np.isfinite(dgm)
        anteil = luecke.sum() / max(in_flaeche.sum(), 1)
        if anteil > 0.001:
            lg(f"WARNUNG: Das DGM1 deckt {anteil:.1%} der Ergebnisfläche nicht ab "
               f"({luecke.sum() * px * py:,.0f} m²) – Kacheln vollständig?")
        else:
            lg("DGM1 deckt die Ergebnisfläche vollständig ab")
        if e.sohle:
            dgm = sohle_einbrennen(e, dgm, gt, srs, srs_wkt, lg)
            p_s = os.path.join(e.ausgabe_ordner, "DGM1_mit_Sohle.tif")
            raster_schreiben(p_s, dgm, gt, srs_wkt, "DGM1 mit eingebrannter Sohle [m ue. NHN]")
            erg["dgm_sohle"] = p_s
            lg("-> DGM1_mit_Sohle.tif")
    elif e.sohle:
        lg("WARNUNG: Sohle angegeben, aber kein DGM1 – wird übersprungen")
    fs(30)

    def dgm_wert(xy):
        out = np.full(len(xy), np.nan)
        if dgm is None:
            return out
        c = np.floor((xy[:, 0] - gt[0]) / gt[1]).astype(int)
        r = np.floor((xy[:, 1] - gt[3]) / gt[5]).astype(int)
        ok = (c >= 0) & (c < breite) & (r >= 0) & (r < hoehe)
        out[ok] = dgm[r[ok], c[ok]]
        return out

    # ---------------- 5. Verdichtung ----------------
    if gl:
        zusatz = gelaendelinien_verdichten(e, punkte, gl, dgm_wert, lg)
    elif methode == "abstand":
        zusatz = profile_interpolieren(punkte, achse, e.verdichtung, e.tangenten_fenster, lg)
    elif linien:
        zusatz = verdichten(punkte, linien, e.verdichtung, lg)
    else:
        zusatz = []
    punkte_alle = _xy_eindeutig(punkte + zusatz)
    pfad_pkt = os.path.join(e.ausgabe_ordner, "Punkte_1945.gpkg")
    punkte_schreiben(pfad_pkt, punkte_alle, srs)
    erg["punkte"] = pfad_pkt
    lg(f"-> Punkte_1945.gpkg ({len(punkte_alle)} Punkte)")
    fs(40)

    # ---------------- 6. Triangulation ----------------
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
    fs(60)

    if e.max_kante > 0:
        lang, n = lange_dreiecke_maske(punkte_alle, e.max_kante, breite, hoehe, gt, srs, srs_wkt)
        gok[lang] = np.nan
        lg(f"TIN: {n} Dreiecke mit Kanten > {e.max_kante:g} m verworfen (dort DGM1)")
    if e.maske and e.maske != fuell:
        gok[~_maske_rastern(e.maske, breite, hoehe, gt, srs, srs_wkt)] = np.nan
        lg("TIN auf vorgegebenes Polygon begrenzt")
    if e.auto_maske and achse is not None and methode != "punkte" and not gl:
        poly = korridor_polygon(punkte_alle, achse, e.masken_klasse, 0.5 * px)
        gok[~_maske_rastern(poly, breite, hoehe, gt, srs, srs_wkt)] = np.nan
        _polygon_schreiben(os.path.join(e.ausgabe_ordner, "Korridor_Maske.gpkg"), poly, srs)
        lg("TIN auf Korridor der Profile begrenzt -> Korridor_Maske.gpkg")
    gok[~in_flaeche] = np.nan
    fs(70)

    # ---------------- 7. DGM1 auffüllen, Ergebnisse ----------------
    fl = px * py
    quelle = np.where(np.isfinite(gok), 1, 0).astype(np.uint8)
    if dgm is not None and e.dgm1_auffuellen:
        nutzen = in_flaeche & ~np.isfinite(gok) & np.isfinite(dgm)
        gok[nutzen] = dgm[nutzen]
        quelle[nutzen] = 2
    lg(f"GOK45: {np.sum(quelle == 1) * fl:,.0f} m² aus Höhendaten 1945 trianguliert, "
       f"{np.sum(quelle == 2) * fl:,.0f} m² aus DGM1 übernommen")
    p_q = os.path.join(e.ausgabe_ordner, "GOK45_Quelle.tif")
    raster_schreiben(p_q, quelle, gt, srs_wkt, "1 = TIN aus Daten 1945, 2 = DGM1 übernommen", byte=True)
    erg["quelle"] = p_q

    p_gok = os.path.join(e.ausgabe_ordner, "GOK45.tif")
    raster_schreiben(p_gok, gok, gt, srs_wkt, "GOK 1945 [m ue. NHN]")
    erg["gok45"] = p_gok
    lg(f"-> GOK45.tif   ({_stat(gok)})")

    bombe = gok - float(e.eindringtiefe)
    p_bombe = os.path.join(e.ausgabe_ordner, f"GOK45_minus_{e.eindringtiefe:g}m_Eindringtiefe.tif")
    raster_schreiben(p_bombe, bombe, gt, srs_wkt,
                     f"max. Bomben-Eindringtiefe (GOK45 - {e.eindringtiefe:g} m) [m ue. NHN]")
    erg["eindringtiefe"] = p_bombe
    lg(f"-> {os.path.basename(p_bombe)}   ({_stat(bombe)})")
    fs(80)

    if dgm is not None:
        diff = dgm - gok
        p_diff = os.path.join(e.ausgabe_ordner, "Differenz_DGM1_minus_GOK45.tif")
        raster_schreiben(p_diff, diff, gt, srs_wkt, "DGM1 - GOK45 [m] (+ Auftrag / - Abtrag seit 1945)")
        erg["differenz"] = p_diff
        lg(f"-> Differenz_DGM1_minus_GOK45.tif   ({_stat(diff)})")
        v = diff[np.isfinite(diff)]
        if v.size:
            lg(f"   Auftrag seit 1945: {v[v > 0].sum() * fl:,.0f} m³   "
               f"Abtrag: {abs(v[v < 0].sum()) * fl:,.0f} m³   Fläche: {v.size * fl:,.0f} m²")
        sonde = dgm - bombe
        p_sonde = os.path.join(e.ausgabe_ordner, "Sondiertiefe_unter_DGM1.tif")
        raster_schreiben(p_sonde, sonde, gt, srs_wkt, "Tiefe der max. Bomben-Eindringtiefe unter heutiger GOK [m]")
        erg["sondiertiefe"] = p_sonde
        lg(f"-> Sondiertiefe_unter_DGM1.tif   ({_stat(sonde)})")
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
        if k in ("excel", "linien", "ausgabe_ordner", "achse_datei", "km_punkte", "maske", "punkte",
                 "gelaendelinien", "untersuchungsgebiet", "sohle", "gewaesser"):
            kw[k] = pfad_abs(v)
        elif k == "dgm1":
            kw[k] = [pfad_abs(x.strip()) for x in re.split(r"[;\n]", v) if x.strip()]
        elif "bool" in str(typ):
            kw[k] = None if v.strip().lower() == "auto" else _bool(v)
        elif "float" in str(typ):
            kw[k] = float(v.replace(",", "."))
        elif "int" in str(typ):
            kw[k] = int(v)
        else:
            kw[k] = v
    return Einstellungen(**kw)


def einstellungen_als_ini(e: Einstellungen, pfad: str):
    """Einstellungen speichern – die Datei kann später direkt wiederverwendet werden."""
    standard = Einstellungen()
    zeilen = ["# DGM 1945 – gespeicherte Einstellungen", "# Aufruf: python dgm1945_kern.py diese_datei.ini", "",
              "[einstellungen]"]
    for name in Einstellungen.__dataclass_fields__:
        wert = getattr(e, name)
        if wert == getattr(standard, name) and name != "ausgabe_ordner":
            continue
        if isinstance(wert, list):
            wert = "; ".join(wert)
        elif isinstance(wert, bool):
            wert = "ja" if wert else "nein"
        elif wert is None:
            wert = "auto"
        zeilen.append(f"{name} = {wert}")
    with open(pfad, "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")


# --------------------------------------------------------------------------
# Assistent: fragt zuerst alle Daten ab
# --------------------------------------------------------------------------
_VEKTOR = [("Shape / GeoPackage", "*.shp *.gpkg"), ("Alle Dateien", "*.*")]
_RASTER = [("Raster", "*.tif *.tiff *.vrt *.asc *.xyz *.img"), ("Alle Dateien", "*.*")]
_TABELLE = [("Tabellen", "*.xlsx *.xlsm *.xls *.csv *.txt"), ("Alle Dateien", "*.*")]


class _Fragen:
    """Dateiauswahl per Dialog (tkinter) oder – ohne Oberfläche – per Eingabe in der Konsole."""

    def __init__(self):
        try:
            import tkinter
            from tkinter import filedialog, messagebox, simpledialog
            self.root = tkinter.Tk()
            self.root.withdraw()
            self.fd, self.mb, self.sd = filedialog, messagebox, simpledialog
        except Exception:
            self.root = None

    def datei(self, titel, typen, pflicht=False, mehrere=False):
        while True:
            if self.root:
                print(titel)
                if mehrere:
                    wahl = list(self.fd.askopenfilenames(title=titel, filetypes=typen))
                else:
                    wahl = self.fd.askopenfilename(title=titel, filetypes=typen) or ""
            else:
                ein = input(f"{titel}{' (mehrere mit ; trennen)' if mehrere else ''}"
                            f"{'' if pflicht else ' [Enter = keine]'}: ").strip().strip('"')
                wahl = [x.strip().strip('"') for x in ein.split(";") if x.strip()] if mehrere else ein
            if wahl or not pflicht:
                return wahl
            print("  -> Diese Angabe wird benötigt.")

    def ordner(self, titel):
        while True:
            if self.root:
                print(titel)
                wahl = self.fd.askdirectory(title=titel)
            else:
                wahl = input(f"{titel}: ").strip().strip('"')
            if wahl:
                return wahl

    def ja(self, frage):
        if self.root:
            return self.mb.askyesno("DGM 1945", frage)
        return input(f"{frage} [j/n]: ").strip().lower().startswith("j")

    def zahl(self, frage, standard):
        if self.root:
            w = self.sd.askfloat("DGM 1945", frage, initialvalue=standard)
            return standard if w is None else w
        ein = input(f"{frage} [{standard:g}]: ").strip().replace(",", ".")
        return float(ein) if ein else standard

    def info(self, text):
        print(text)
        if self.root:
            self.mb.showinfo("DGM 1945", text)


def assistent():
    """Fragt nacheinander alle Eingangsdaten ab, zeigt eine Übersicht und startet die Berechnung."""
    f = _Fragen()
    print("=== DGM 1945 – Dateneingabe ===")
    e = Einstellungen()
    if f.ja("Liegen die Höhenpunkte 1945 bereits als Punkt-Shape (verortet, mit Höhe) vor?\n\n"
            "Nein = Tabelle (Kilometrierung/Seite/Abstand/Höhe) + Achse"):
        e.punkte = f.datei("1/6  Höhenpunkte 1945 (Punkt-Shape)", _VEKTOR, pflicht=True)
    else:
        e.excel = f.datei("1/6  Höhentabelle 1945 (Excel/CSV)", _TABELLE, pflicht=True)
        e.linien = f.datei("1/6  Flussachse mit Kilometrierung (Linien-Shape)", _VEKTOR, pflicht=True)
    e.gelaendelinien = f.datei("2/6  Geländelinien (Linien-Shape) – Abbrechen = keine", _VEKTOR)
    e.dgm1 = f.datei("3/6  Aktuelles DGM1: fertiges Mosaik ODER alle Einzelkacheln markieren", _RASTER,
                     pflicht=True, mehrere=True)
    e.untersuchungsgebiet = f.datei("4/6  Untersuchungsgebiet (Polygon) – Abbrechen = automatisch", _VEKTOR)
    if f.ja("Soll die Gewässersohle in das aktuelle DGM1 eingebrannt werden?\n"
            "(das DGM1 zeigt sonst nur die Wasseroberfläche)"):
        e.sohle = f.datei("5/6  Sohle: Linien (mit Z-Werten oder Höhenfeld) oder Punkte", _VEKTOR, pflicht=True)
        e.sohle_puffer = f.zahl("Halbe Sohlbreite in m (Puffer um die Sohllinie)", 2.5)
        e.gewaesser = f.datei("5/6  Wasserfläche (Polygon) für die Böschung – Abbrechen = keine", _VEKTOR)
    e.ausgabe_ordner = f.ordner("6/6  Ausgabeordner wählen")

    uebersicht = "\n".join([
        "Folgende Daten werden verwendet:",
        f"  Höhenpunkte:   {e.punkte or e.excel}",
        *([f"  Achse:         {e.linien}"] if e.linien else []),
        f"  Geländelinien: {e.gelaendelinien or '-'}",
        f"  DGM1:          {len(e.dgm1)} Datei(en){' -> Mosaik wird gebaut' if len(e.dgm1) > 1 else ''}",
        f"  Untersuchungsgebiet: {e.untersuchungsgebiet or 'automatisch'}",
        f"  Sohle:         {e.sohle or '-'}",
        f"  Ausgabe:       {e.ausgabe_ordner}",
    ])
    if f.root:
        print(uebersicht)
    if not f.ja(uebersicht + "\n\nBerechnung starten?"):
        print("Abgebrochen.")
        return 1
    os.makedirs(e.ausgabe_ordner, exist_ok=True)
    ini = os.path.join(e.ausgabe_ordner, "Einstellungen.ini")
    einstellungen_als_ini(e, ini)
    try:
        ausfuehren(e)
    except Exception as ex:
        f.info(f"FEHLER: {ex}")
        return 1
    f.info(f"Fertig. Ergebnisse in:\n{e.ausgabe_ordner}\n\nEinstellungen gespeichert in Einstellungen.ini "
           "(für eine erneute Berechnung: Datei auf DGM1945_starten.bat ziehen).")
    return 0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        return assistent()
    if argv[0] in ("-h", "--help", "/?"):
        print(__doc__)
        return 0
    for ini in argv:
        e = einstellungen_aus_ini(ini)
        ausfuehren(e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
