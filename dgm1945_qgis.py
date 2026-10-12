# -*- coding: utf-8 -*-
"""
QGIS-Processing-Werkzeug „DGM 1945 rekonstruieren (GOK45)“

Installation: Diese Datei UND dgm1945_kern.py in den Processing-Skriptordner
kopieren (QGIS: Einstellungen > Optionen > Verarbeitung > Skripte, unter Windows
meist %APPDATA%\\QGIS\\QGIS3\\profiles\\default\\processing\\scripts) und QGIS
neu starten oder in der Toolbox „Skripte neu laden“. Das Werkzeug erscheint unter
Skripte > DGM 1945.
"""

import os
import sys

from qgis.core import (QgsProcessing, QgsProcessingAlgorithm, QgsProcessingContext,
                       QgsProcessingException, QgsProcessingParameterBoolean,
                       QgsProcessingParameterEnum, QgsProcessingParameterFeatureSource,
                       QgsProcessingParameterField, QgsProcessingParameterFile,
                       QgsProcessingParameterFolderDestination,
                       QgsProcessingParameterMultipleLayers, QgsProcessingParameterNumber,
                       QgsProcessingParameterString)


def _kern():
    """dgm1945_kern.py aus dem gleichen Ordner bzw. den Processing-Skriptordnern laden."""
    ordner = []
    if "__file__" in globals():
        ordner.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from processing.script import ScriptUtils
        ordner.extend(ScriptUtils.scriptsFolders())
    except Exception:
        pass
    for o in ordner:
        if o and o not in sys.path:
            sys.path.append(o)
    try:
        import dgm1945_kern
    except ImportError:
        raise QgsProcessingException(
            "dgm1945_kern.py nicht gefunden. Bitte in den Processing-Skriptordner kopieren: "
            + ", ".join(ordner))
    return dgm1945_kern


class Dgm1945Algorithmus(QgsProcessingAlgorithm):
    EXCEL = "EXCEL"
    BLATT = "BLATT"
    LINIEN = "LINIEN"
    TYP_FELD = "TYP_FELD"
    ACHSE_WERT = "ACHSE_WERT"
    KM_START = "KM_START"
    KM_STEIGEND = "KM_STEIGEND"
    KM_PUNKTE = "KM_PUNKTE"
    KM_PUNKTE_FELD = "KM_PUNKTE_FELD"
    FLIESS_KM_STEIGEND = "FLIESS_KM_STEIGEND"
    METHODE = "METHODE"
    SUCHWEITE = "SUCHWEITE"
    VERDICHTUNG = "VERDICHTUNG"
    DGM1 = "DGM1"
    DGM1_ORDNER = "DGM1_ORDNER"
    PIXEL = "PIXEL"
    MASKE = "MASKE"
    AUTO_MASKE = "AUTO_MASKE"
    TIEFE = "TIEFE"
    AUSGABE = "AUSGABE"

    def name(self):
        return "dgm1945_gok45"

    def displayName(self):
        return "DGM 1945 rekonstruieren (GOK45)"

    def group(self):
        return "DGM 1945"

    def groupId(self):
        return "dgm1945"

    def createInstance(self):
        return Dgm1945Algorithmus()

    def shortHelpString(self):
        return (
            "Rekonstruiert die Geländeoberkante 1945 aus einer Höhentabelle (Excel/CSV) und "
            "Linien (Achse, Sohle, Berme, Deich).<br><br>"
            "<b>Tabelle</b>: Spalten Kilometrierung (z.B. 12.345 oder 12+345), Name "
            "(Sohle/Berme/Deich, optional mit 'links'/'rechts'), Höhe [m NHN], optional Seite. "
            "Die Kopfzeile wird automatisch erkannt.<br>"
            "<b>Linien</b>: Ein Attributfeld enthält den Typ (Achse/Sohle/Berme/Deich). Die "
            "Achse trägt die Kilometrierung.<br><br>"
            "Ablauf: Punkte auf den Linien setzen (Lot von der Achse), entlang der Linien "
            "verdichten, Delaunay-Triangulation -> GOK45.tif, GOK45 - 8 m -> "
            "Eindringtiefe, DGM1 - GOK45 -> Differenz, DGM1 - (GOK45 - 8 m) -> Sondiertiefe.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.EXCEL, "Höhentabelle 1945 (Excel/CSV)", extension="",
            fileFilter="Tabellen (*.xlsx *.xlsm *.xls *.ods *.csv *.txt)"))
        self.addParameter(QgsProcessingParameterString(
            self.BLATT, "Tabellenblatt (leer = erstes)", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINIEN, "Linien (Achse, Sohle, Berme, Deich)", [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterField(
            self.TYP_FELD, "Feld mit Linientyp", "typ", self.LINIEN))
        self.addParameter(QgsProcessingParameterString(
            self.ACHSE_WERT, "Wert der Flussachse im Typ-Feld", "Achse"))
        self.addParameter(QgsProcessingParameterNumber(
            self.KM_START, "km am Anfangspunkt der Achse (leer, wenn km-Punkte genutzt)",
            QgsProcessingParameterNumber.Double, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.KM_STEIGEND, "km nimmt in Digitalisierrichtung der Achse zu", True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.KM_PUNKTE, "Optional: km-Punkte zur Kalibrierung der Kilometrierung",
            [QgsProcessing.TypeVectorPoint], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.KM_PUNKTE_FELD, "Feld mit km in km-Punkten", "km", self.KM_PUNKTE, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FLIESS_KM_STEIGEND, "Fließrichtung = km steigend (für links/rechts)", True))
        self.addParameter(QgsProcessingParameterEnum(
            self.METHODE, "Punktsetzung",
            ["Lot von der Achse (Querprofil)", "Gleiche Stationierung entlang jeder Linie"], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SUCHWEITE, "Max. Abstand Achse – Linie [m]", QgsProcessingParameterNumber.Double, 1000.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VERDICHTUNG, "Verdichtung entlang der Linien [m] (0 = aus)",
            QgsProcessingParameterNumber.Double, 5.0, minValue=0))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.DGM1, "DGM1 (aktuell, ein oder mehrere Kacheln)", QgsProcessing.TypeRaster, optional=True))
        self.addParameter(QgsProcessingParameterFile(
            self.DGM1_ORDNER, "… oder Ordner mit DGM1-Kacheln", QgsProcessingParameterFile.Folder,
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.PIXEL, "Pixelgröße [m] (nur ohne DGM1)", QgsProcessingParameterNumber.Double, 1.0, minValue=0.01))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.MASKE, "Optional: Begrenzungspolygon", [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.AUTO_MASKE, "Automatisch auf Korridor zwischen äußersten Linien begrenzen", True))
        self.addParameter(QgsProcessingParameterNumber(
            self.TIEFE, "Bomben-Eindringtiefe [m]", QgsProcessingParameterNumber.Double, 8.0))
        self.addParameter(QgsProcessingParameterFolderDestination(self.AUSGABE, "Ausgabeordner"))

    def _pfad(self, parameters, name, context, feedback):
        if parameters.get(name) in (None, ""):
            return ""
        return self.parameterAsCompatibleSourceLayerPath(
            parameters, name, context, ["gpkg", "shp"], "gpkg", feedback)

    def processAlgorithm(self, parameters, context, feedback):
        kern = _kern()

        km_start = None
        if parameters.get(self.KM_START) not in (None, ""):
            km_start = self.parameterAsDouble(parameters, self.KM_START, context)
        dgm1 = [l.source().split("|")[0] for l in self.parameterAsLayerList(parameters, self.DGM1, context)]
        ordner = self.parameterAsFile(parameters, self.DGM1_ORDNER, context)
        if ordner:
            dgm1.append(ordner)

        e = kern.Einstellungen(
            excel=self.parameterAsFile(parameters, self.EXCEL, context),
            excel_blatt=self.parameterAsString(parameters, self.BLATT, context) or "",
            linien=self._pfad(parameters, self.LINIEN, context, feedback),
            linien_typ_feld=self.parameterAsFields(parameters, self.TYP_FELD, context)[0],
            achse_wert=self.parameterAsString(parameters, self.ACHSE_WERT, context),
            km_start=km_start,
            km_steigend=self.parameterAsBool(parameters, self.KM_STEIGEND, context),
            km_punkte=self._pfad(parameters, self.KM_PUNKTE, context, feedback),
            km_punkte_feld=(self.parameterAsFields(parameters, self.KM_PUNKTE_FELD, context) or ["km"])[0],
            fliessrichtung_km_steigend=self.parameterAsBool(parameters, self.FLIESS_KM_STEIGEND, context),
            methode=["lot", "entlang"][self.parameterAsEnum(parameters, self.METHODE, context)],
            suchweite=self.parameterAsDouble(parameters, self.SUCHWEITE, context),
            verdichtung=self.parameterAsDouble(parameters, self.VERDICHTUNG, context),
            dgm1=dgm1,
            pixelgroesse=self.parameterAsDouble(parameters, self.PIXEL, context),
            maske=self._pfad(parameters, self.MASKE, context, feedback),
            auto_maske=self.parameterAsBool(parameters, self.AUTO_MASKE, context),
            eindringtiefe=self.parameterAsDouble(parameters, self.TIEFE, context),
            ausgabe_ordner=self.parameterAsString(parameters, self.AUSGABE, context),
        )

        try:
            erg = kern.ausfuehren(e, log=feedback.pushInfo, fortschritt=feedback.setProgress)
        except Exception as ex:  # verständliche Meldung statt Traceback
            raise QgsProcessingException(str(ex))

        namen = {"gok45": "GOK45", "eindringtiefe": "GOK45 - %g m (Eindringtiefe)" % e.eindringtiefe,
                 "differenz": "Differenz DGM1 - GOK45", "sondiertiefe": "Sondiertiefe unter DGM1",
                 "punkte": "Punkte 1945"}
        for key, pfad in erg.items():
            context.addLayerToLoadOnCompletion(
                pfad, QgsProcessingContext.LayerDetails(namen.get(key, key), context.project(), key))
        erg[self.AUSGABE] = e.ausgabe_ordner
        return erg
