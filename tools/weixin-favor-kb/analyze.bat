@echo off
setlocal
>nul chcp 65001
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

cd /d "%~dp0"
set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
set "PL=%~dp0pipeline.py"
set "DL=%~dp0downloads"

echo ========================================
echo   Video Analysis Pipeline
echo   Model: Qwen3-VL-30B-A3B-Instruct
echo ========================================
echo.

"%PY%" "%PL%" "%DL%"
echo.
echo EXIT CODE: %ERRORLEVEL%
endlocal
