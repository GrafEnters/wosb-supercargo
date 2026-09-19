@echo off
cd /d "%~dp0"
chcp 65001 >nul
.venv\Scripts\python -m supercargo %*
if errorlevel 1 pause
