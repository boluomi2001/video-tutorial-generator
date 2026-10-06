@echo off
rem ============================================================
rem  Full batch: parse all 86 videos of WeChat Channels account
rem  Usage: double-click this file to START.
rem  If interrupted, use run_full_resume.cmd instead.
rem  Log: output\_full_run.log
rem ============================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
set "LINKS=%ROOT%\output\dali_shufang_links.txt"
set "LOG=%ROOT%\output\_full_run.log"

echo ============================================================
echo  Full batch parse: 86 videos (Dali Shufang)
echo  ETA 6-9 hours. Keep PC awake, WeChat + downloader running.
echo ============================================================
echo.

if not exist "%LINKS%" (
  echo [ERROR] links file not found: %LINKS%
  pause
  exit /b 1
)

echo [%date% %time%] ===== FULL BATCH START ===== >> "%LOG%"
"%PY%" -u auto_run.py --from-file "%LINKS%" >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo [%date% %time%] ===== DONE exit=%RC% ===== >> "%LOG%"
echo.
echo Finished. exit code = %RC%
echo Log file: %LOG%
echo.
pause
endlocal
