@echo off
>nul chcp 65001
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\venv\Scripts\python.exe"
set "PL=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\pipeline.py"
set "DL=D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb\downloads"

cd /d "D:\work\2026-07-12-13-31-07\tools\weixin-favor-kb"

echo ========================================
echo   Video Analysis Pipeline
echo   Model: Qwen3-VL-32B-Instruct
echo ========================================
echo.

"%PY%" "%PL%" "%DL%"
echo.
echo EXIT CODE: %ERRORLEVEL%
pause
