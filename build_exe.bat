@echo off
REM Erzeugt dist\Luftbild-Editor\Luftbild-Editor.exe (kein Python auf dem Zielrechner noetig).
cd /d "%~dp0"
if not exist .venv (
    py -3 -m venv .venv || python -m venv .venv
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\pyinstaller --noconfirm --windowed --name Luftbild-Editor ^
    --collect-submodules imagecodecs run_editor.py
echo.
echo Fertig: dist\Luftbild-Editor\Luftbild-Editor.exe
pause
