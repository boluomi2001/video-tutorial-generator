@echo off
setlocal
echo ========================================
echo   Step 1: Audio + Whisper Transcription
echo ========================================
echo.

set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "SCRIPT=%~dp0transcribe_only.py"

if not exist "%SCRIPT%" (
    echo ERROR: Script not found at %SCRIPT%
    pause
    exit /b 1
)

echo Python: %PY%
echo Script: OK
echo.

if not "%~1"=="" (
    echo Processing: %1
    "%PY%" -u "%SCRIPT%" "%~1"
) else (
    "%PY%" -u "%SCRIPT%"
)

echo.
echo Exit code: %ERRORLEVEL%
endlocal
