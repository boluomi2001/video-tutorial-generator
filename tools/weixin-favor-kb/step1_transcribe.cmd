@echo off
echo ========================================
echo   Step 1: Audio + Whisper Transcription
echo ========================================
echo.

set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\venv\Scripts\python.exe"
set "SCRIPT=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\transcribe_only.py"

if not exist "%PY%" (
    echo ERROR: Python not found at %PY%
    pause
    exit /b 1
)
if not exist "%SCRIPT%" (
    echo ERROR: Script not found at %SCRIPT%
    pause
    exit /b 1
)

echo Python: OK
echo Script: OK
echo.

if not "%1"=="" (
    echo Processing: %1
    "%PY%" -u "%SCRIPT%" "%1"
) else (
    "%PY%" -u "%SCRIPT%"
)

echo.
echo Exit code: %ERRORLEVEL%
pause
