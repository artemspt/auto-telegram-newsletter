@echo off
setlocal

set ROOT_DIR=%~dp0..
cd /d "%ROOT_DIR%"

echo === CHECK PYTHON ===

where python >nul 2>&1
if %errorlevel% neq 0 (
    where py >nul 2>&1
    if %errorlevel% neq 0 (
        echo ERROR: Python not found. Install Python and restart.
        pause
        exit /b
    ) else (
        set PY_CMD=py
    )
) else (
    set PY_CMD=python
)

echo Using %PY_CMD%

echo === CREATE VENV ===
if not exist ".venv" (
    %PY_CMD% -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: venv creation failed
    pause
    exit /b
)

echo === INSTALL DEPENDENCIES ===
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo === ADD TO AUTOSTART ===

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set BAT_PATH=%ROOT_DIR%\run_bot.bat

echo @echo off > "%BAT_PATH%"
echo cd /d "%ROOT_DIR%" >> "%BAT_PATH%"
echo start "" "%ROOT_DIR%\.venv\Scripts\python.exe" "%ROOT_DIR%\main.py" >> "%BAT_PATH%"

copy "%BAT_PATH%" "%STARTUP%" >nul

echo Added to startup

echo === START BOT NOW ===
start "" .venv\Scripts\python.exe main.py

echo === DONE ===
pause