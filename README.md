# Luftbild-Editor

Ein einfaches Windows-Programm zum Bearbeiten **historischer Luftbilder** (Schwarz-Weiß / Graustufen):
pixelgenau zuschneiden, Kontrast per **Histogrammstreckung** verbessern und **schärfen**, mit
Live-Vorschau und Voreinstellungen zum schnellen Ausprobieren.

![Verbessern](docs/verbessern.png)

## Bedienung in drei Schritten

1. **Zuschneiden**: Rahmen mit der Maus aufziehen oder X, Y, Breite und Höhe in Pixeln eingeben.
   Beim Hineinzoomen (Mausrad) erscheint ein Pixelraster. Eine Drehung von ±10° mit Raster
   hilft, das Bild gerade auszurichten.
2. **Verbessern**: Links eine Voreinstellung anklicken. Jede Kachel zeigt das eigene Bild mit
   dieser Einstellung. Rechts lässt sich alles fein nachregeln. Die Vorschau ändert sich sofort.
   Leertaste gedrückt halten zeigt das Original.
3. **Bild speichern**: Das Bild wird in voller Auflösung berechnet, als TIFF 16/8 Bit, PNG oder
   JPEG. Bei TIFF bleibt die dpi-Angabe erhalten.

| Zuschneiden | Detailansicht 100 % |
|---|---|
| ![Zuschneiden](docs/zuschneiden.png) | ![100 %](docs/detail_100.png) |

## Werkzeuge

| Bereich | Regler | Wozu |
|---|---|---|
| Kontrast | **Histogramm strecken**, Schwarz/Weiß abschneiden | Spreizt flaue Grauwerte auf den vollen Bereich |
| | Mitteltöne | Mittlere Grautöne heller oder dunkler |
| | Lokaler Kontrast | Hebt Felder, Wald und Bebauung voneinander ab |
| Schärfen | Stärke, Radius, Schwelle | Unscharf maskieren. Die Schwelle verhindert, dass Filmkorn mitgeschärft wird |
| Bildfehler | Filmkorn reduzieren | Kantenerhaltendes Glätten |
| | Ausleuchtung ausgleichen | Gleicht dunkle Ränder und Vignettierung aus |
| | Negativ umkehren | Für gescannte Negative |

Die Voreinstellungen sind: Original, Standard, Kräftig, Flaues Bild, Ungleich belichtet,
Körnig/verrauscht, Feine Details, Dunkler Scan und Negativ. Eigene Voreinstellungen lassen
sich mit „Aktuelle speichern…“ anlegen.

Weitere Funktionen:

- Mehrere Bilder öffnen, auch per Drag & Drop.
- Rückgängig/Wiederholen mit Strg+Z und Strg+Y.
- *Bearbeiten → Regler-Einstellungen auf alle Bilder übertragen* (für eine ganze Befliegung).
- *Datei → Alle Bilder speichern*.

Große Scans werden für die Vorschau verkleinert berechnet. Beim Hineinzoomen und beim Speichern
rechnet das Programm in voller Auflösung, und zwar so, dass Vorschau und Ergebnis übereinstimmen.

## Installation unter Windows

**Variante A: fertige .exe.** Unter *GitHub → Actions → „Windows-Programm bauen“* das Artefakt
`Luftbild-Editor-Windows` herunterladen, entpacken und `Luftbild-Editor.exe` starten. Python wird
nicht benötigt.

**Variante B: mit Python.** Python 3.10 oder neuer von python.org installieren, dann `start.bat`
doppelklicken. Beim ersten Start werden die Pakete eingerichtet.

Eine eigene .exe lässt sich mit `build_exe.bat` erzeugen.

## Entwicklung

```
pip install -r requirements.txt pytest
python -m luftbild_editor           # Programm starten
python -m pytest -q                 # Tests
```

Aufbau:

- `luftbild_editor/processing.py`: Bildverarbeitung (ohne Oberfläche, getestet)
- `luftbild_editor/widgets.py`: Bildansicht mit Zuschneiderahmen, Regler, Histogramm
- `luftbild_editor/main_window.py`: Hauptfenster
- `luftbild_editor/presets.py`: Voreinstellungen
