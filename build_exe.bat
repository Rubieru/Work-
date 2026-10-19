@echo off
REM Erzeugt dist\Luftbild-Editor\Luftbild-Editor.exe (kein Python auf dem Zielrechner noetig).
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller pillow
.venv\Scripts\python tools\make_icon.py
set ICON=
if exist luftbild_editor\resources\logo.ico set ICON=--icon luftbild_editor\resources\logo.ico
.venv\Scripts\pyinstaller --noconfirm --windowed --name "Luftbild-Editor PZU" %ICON% ^
    --collect-submodules imagecodecs ^
    --add-data "luftbild_editor/resources:luftbild_editor/resources" run_editor.py
echo.
echo Fertig: "dist\Luftbild-Editor PZU\Luftbild-Editor PZU.exe"
pause
