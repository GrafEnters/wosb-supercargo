@echo off
cd /d "%~dp0"
rem Without arguments: the map window, no console.
if "%~1"=="" (
    start "" ".venv\Scripts\pythonw.exe" -m supercargo
    exit /b
)
chcp 65001 >nul
.venv\Scripts\python -m supercargo %*
if errorlevel 1 pause
