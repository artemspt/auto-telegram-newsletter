@echo off
setlocal

cd /d "%~dp0.."

echo === ROOT ===
cd

echo === CHECK PYTHON ===

where python >nul 2>&1
if %errorlevel% neq 0 (
    where py >nul 2>&1
    if %errorlevel% neq 0 (
        echo ERROR: Python not found
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
    echo ERROR: venv not created
    pause
    exit /b
)

echo === LOAD .ENV ===
if exist ".env" (
    for /f "delims=" %%x in (.env) do set %%x
)

echo === INSTALL DEPENDENCIES ===
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

echo === CREATE RUN FILE ===

set RUN_FILE=%CD%\run_bot.bat

echo @echo off > "%RUN_FILE%"
echo cd /d "%CD%" >> "%RUN_FILE%"
echo for /f "delims=" %%%%x in (.env) do set %%%%x >> "%RUN_FILE%"
echo start "" "%CD%\.venv\Scripts\python.exe" "%CD%\main.py" >> "%RUN_FILE%"

echo === ADD TO AUTOSTART ===

set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
copy "%RUN_FILE%" "%STARTUP%" >nul

echo === START BOT ===
start "" .venv\Scripts\python.exe main.py

echo === DONE ===
pause