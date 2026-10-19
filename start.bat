@echo off
REM Startet den Luftbild-Editor (richtet beim ersten Mal Python-Pakete ein).
cd /d "%~dp0"
if not exist .venv (
    echo Erster Start: Python-Umgebung wird eingerichtet ...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw -m luftbild_editor %*
