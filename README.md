# DGM 1945 – automatische Rekonstruktion der GOK45

Das Programm erledigt den kompletten Ablauf von der Excel-Tabelle bis zu den fertigen Rastern:

| Schritt | bisher manuell (QGIS/SAGA) | im Programm |
|---|---|---|
| 1 | Excel-Tabelle aufbereiten | Tabelle einlesen, Kopfzeile und Spalten automatisch erkennen |
| 2 | Punkte nach Kilometrierung an die Linien setzen | Lot von der Flussachse am jeweiligen km, Schnitt mit Sohle-, Berme- und Deichlinie |
| 3 | – | Verdichtung entlang der Linien, damit die Linien als Bruchkanten wirken |
| 4 | TIN-Interpolation | Delaunay-Triangulation (linear) → **GOK45.tif** |
| 5 | Rasterrechner DGM1 − GOK45 | → **Differenz_DGM1_minus_GOK45.tif** (+ Auftrag / − Abtrag) |
| 6 | GOK45 − 8 m | → **GOK45_minus_8m_Eindringtiefe.tif** (m ü. NHN) |
| 7 | – | → **Sondiertiefe_unter_DGM1.tif** = DGM1 − (GOK45 − 8 m), also die Tiefe unter heutiger GOK |

Außerdem werden `Punkte_1945.gpkg` (alle gesetzten Punkte mit Höhe und km), `Korridor_Maske.gpkg`
und `Protokoll.txt` erzeugt. Das Protokoll enthält Warnungen sowie die Auftrags- und Abtragsvolumen.

Benötigt werden nur **GDAL und numpy**. Beides ist in jeder QGIS-Installation enthalten, SAGA ist nicht nötig.

---

## Eingangsdaten

### Höhentabelle (Excel `.xlsx/.xls` oder CSV)

| Fluss-km | Bezeichnung | Höhe [m NHN] | Seite (optional) |
|---|---|---|---|
| 12.000 | Sohle | 41,20 | |
| 12.000 | Berme | 44,85 | |
| 12+000 | Deich | 48,30 | links |
| 12.000 | Deich re. | 48,10 | |

* **Kopfzeile** wird automatisch gesucht, auch wenn darüber Titelzeilen stehen. Erkannt werden u. a.
  *km, Station, Fluss-km* / *Name, Bezeichnung, Typ* / *Höhe, NHN, Z* / *Seite, Ufer*.
  Bei abweichenden Namen lassen sie sich in der INI-Datei festlegen (`spalte_km` …).
* **km** als `12.345`, `12,345` oder `12+345`.
* **Seite** in einer eigenen Spalte oder im Namen (`Deich links`, `Deich li.`, `Deich re`).
  Ohne Seitenangabe wird der Wert auf **alle** Linien dieses Typs gesetzt, also beidseitig.
  Links und rechts gelten in Fließrichtung.

### Linien (Shape oder GeoPackage)

Ein Linien-Layer mit einem Attributfeld `typ`. Das Feld ist frei wählbar und enthält die Werte:

* `Achse`: die kilometrierte Flussachse (genau eine durchgehende Linie)
* `Sohle`, `Berme`, `Deich`: beliebig viele Linien, z. B. je eine links und eine rechts

Die Namen in der Tabelle und im Typ-Feld werden ohne Rücksicht auf Groß- und Kleinschreibung oder
Umlaute verglichen. Deshalb passt z. B. `Deichkrone` auch zu `Deich`.

**Kilometrierung der Achse**: entweder `km_start`, also der km am ersten Stützpunkt der Achse,
zusammen mit der Richtung, oder ein **Punkt-Layer mit km-Steinen** (`km_punkte`). Die zweite Variante
ist genauer, wenn die amtliche Kilometrierung nicht exakt der digitalisierten Achslänge entspricht.

### DGM1 (optional)

Eine oder mehrere Kacheln (GeoTIFF, XYZ, ASC …) oder ein ganzer Ordner. Die GOK45 wird **pixelgenau
auf das DGM1-Raster ausgerichtet**, sodass die Differenz ohne Verschiebung berechnet wird.

---

## Variante A: Werkzeug in der QGIS-Toolbox

1. `dgm1945_kern.py` **und** `dgm1945_qgis.py` in den Processing-Skriptordner kopieren.
   Den Ordner findet man unter QGIS → *Einstellungen → Optionen → Verarbeitung → Skripte*.
   Unter Windows ist das meist
   `%APPDATA%\QGIS\QGIS3\profiles\default\processing\scripts`.
2. QGIS neu starten oder in der Verarbeitungswerkzeugkiste *Skripte → Skripte neu laden* wählen.
3. *Skripte → DGM 1945 → DGM 1945 rekonstruieren (GOK45)* öffnen, die Eingaben wählen und starten.
   Die Ergebnisse werden automatisch ins Projekt geladen.

Das Werkzeug lässt sich auch im **Stapelprozess** (Rechtsklick → *Als Batchprozess ausführen*)
oder im **Grafischen Modellierer** verwenden.

## Variante B: ohne QGIS-Oberfläche (Konfigurationsdatei)

1. `beispiel_konfiguration.ini` kopieren und für das Projekt anpassen.
2. Die INI-Datei per Drag & Drop auf `DGM1945_starten.bat` ziehen (Windows) oder in der OSGeo4W-Shell
   ```
   python dgm1945_kern.py projekt.ini
   ```
   aufrufen. Mehrere INI-Dateien werden nacheinander abgearbeitet.

---

## Wichtige Optionen

| Option | Standard | Bedeutung |
|---|---|---|
| `methode` | `lot` | `lot`: Punkt liegt dort, wo das Lot von der Achse am km die Linie schneidet (klassisches Querprofil). `entlang`: gleiche Stationierung entlang jeder Linie |
| `verdichtung` | 5 m | Zwischenpunkte entlang jeder Linie, linear zwischen den Tabellenwerten interpoliert. So folgt das TIN den Linien als Bruchkanten. `0` = nur Tabellenpunkte |
| `auto_maske` | ja | Begrenzt das TIN auf den Korridor zwischen den äußersten Linien. Ohne diese Begrenzung würde die Triangulation die konvexe Hülle füllen und z. B. die Innenseite von Flussbögen überbrücken |
| `maske` | – | eigenes Begrenzungspolygon statt der automatischen Maske |
| `eindringtiefe` | 8 m | Abschlag für die maximale Bomben-Eindringtiefe |
| `suchweite` | 1000 m | maximaler Abstand Achse → Linie |

## Test

```
python tests/test_kette.py
```

Der Test erzeugt einen synthetischen, gekrümmten Fluss mit Sohle, Berme und Deich, eine Excel-Tabelle
und zwei DGM1-Kacheln mit bekanntem Auftrag von +1 m. Anschließend prüft er, ob die ganze Kette exakt
diese Werte reproduziert. Geprüft werden Excel, CSV, km-Kalibrierung, Excel ohne openpyxl und die
Methode „entlang“. Für die Testdaten werden zusätzlich `openpyxl` und `scipy` gebraucht.
