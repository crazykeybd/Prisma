@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m gui.relap5_gui
) else (
    start "" "pythonw.exe" -m gui.relap5_gui
)