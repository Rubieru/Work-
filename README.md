# DGM 1945 – automatische Rekonstruktion der GOK45

Das Programm erledigt den kompletten Ablauf von der Höhentabelle bis zu den fertigen Rastern:

| Schritt | im Programm |
|---|---|
| 1 | Tabelle einlesen (Excel oder CSV). Kopfzeile und Spalten werden automatisch erkannt. |
| 2 | Kilometrierung der Achse aus der DBF übernehmen (Start-km/-m). Die Richtung wird automatisch bestimmt. |
| 3 | Punkte über **Station + Seite + Abstand** senkrecht zur Achse setzen |
| 4 | Mehrere Werte am selben Ort: Es zählt der Wert, dessen Planjahr **am nächsten an 1945** liegt |
| 5 | **Zwischenprofile** alle 5 m entlang der Achse, damit das Modell der Flusskrümmung folgt |
| 6 | Delaunay-Triangulation (linear). Flächen ohne Tabellenwerte werden mit dem **DGM1 gefüllt** → **GOK45.tif** |
| 7 | GOK45 − 8 m → **GOK45_minus_8m_Eindringtiefe.tif** (m ü. NHN) |
| 8 | DGM1 − GOK45 → **Differenz_DGM1_minus_GOK45.tif** (+ Auftrag / − Abtrag seit 1945) |
| 9 | DGM1 − (GOK45 − 8 m) → **Sondiertiefe_unter_DGM1.tif**, also die Tiefe unter heutiger GOK |

Außerdem entstehen:

* `GOK45_Quelle.tif`: 1 = Wert aus der Tabelle trianguliert, 2 = Wert aus dem DGM1 übernommen
* `Punkte_1945.gpkg`: alle Punkte mit Höhe, km, Jahr und Herkunft (`tabelle` oder `interpoliert`)
* `Korridor_Maske.gpkg` und `Auffuellbereich_DGM1.gpkg`
* `Protokoll.txt` mit allen Warnungen und den Auftrags- und Abtragsvolumen

Benötigt werden nur **GDAL und numpy**. Beides ist in jeder QGIS-Installation enthalten, SAGA ist nicht nötig.

---

## Eingangsdaten

### Höhentabelle (CSV oder Excel)

Ausschlaggebend sind die ersten Spalten:

| KILOMETRIE | SEITE | ABSTAND | HOEHE | … JAHR … | … BERGSENK … |
|---|---|---|---|---|---|
| 19800 | rechts | 40 | 37,7 | 1958 | 256,3 |
| 19800 | links | 4 | 36,2 | 1949 | 304,1 |

* **KILOMETRIE**: in m (`19800`) oder km (`19.8`, `19+800`). Die Einheit wird automatisch erkannt.
* **SEITE**: links oder rechts. Gemeint ist die Blickrichtung in Richtung steigender km (umstellbar).
* **ABSTAND**: Abstand von der Achse in m (0 = auf der Achse)
* **HOEHE**: Höhe in m ü. NHN
* **JAHR** (optional): Gibt es am selben Ort mehrere Werte, wird der Wert genommen, dessen Jahr am nächsten an 1945
  liegt. Bei Gleichstand wird gemittelt.
* **Bergsenkung** (optional): Mit `bergsenkung_spalte = BERGSENK` wird `BERGSENK × 0,01` (cm → m) von der Höhe abgezogen.

Alle anderen Spalten werden ignoriert. Die CSV darf Semikolon und Dezimalkomma verwenden. Die Kodierung kann
UTF-8 oder Windows-1252 sein.

*Alternative:* Statt eines Abstands kann die Tabelle Namen (Sohle/Berme/Deich) enthalten. Dann werden die Punkte auf
gleichnamige Linien im Linien-Layer gesetzt (`methode = lot`).

### Flussachse (Shape mit `.shp`, `.shx`, `.dbf`, `.prj`)

* eine durchgehende Linie
* **Start-km/-m** steht in der DBF. Gesucht werden automatisch Felder wie `KM`, `START_M`, `KM_START`, `STATION` oder
  `VON_KM`. Ein abweichender Feldname lässt sich mit `achse_km_feld` festlegen, oder man gibt `km_start` von Hand an.
* Ob die km in Digitalisierrichtung steigen oder fallen, erkennt das Programm daran, welche Richtung zu den
  Tabellen-Stationen passt (oder aus einem Ende-Feld wie `KM_ENDE` oder `END_M`).
* Fehlt die `.prj`, wird das Koordinatensystem des DGM1 übernommen.

### DGM1

Eine oder mehrere Kacheln oder ein ganzer Ordner. Die GOK45 wird **pixelgenau auf das DGM1-Raster ausgerichtet**.

---

## Variante A: Werkzeug in der QGIS-Toolbox

1. `dgm1945_kern.py` **und** `dgm1945_qgis.py` in den Processing-Skriptordner kopieren.
   Den Ordner findet man unter QGIS → *Einstellungen → Optionen → Verarbeitung → Skripte*.
   Unter Windows ist das meist `%APPDATA%\QGIS\QGIS3\profiles\default\processing\scripts`.
2. QGIS neu starten oder in der Verarbeitungswerkzeugkiste *Skripte → Skripte neu laden* wählen.
3. *Skripte → DGM 1945 → DGM 1945 rekonstruieren (GOK45)* öffnen, die Tabelle, die Achse und das DGM1 wählen
   und starten. Die Ergebnisse werden automatisch ins Projekt geladen.

Das Werkzeug lässt sich auch als Stapelprozess oder im Grafischen Modellierer verwenden.

## Variante B: ohne QGIS-Oberfläche

1. `beispiel_konfiguration.ini` kopieren und anpassen.
2. Die INI-Datei per Drag & Drop auf `DGM1945_starten.bat` ziehen oder in der OSGeo4W-Shell
   `python dgm1945_kern.py projekt.ini` aufrufen.

---

## Wie die Zwischenprofile funktionieren

Querprofile liegen oft 50 bis 500 m auseinander. Eine einfache Triangulation würde sie geradlinig verbinden und
dabei in Flussbögen die Rinne „abschneiden“. Das Programm erzeugt deshalb alle `verdichtung` m (Standard 5 m)
ein Zwischenprofil senkrecht zur Achse:

* Für jeden Achsabstand wird zwischen dem nächsten Profil davor und dem nächsten Profil danach interpoliert,
  die diesen Abstand enthalten.
* Fehlt in einem Profil z. B. die rechte Seite, überbrückt das nächste vollständige Profil diese Lücke.
* Wo auf einer Seite gar keine Werte vorliegen, wird nicht trianguliert. Dort wird das DGM1 eingesetzt.

## Wichtige Optionen

| Option | Standard | Bedeutung |
|---|---|---|
| `bergsenkung_spalte` | – | Spalte mit Bergsenkung (z. B. `BERGSENK`), wird × `bergsenkung_faktor` von der Höhe abgezogen |
| `duplikate` | `jahr` | `jahr` = Jahr am nächsten an `bezugsjahr`, `mittel`, `alle` |
| `verdichtung` | 5 m | Abstand der Zwischenprofile (0 = aus) |
| `dgm1_auffuellen` | ja | Flächen ohne Tabellenwerte mit DGM1 füllen (Differenz dort = 0) |
| `auffuell_puffer` | 20 m | Füllbereich = Achse ± (größter Abstand + Puffer), alternativ `maske` |
| `fliessrichtung_km_steigend` | ja | links/rechts in Richtung steigender km; `nein` = in Richtung fallender km |
| `eindringtiefe` | 8 m | Abschlag für die maximale Bomben-Eindringtiefe |

## Test

```
python tests/test_kette.py
```

Der Test erzeugt synthetische Daten (gekrümmter Fluss, DGM1 mit bekanntem Auftrag von +1 m) und prüft, ob die Kette
diese Werte reproduziert. Geprüft werden:

* Excel und CSV
* km-Kalibrierung
* Station + Abstand mit Start-m aus der DBF und automatischer Richtung
* Auswahl nach Jahr
* DGM1-Auffüllung
