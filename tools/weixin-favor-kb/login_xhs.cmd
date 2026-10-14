@echo off
rem ============================================================
rem  小红书 Cookie 获取助手（扫码优先，粘贴兜底）
rem
rem  用法：
rem    login_xhs.cmd                     扫码登录（推荐）
rem    login_xhs.cmd --paste             手动粘贴 Cookie
rem    login_xhs.cmd --browser chrome    用 Chrome 扫码
rem    login_xhs.cmd --validate-only     只校验现有 Cookie
rem
rem  说明：小红书网页端笔记详情需要登录态，必须先配一次 Cookie。
rem        web_session 有效期通常数月，过期后重跑本脚本即可。
rem ============================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -u scripts\login_xhs.py %*
endlocal
