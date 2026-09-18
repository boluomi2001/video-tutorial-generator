@echo off
setlocal
echo [Step 2] Keyframe + OCR + Visual Analysis
echo.

set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "SCRIPT=%~dp0step2_analyze.py"

if not "%~1"=="" (
    echo Run ID: %1
    "%PY%" -u "%SCRIPT%" "%~1"
) else (
    "%PY%" -u "%SCRIPT%"
)
endlocal
