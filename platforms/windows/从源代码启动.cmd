@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI"
set "PYTHONPATH=%PROJECT_ROOT%\app"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "REM_HOME=%PROJECT_ROOT%"

cd /d "%PROJECT_ROOT%"

echo Starting RemCompanion from source code...
echo PROJECT_ROOT=%PROJECT_ROOT%
echo PYTHONPATH=%PYTHONPATH%
echo.

python -m remcompanion.desktop

if errorlevel 1 (
    echo.
    echo Error occurred. Press any key to exit...
    pause
)
