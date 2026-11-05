# DGM 1945 – automatische Rekonstruktion der GOK45

Das Programm erledigt den kompletten Ablauf: Höhendaten 1945 einlesen, GOK45 triangulieren, mit dem aktuellen
DGM1 verrechnen und die Bomben-Eindringtiefe ableiten. Benötigt werden nur **GDAL und numpy**. Beides ist in
jeder QGIS-Installation enthalten.

## Ablauf

1. **Alle Daten abfragen** (zuerst, vor jeder Berechnung):
   * **Höhenpunkte 1945**: Punkt-Shape mit Höhe. Das Höhenfeld wird automatisch erkannt (`HOEHE_FG`, `POINT_Z`,
     `HOEHE_BS`, `HOEHE` …).
   * **Geländelinien**: Linien, entlang derer zwischen den Punkten interpoliert wird
   * **Aktuelles DGM1**: ein fertiges Mosaik **oder** alle Einzelkacheln. Aus Kacheln baut das Programm selbst ein
     Mosaik (`DGM1_Mosaik.tif`).
   * **Untersuchungsgebiet** (Polygon, empfohlen)
   * **Sohle** (optional): Sohllinien mit Höhen, die in das DGM1 eingebrannt werden
2. Optional: **Sohle in das DGM1 einbrennen**, denn das DGM1 zeigt nur die Wasseroberfläche → `DGM1_mit_Sohle.tif`
3. **Geländelinien verdichten**: alle 1 m ein Punkt. Die Höhe wird linear zwischen den Höhenpunkten interpoliert,
   die auf der Linie liegen. Linien mit Höheninfo `DGM` oder `Sohle` (Feld `Hoe_Inf`) bekommen ihre Höhen aus dem
   DGM1.
4. **Triangulation** (Delaunay, linear). Dreiecke mit Kanten über 50 m werden verworfen. Dort und überall sonst ohne
   Daten 1945 wird das DGM1 übernommen → **GOK45.tif**
5. GOK45 − 8 m → **GOK45_minus_8m_Eindringtiefe.tif** (m ü. NHN)
6. DGM1 − GOK45 → **Differenz_DGM1_minus_GOK45.tif** (+ Auftrag / − Abtrag seit 1945)
7. DGM1 − (GOK45 − 8 m) → **Sondiertiefe_unter_DGM1.tif** (Tiefe unter heutiger GOK)

Außerdem entstehen:

* `GOK45_Quelle.tif`: 1 = aus den Daten 1945 trianguliert, 2 = aus dem DGM1 übernommen
* `Punkte_1945.gpkg`: alle verwendeten Punkte mit Herkunft
* `Protokoll.txt`: Warnungen, z. B. wenn das DGM1 die Fläche nicht vollständig abdeckt, sowie Auftrags- und
  Abtragsvolumen
* `Einstellungen.ini`: zum erneuten Rechnen

## Starten

### In QGIS (Verarbeitungswerkzeugkiste)

1. `dgm1945_kern.py` **und** `dgm1945_qgis.py` in den Processing-Skriptordner kopieren.
   Den Ordner findet man unter QGIS → *Einstellungen → Optionen → Verarbeitung → Skripte*.
   Unter Windows ist das meist `%APPDATA%\QGIS\QGIS3\profiles\default\processing\scripts`.
2. QGIS neu starten oder in der Werkzeugkiste *Skripte → Skripte neu laden* wählen.
3. *Skripte → DGM 1945 → DGM 1945 rekonstruieren (GOK45)* öffnen. Der Dialog fragt oben nacheinander die Daten ab:
   1. Höhenpunkte
   2. Geländelinien
   3. DGM1 (Mosaik oder Kacheln)
   4. Untersuchungsgebiet
   5. Sohle

   Feineinstellungen stehen unter „Erweiterte Parameter“. Die Ergebnisse werden automatisch ins Projekt geladen.

### Ohne QGIS-Oberfläche (Assistent)

Doppelklick auf `DGM1945_starten.bat`. Der Assistent fragt **nacheinander alle Daten per Dateiauswahl** ab. Beim
DGM1 kann man entweder das Mosaik wählen oder alle Kacheln auf einmal markieren. Danach zeigt er eine Übersicht
und startet nach Bestätigung. Die Auswahl wird als `Einstellungen.ini` im Ausgabeordner gespeichert. Diese Datei
per Drag & Drop auf `DGM1945_starten.bat` ziehen, um genau so erneut zu rechnen.

## Eingangsdaten im Detail

| Daten | Hinweise |
|---|---|
| Höhenpunkte 1945 | Punkt-Shape/GPKG. Höhenfeld automatisch, sonst festlegen (`punkte_hoehe_feld`). Bei mehreren Werten am selben Ort wird der Wert genommen, dessen Jahr (`JAHR`) am nächsten an 1945 liegt. |
| Geländelinien | Ein Punkt gilt als „auf der Linie“ bis 10 cm Abstand (`fang_toleranz`). Über den letzten Punkt einer Linie hinaus bleibt die Höhe konstant. Linien ohne einen einzigen Höhenpunkt bekommen ihre Höhen aus dem DGM1 (steht im Protokoll). |
| DGM1 | Mosaik oder Kacheln (GeoTIFF, XYZ, ASC …) oder ein Ordner. Das Ergebnisraster wird pixelgenau auf das DGM1 ausgerichtet. Deckt das DGM1 die Fläche nicht ganz ab, steht eine Warnung im Protokoll. |
| Untersuchungsgebiet | legt die Ergebnisfläche fest. Ohne Untersuchungsgebiet gilt: Puffer von 20 m um alle Punkte und Linien, von Linien umschlossene Flächen werden mitgenommen. |
| Sohle | Linien mit Z-Werten oder einem Höhenfeld (oder Punkte). Im Streifen ± `sohle_puffer` (halbe Sohlbreite) wird das DGM1 auf Sohlhöhe gesetzt. Mit einer **Wasserfläche** (Polygon) wird zusätzlich die Böschung bis zum Ufer interpoliert. Standardmäßig wird das DGM1 nur dort ersetzt, wo die Sohle tiefer liegt. |

### Alternative: Tabelle + Achse

Statt eines Punkt-Shapes kann auch die Höhentabelle eingelesen werden. Erwartet werden die Spalten
Kilometrierung (bzw. Station), Seite (bzw. Flussachse), Abstand und Höhe. Die Punkte werden dann senkrecht zur
Achse gesetzt.

* Der Start-km der Achse steht in der DBF oder wird mit `km_start` angegeben.
* **links/rechts** gelten standardmäßig in Blickrichtung **fallender km**, also in Fließrichtung, wenn die km wie
  an der Emscher von der Mündung aus gezählt werden.

## Test

```
python tests/test_kette.py
```

Der Test erzeugt synthetische Daten (gekrümmter Fluss, DGM1 mit bekanntem Auftrag von +1 m) und prüft alle Wege:

* Excel und CSV
* km-Kalibrierung
* Tabelle + Achse
* Punkt-Layer + Geländelinien
* Mosaik aus Kacheln
* Sohle einbrennen

### Beta-Test mit Originaldaten (Emscher Los 4)

Geprüft mit `2026_08_11_HP_geeignet_fg_Pan.shp`, `2026_09_11_Pan_Gelaendelinien.shp` und
`DGM1_NRW_merge_mit_Sohl_C.tif` gegen das manuell erstellte `dgm_1945_GS_v6_clip.tif`:

* **88,9 % der triangulierten Pixel sind auf 1 cm identisch**
* 93 % liegen innerhalb von 10 cm, 97 % innerhalb von 50 cm

Die Restabweichungen liegen an Linien mit eigener Sohlhöhe („gepufferte Emschersohle“) und an Linien mit der
Höheninfo „DGM“.
