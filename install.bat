@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   Суперкарго: готовлю всё к отплытию...
echo.

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY python --version >nul 2>&1 && set "PY=python"
if not defined PY (
    echo   Не нашёл Python. Поставь Python 3.13 с python.org
    echo   ^(при установке отметь галочку "Add python.exe to PATH"^) и запусти меня снова.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo   Не получилось поставить библиотеки. Проверь интернет и запусти меня снова.
    pause
    exit /b 1
)

rem A shortcut that opens the logbook without a console window
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%~dp0Суперкарго.lnk'); $s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='-m supercargo'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0supercargo\icon.ico'; $s.Save()"

echo.
echo   Готово! Запускай ярлык «Суперкарго» в этой папке. Попутного ветра!
echo.
pause
