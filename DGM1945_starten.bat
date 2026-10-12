@echo off
REM ------------------------------------------------------------------
REM  DGM 1945 ohne QGIS-Oberfläche starten (nutzt das Python von QGIS)
REM  Aufruf: Konfigurationsdatei(en) per Drag & Drop auf diese Datei ziehen
REM          oder: DGM1945_starten.bat projekt.ini
REM  QGIS_PFAD bei Bedarf an die eigene Installation anpassen.
REM ------------------------------------------------------------------
set "QGIS_PFAD="
for /d %%D in ("C:\Program Files\QGIS*") do set "QGIS_PFAD=%%D"
if exist "C:\OSGeo4W\bin\o4w_env.bat" if not defined QGIS_PFAD set "QGIS_PFAD=C:\OSGeo4W"
if not defined QGIS_PFAD (
  echo QGIS nicht gefunden - bitte QGIS_PFAD in dieser Datei setzen.
  pause & exit /b 1
)
call "%QGIS_PFAD%\bin\o4w_env.bat"
python "%~dp0dgm1945_kern.py" %*
pause
