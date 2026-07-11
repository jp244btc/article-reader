@echo off
setlocal
REM Article Reader launcher.
REM First run: creates the Python environment and installs everything (a few
REM minutes). After that it starts instantly.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run

echo ============================================================
echo  First run - setting up Article Reader (one time only)...
echo ============================================================
echo.

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYLAUNCH=py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PYLAUNCH=python"
    ) else (
        echo ERROR: Python was not found on this PC.
        echo.
        echo   Install it from https://www.python.org/downloads/
        echo   IMPORTANT: tick "Add python.exe to PATH" in the installer,
        echo   then double-click this file again.
        echo.
        pause
        exit /b 1
    )
)

echo [1/3] Creating the Python environment...
%PYLAUNCH% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: could not create the virtual environment.
    pause
    exit /b 1
)

echo [2/3] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency install failed - check your internet connection,
    echo then double-click this file again.
    pause
    exit /b 1
)

echo [3/3] Installing the headless browser (skipped if already present)...
".venv\Scripts\python.exe" -m playwright install chromium

echo.
echo Setup complete - starting Article Reader...
echo.

:run
".venv\Scripts\python.exe" app.py
pause
