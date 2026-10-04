@echo off
rem 一键视频分析流水线启动器（适配任意安装目录）
rem 单条: run_auto.cmd "<链接或本地文件>"
rem 批量: run_auto.cmd "<链接1>" "<链接2>" "<链接3>" ...
rem 批量: run_auto.cmd --from-file links.txt
rem 续跑: run_auto.cmd --from-file links.txt --resume output\batch_<时间戳>
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -u auto_run.py %*
endlocal
