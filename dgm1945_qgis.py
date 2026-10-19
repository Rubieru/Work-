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
    ACHSE_KM_FELD = "ACHSE_KM_FELD"
    BERGSENKUNG = "BERGSENKUNG"
    BERGSENKUNG_FAKTOR = "BERGSENKUNG_FAKTOR"
    BEZUGSJAHR = "BEZUGSJAHR"
    DUPLIKATE = "DUPLIKATE"
    AUFFUELLEN = "AUFFUELLEN"
    AUFFUELL_PUFFER = "AUFFUELL_PUFFER"
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
            "Rekonstruiert die Geländeoberkante 1945 aus einer Höhentabelle (Excel/CSV) und der "
            "kilometrierten Achse.<br><br>"
            "<b>Tabelle</b>: KILOMETRIE (m oder km), SEITE (links/rechts), ABSTAND zur Achse [m], "
            "HOEHE [m NHN]; optional JAHR (bei mehreren Werten am selben Ort gewinnt das Jahr am "
            "nächsten an 1945) und eine Bergsenkungsspalte. Die Kopfzeile wird automatisch erkannt.<br>"
            "<b>Achse</b>: Start-km/-m steht in der DBF (Feld KM, START_M … wird automatisch "
            "gesucht), die Richtung wird aus den Tabellen-Stationen bestimmt.<br>"
            "Alternativ: Tabelle mit Namen Sohle/Berme/Deich und passende Linien im Layer.<br><br>"
            "Ablauf: Punkte setzen, Delaunay-Triangulation, Flächen ohne Tabellenwerte mit DGM1 "
            "füllen -> GOK45.tif, GOK45 - 8 m -> Eindringtiefe, DGM1 - GOK45 -> Differenz, "
            "DGM1 - (GOK45 - 8 m) -> Sondiertiefe.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.EXCEL, "Höhentabelle 1945 (Excel/CSV)", extension="",
            fileFilter="Tabellen (*.xlsx *.xlsm *.xls *.ods *.csv *.txt)"))
        self.addParameter(QgsProcessingParameterString(
            self.BLATT, "Tabellenblatt (leer = erstes)", optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINIEN, "Flussachse (ggf. mit Sohle-/Berme-/Deich-Linien)", [QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterField(
            self.TYP_FELD, "Feld mit Linientyp (nur bei mehreren Linien)", "typ", self.LINIEN, optional=True))
        self.addParameter(QgsProcessingParameterString(
            self.ACHSE_WERT, "Wert der Flussachse im Typ-Feld", "Achse"))
        self.addParameter(QgsProcessingParameterField(
            self.ACHSE_KM_FELD, "DBF-Feld mit Start-km/-m der Achse (leer = automatisch suchen)",
            None, self.LINIEN, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.KM_START, "… oder km am Anfangspunkt der Achse von Hand",
            QgsProcessingParameterNumber.Double, optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.KM_STEIGEND, "km in Digitalisierrichtung der Achse",
            ["automatisch bestimmen", "steigend", "fallend"], defaultValue=0))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.KM_PUNKTE, "Optional: km-Punkte zur Kalibrierung der Kilometrierung",
            [QgsProcessing.TypeVectorPoint], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.KM_PUNKTE_FELD, "Feld mit km in km-Punkten", "km", self.KM_PUNKTE, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FLIESS_KM_STEIGEND, "links/rechts gelten in Richtung steigender km", True))
        self.addParameter(QgsProcessingParameterEnum(
            self.METHODE, "Punktsetzung",
            ["automatisch (Station + Abstand, wenn Spalte ABSTAND vorhanden)",
             "Station + Seite + Abstand senkrecht zur Achse",
             "Lot von der Achse auf Sohle-/Berme-/Deich-Linien",
             "Gleiche Stationierung entlang jeder Linie"], defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            self.BERGSENKUNG, "Optional: Spalte mit Bergsenkung (wird von der Höhe abgezogen, z.B. BERGSENK)",
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.BERGSENKUNG_FAKTOR, "Umrechnung Bergsenkung in m (cm -> 0.01)",
            QgsProcessingParameterNumber.Double, 0.01))
        self.addParameter(QgsProcessingParameterNumber(
            self.BEZUGSJAHR, "Mehrere Werte am selben Ort: Jahr am nächsten an",
            QgsProcessingParameterNumber.Integer, 1945))
        self.addParameter(QgsProcessingParameterEnum(
            self.DUPLIKATE, "Mehrere Werte am selben Ort",
            ["Jahr am nächsten am Bezugsjahr", "Mittelwert", "alle verwenden"], defaultValue=0))
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
            self.AUTO_MASKE, "TIN automatisch auf den Bereich der Profile begrenzen", True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.AUFFUELLEN, "Flächen ohne Tabellenwerte mit DGM1 füllen", True))
        self.addParameter(QgsProcessingParameterNumber(
            self.AUFFUELL_PUFFER, "Füllbereich: Achse ± (max. Abstand + x m)",
            QgsProcessingParameterNumber.Double, 20.0, minValue=0))
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
            linien_typ_feld=(self.parameterAsFields(parameters, self.TYP_FELD, context) or ["typ"])[0],
            achse_wert=self.parameterAsString(parameters, self.ACHSE_WERT, context),
            achse_km_feld=(self.parameterAsFields(parameters, self.ACHSE_KM_FELD, context) or [""])[0],
            km_start=km_start,
            km_steigend=[None, True, False][self.parameterAsEnum(parameters, self.KM_STEIGEND, context)],
            km_punkte=self._pfad(parameters, self.KM_PUNKTE, context, feedback),
            km_punkte_feld=(self.parameterAsFields(parameters, self.KM_PUNKTE_FELD, context) or ["km"])[0],
            fliessrichtung_km_steigend=self.parameterAsBool(parameters, self.FLIESS_KM_STEIGEND, context),
            methode=["auto", "abstand", "lot", "entlang"][self.parameterAsEnum(parameters, self.METHODE, context)],
            bergsenkung_spalte=self.parameterAsString(parameters, self.BERGSENKUNG, context) or "",
            bergsenkung_faktor=self.parameterAsDouble(parameters, self.BERGSENKUNG_FAKTOR, context),
            bezugsjahr=self.parameterAsInt(parameters, self.BEZUGSJAHR, context),
            duplikate=["jahr", "mittel", "alle"][self.parameterAsEnum(parameters, self.DUPLIKATE, context)],
            dgm1_auffuellen=self.parameterAsBool(parameters, self.AUFFUELLEN, context),
            auffuell_puffer=self.parameterAsDouble(parameters, self.AUFFUELL_PUFFER, context),
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
                 "punkte": "Punkte 1945", "quelle": "GOK45 Quelle (1 = Tabelle, 2 = DGM1)"}
        for key, pfad in erg.items():
            context.addLayerToLoadOnCompletion(
                pfad, QgsProcessingContext.LayerDetails(namen.get(key, key), context.project(), key))
        erg[self.AUSGABE] = e.ausgabe_ordner
        return erg
