@echo off
rem 一键视频分析流水线启动器（适配任意安装目录）
rem 用法: run_auto.cmd <视频号链接或本地文件路径> [--keep] [--no-publish]
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -u auto_run.py %*
endlocal
