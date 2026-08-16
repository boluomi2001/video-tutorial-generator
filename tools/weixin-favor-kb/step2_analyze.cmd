@echo off
echo [Step 2] Keyframe + OCR + Visual Analysis
echo.

set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set PY=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\venv\Scripts\python.exe
set SCRIPT=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\step2_analyze.py

if not "%1"=="" (
    echo Run ID: %1
    "%PY%" -u "%SCRIPT%" "%1"
) else (
    "%PY%" -u "%SCRIPT%"
)
pause
