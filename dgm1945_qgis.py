# -*- coding: utf-8 -*-
"""
QGIS-Processing-Werkzeug „DGM 1945 rekonstruieren (GOK45)“

Installation: Diese Datei UND dgm1945_kern.py in den Processing-Skriptordner
kopieren (QGIS: Einstellungen > Optionen > Verarbeitung > Skripte, unter Windows
meist %APPDATA%\\QGIS\\QGIS3\\profiles\\default\\processing\\scripts) und QGIS
neu starten oder in der Toolbox „Skripte neu laden“. Das Werkzeug erscheint unter
Skripte > DGM 1945.
"""

import importlib
import os
import sys

from qgis.core import (QgsProcessing, QgsProcessingAlgorithm, QgsProcessingContext,
                       QgsProcessingException, QgsProcessingParameterBoolean,
                       QgsProcessingParameterDefinition, QgsProcessingParameterEnum,
                       QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
                       QgsProcessingParameterFile, QgsProcessingParameterFolderDestination,
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
    return importlib.reload(dgm1945_kern)   # nach einem Update ohne QGIS-Neustart wirksam


class Dgm1945Algorithmus(QgsProcessingAlgorithm):
    # --- Daten (werden zuerst abgefragt) ---
    PUNKTE = "PUNKTE"
    PUNKTE_HOEHE_FELD = "PUNKTE_HOEHE_FELD"
    GELAENDELINIEN = "GELAENDELINIEN"
    LINIEN_HOEHE_FELD = "LINIEN_HOEHE_FELD"
    DGM1 = "DGM1"
    DGM1_ORDNER = "DGM1_ORDNER"
    UNTERSUCHUNGSGEBIET = "UNTERSUCHUNGSGEBIET"
    SOHLE = "SOHLE"
    SOHLE_HOEHE_FELD = "SOHLE_HOEHE_FELD"
    SOHLE_PUFFER = "SOHLE_PUFFER"
    GEWAESSER = "GEWAESSER"
    EXCEL = "EXCEL"
    ACHSE = "ACHSE"
    AUSGABE = "AUSGABE"
    # --- Erweitert ---
    MAX_KANTE = "MAX_KANTE"
    LINIEN_ABSTAND = "LINIEN_ABSTAND"
    FANG_TOLERANZ = "FANG_TOLERANZ"
    AUFFUELL_PUFFER = "AUFFUELL_PUFFER"
    TIEFE = "TIEFE"
    BEZUGSJAHR = "BEZUGSJAHR"
    KM_START = "KM_START"
    KM_STEIGEND = "KM_STEIGEND"
    LINKS_BEZUG = "LINKS_BEZUG"
    BERGSENKUNG = "BERGSENKUNG"
    MOSAIK_SPEICHERN = "MOSAIK_SPEICHERN"

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
            "Rekonstruiert die Geländeoberkante 1945 (GOK45) und berechnet Differenz zum DGM1, "
            "Bomben-Eindringtiefe (GOK45 − 8 m) und Sondiertiefe.<br><br>"
            "<b>Daten</b><br>"
            "• <b>Höhenpunkte 1945</b>: verortete Punkte mit Höhe (Feld HOEHE_FG o.ä. wird automatisch "
            "erkannt).<br>"
            "• <b>Geländelinien</b>: entlang jeder Linie wird zwischen den Höhenpunkten interpoliert; Linien mit "
            "Höheninfo „DGM“ oder „Sohle“ (Feld Hoe_Inf) erhalten die Höhen aus dem DGM1.<br>"
            "• <b>DGM1</b>: ein fertiges Mosaik ODER alle Einzelkacheln (bzw. ein Ordner) – das Mosaik wird "
            "automatisch gebaut. Es muss die ganze Fläche abdecken (sonst Warnung im Protokoll).<br>"
            "• <b>Untersuchungsgebiet</b> (empfohlen): legt die Ergebnisfläche fest; dort, wo keine Daten 1945 "
            "vorliegen, wird das DGM1 übernommen.<br>"
            "• <b>Sohle</b> (optional): Sohllinien mit Z-Werten oder Höhenfeld werden mit Puffer in das DGM1 "
            "eingebrannt (DGM1 zeigt sonst die Wasseroberfläche); optional Wasserfläche für die Böschung.<br>"
            "• Alternativ zu den Höhenpunkten: Tabelle (Kilometrierung/Seite/Abstand/Höhe) + Achse.<br><br>"
            "Dreiecke mit Kanten über „max. Kantenlänge“ werden verworfen – dort gilt das DGM1.")

    def _neu(self, p, erweitert=False):
        if erweitert:
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)

    def initAlgorithm(self, config=None):
        n = self._neu
        D, I = QgsProcessingParameterNumber.Double, QgsProcessingParameterNumber.Integer
        n(QgsProcessingParameterFeatureSource(
            self.PUNKTE, "1. Höhenpunkte 1945 (Punkt-Layer)", [QgsProcessing.TypeVectorPoint], optional=True))
        n(QgsProcessingParameterField(
            self.PUNKTE_HOEHE_FELD, "   Höhenfeld der Punkte (leer = automatisch, z.B. HOEHE_FG)", None,
            self.PUNKTE, QgsProcessingParameterField.Numeric, optional=True))
        n(QgsProcessingParameterFeatureSource(
            self.GELAENDELINIEN, "2. Geländelinien", [QgsProcessing.TypeVectorLine], optional=True))
        n(QgsProcessingParameterField(
            self.LINIEN_HOEHE_FELD, "   Feld mit Höheninfo (DGM/Sohle) (leer = automatisch Hoe_Inf)", None,
            self.GELAENDELINIEN, optional=True))
        n(QgsProcessingParameterMultipleLayers(
            self.DGM1, "3. Aktuelles DGM1 – fertiges Mosaik oder alle Einzelkacheln", QgsProcessing.TypeRaster,
            optional=True))
        n(QgsProcessingParameterFile(
            self.DGM1_ORDNER, "   … oder Ordner mit DGM1-Kacheln", QgsProcessingParameterFile.Folder, optional=True))
        n(QgsProcessingParameterFeatureSource(
            self.UNTERSUCHUNGSGEBIET, "4. Untersuchungsgebiet (Polygon, empfohlen)",
            [QgsProcessing.TypeVectorPolygon], optional=True))
        n(QgsProcessingParameterFeatureSource(
            self.SOHLE, "5. Sohle für das DGM1 (Linien mit Z/Höhenfeld oder Punkte, optional)",
            [QgsProcessing.TypeVectorLine, QgsProcessing.TypeVectorPoint], optional=True))
        n(QgsProcessingParameterField(
            self.SOHLE_HOEHE_FELD, "   Höhenfeld der Sohle (leer = Z-Werte / automatisch)", None, self.SOHLE,
            QgsProcessingParameterField.Numeric, optional=True))
        n(QgsProcessingParameterNumber(self.SOHLE_PUFFER, "   Halbe Sohlbreite [m]", D, 2.5, minValue=0))
        n(QgsProcessingParameterFeatureSource(
            self.GEWAESSER, "   Wasserfläche für die Böschung (Polygon, optional)",
            [QgsProcessing.TypeVectorPolygon], optional=True))
        n(QgsProcessingParameterFile(
            self.EXCEL, "Alternativ zu 1.: Höhentabelle (Kilometrierung/Seite/Abstand/Höhe)",
            extension="", fileFilter="Tabellen (*.xlsx *.xlsm *.xls *.ods *.csv *.txt)", optional=True))
        n(QgsProcessingParameterFeatureSource(
            self.ACHSE, "   … mit Flussachse (Kilometrierung in der DBF)", [QgsProcessing.TypeVectorLine],
            optional=True))
        n(QgsProcessingParameterFolderDestination(self.AUSGABE, "Ausgabeordner"))

        # erweiterte Einstellungen
        n(QgsProcessingParameterNumber(self.MAX_KANTE, "Max. Kantenlänge der Dreiecke [m] (0 = aus)", D, 50.0,
                                       minValue=0), True)
        n(QgsProcessingParameterNumber(self.LINIEN_ABSTAND, "Punktabstand entlang der Geländelinien [m]", D, 1.0,
                                       minValue=0.1), True)
        n(QgsProcessingParameterNumber(self.FANG_TOLERANZ, "Punkt liegt auf Linie bis [m]", D, 0.1,
                                       minValue=0), True)
        n(QgsProcessingParameterNumber(self.AUFFUELL_PUFFER,
                                       "Ohne Untersuchungsgebiet: Puffer um Punkte/Linien [m]", D, 20.0,
                                       minValue=0), True)
        n(QgsProcessingParameterNumber(self.TIEFE, "Bomben-Eindringtiefe [m]", D, 8.0), True)
        n(QgsProcessingParameterNumber(self.BEZUGSJAHR, "Mehrere Werte am selben Ort: Jahr am nächsten an", I,
                                       1945), True)
        n(QgsProcessingParameterNumber(self.KM_START, "Tabelle: km am Anfang der Achse (leer = aus DBF)", D,
                                       optional=True), True)
        n(QgsProcessingParameterEnum(self.KM_STEIGEND, "Tabelle: km in Digitalisierrichtung",
                                     ["automatisch", "steigend", "fallend"], defaultValue=0), True)
        n(QgsProcessingParameterEnum(self.LINKS_BEZUG, "Tabelle: links/rechts gelten in Blickrichtung",
                                     ["fallender km (Fließrichtung, Emscher)", "steigender km"], defaultValue=0),
          True)
        n(QgsProcessingParameterString(self.BERGSENKUNG, "Tabelle: Spalte Bergsenkung [cm] abziehen (z.B. BERGSENK)",
                                       optional=True), True)
        n(QgsProcessingParameterBoolean(self.MOSAIK_SPEICHERN, "Aus Kacheln gebautes Mosaik als GeoTIFF speichern",
                                        True), True)

    def _pfad(self, parameters, name, context, feedback):
        if parameters.get(name) in (None, ""):
            return ""
        return self.parameterAsCompatibleSourceLayerPath(
            parameters, name, context, ["gpkg", "shp"], "gpkg", feedback)

    def _feld(self, parameters, name, context):
        return (self.parameterAsFields(parameters, name, context) or [""])[0]

    def processAlgorithm(self, parameters, context, feedback):
        kern = _kern()
        punkte = self._pfad(parameters, self.PUNKTE, context, feedback)
        excel = self.parameterAsFile(parameters, self.EXCEL, context)
        achse = self._pfad(parameters, self.ACHSE, context, feedback)
        if not punkte and not excel:
            raise QgsProcessingException("Bitte Höhenpunkte 1945 (1.) oder eine Höhentabelle mit Achse angeben.")
        if excel and not punkte and not achse:
            raise QgsProcessingException("Zur Höhentabelle wird die Flussachse benötigt.")
        dgm1 = [l.source().split("|")[0] for l in self.parameterAsLayerList(parameters, self.DGM1, context)]
        ordner = self.parameterAsFile(parameters, self.DGM1_ORDNER, context)
        if ordner:
            dgm1.append(ordner)
        if not dgm1:
            feedback.pushWarning("Kein DGM1 angegeben – Differenz- und Sondiertiefenraster entfallen.")
        km_start = None
        if parameters.get(self.KM_START) not in (None, ""):
            km_start = self.parameterAsDouble(parameters, self.KM_START, context)

        e = kern.Einstellungen(
            ausgabe_ordner=self.parameterAsString(parameters, self.AUSGABE, context),
            punkte=punkte,
            punkte_hoehe_feld=self._feld(parameters, self.PUNKTE_HOEHE_FELD, context),
            gelaendelinien=self._pfad(parameters, self.GELAENDELINIEN, context, feedback),
            linien_hoehe_feld=self._feld(parameters, self.LINIEN_HOEHE_FELD, context),
            dgm1=dgm1,
            dgm1_mosaik_speichern=self.parameterAsBool(parameters, self.MOSAIK_SPEICHERN, context),
            untersuchungsgebiet=self._pfad(parameters, self.UNTERSUCHUNGSGEBIET, context, feedback),
            sohle=self._pfad(parameters, self.SOHLE, context, feedback),
            sohle_hoehe_feld=self._feld(parameters, self.SOHLE_HOEHE_FELD, context),
            sohle_puffer=self.parameterAsDouble(parameters, self.SOHLE_PUFFER, context),
            gewaesser=self._pfad(parameters, self.GEWAESSER, context, feedback),
            excel="" if punkte else excel,
            linien="" if punkte else achse,
            max_kante=self.parameterAsDouble(parameters, self.MAX_KANTE, context),
            linien_abstand=self.parameterAsDouble(parameters, self.LINIEN_ABSTAND, context),
            fang_toleranz=self.parameterAsDouble(parameters, self.FANG_TOLERANZ, context),
            auffuell_puffer=self.parameterAsDouble(parameters, self.AUFFUELL_PUFFER, context),
            eindringtiefe=self.parameterAsDouble(parameters, self.TIEFE, context),
            bezugsjahr=self.parameterAsInt(parameters, self.BEZUGSJAHR, context),
            km_start=km_start,
            km_steigend=[None, True, False][self.parameterAsEnum(parameters, self.KM_STEIGEND, context)],
            fliessrichtung_km_steigend=self.parameterAsEnum(parameters, self.LINKS_BEZUG, context) == 1,
            bergsenkung_spalte=self.parameterAsString(parameters, self.BERGSENKUNG, context) or "",
        )
        try:
            os.makedirs(e.ausgabe_ordner, exist_ok=True)
            kern.einstellungen_als_ini(e, os.path.join(e.ausgabe_ordner, "Einstellungen.ini"))
            erg = kern.ausfuehren(e, log=feedback.pushInfo, fortschritt=feedback.setProgress)
        except Exception as ex:  # verständliche Meldung statt Traceback
            raise QgsProcessingException(str(ex))

        namen = {"gok45": "GOK45", "eindringtiefe": "GOK45 - %g m (Eindringtiefe)" % e.eindringtiefe,
                 "differenz": "Differenz DGM1 - GOK45", "sondiertiefe": "Sondiertiefe unter DGM1",
                 "punkte": "Punkte 1945", "quelle": "GOK45 Quelle (1 = Daten 1945, 2 = DGM1)",
                 "dgm_sohle": "DGM1 mit Sohle"}
        for key, pfad in erg.items():
            context.addLayerToLoadOnCompletion(
                pfad, QgsProcessingContext.LayerDetails(namen.get(key, key), context.project(), key))
        erg[self.AUSGABE] = e.ausgabe_ordner
        return erg
