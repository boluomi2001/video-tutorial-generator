@echo off
chcp 65001 >nul
rem 视频解析工作流 —— Windows 一键安装
rem 用法: scripts\setup.cmd [--api-key sk-xxx] [--skip-venv] [--fetch-ffmpeg] [--launch]
setlocal
cd /d "%~dp0\.."

set "PYEXE=python"
where python >nul 2>nul
if errorlevel 1 (
    set "PYEXE=py -3"
)

echo === 1/3 检测 Python ===
%PYEXE% --version

echo.
echo === 2/3 提示 ===
echo 推荐使用 Python 3.10 ~ 3.12。3.13 可能出现依赖编译问题。
echo 首次安装依赖需要几分钟，请不要关闭窗口。
echo.

echo === 3/3 开始安装 ===
%PYEXE% -u scripts\setup.py --pip-mirror tsinghua %*

echo.
pause
endlocal
