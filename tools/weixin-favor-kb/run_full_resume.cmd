@echo off
rem ============================================================
rem  Resume full batch: continue the latest unfinished batch
rem  Usage: double-click this file after an interruption.
rem  Already-succeeded items are skipped automatically.
rem  Log: output\_full_run.log
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONLEGACYWINDOWSSTDIO=utf-8

set "PY=%~dp0venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

for %%I in ("%~dp0..\..") do set "ROOT=%%~fI"
set "LINKS=%ROOT%\output\dali_shufang_links.txt"
set "LOG=%ROOT%\output\_full_run.log"
set "OUTDIR=%~dp0output"

if not exist "%LINKS%" (
  echo [ERROR] links file not found: %LINKS%
  pause
  exit /b 1
)

rem pick the newest batch_* dir as resume target
set "RESUME_DIR="
for /f "delims=" %%D in ('dir /b /ad /o-d "%OUTDIR%\batch_*" 2^>nul') do (
  if not defined RESUME_DIR set "RESUME_DIR=%OUTDIR%\%%D"
)

if not defined RESUME_DIR (
  echo [INFO] no previous batch found. Run run_full_batch.cmd first.
  pause
  exit /b 1
)

echo ============================================================
echo  Resume target batch:
echo  !RESUME_DIR!
echo ============================================================
echo.
echo [%date% %time%] ===== RESUME !RESUME_DIR! ===== >> "%LOG%"
"%PY%" -u auto_run.py --from-file "%LINKS%" --resume "!RESUME_DIR!" >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo [%date% %time%] ===== RESUME DONE exit=%RC% ===== >> "%LOG%"
echo.
echo Resume finished. exit code = %RC%
echo Log file: %LOG%
echo.
pause
endlocal
