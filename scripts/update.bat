@echo off

set ROOT_DIR=%~dp0..
set VENV_DIR=%ROOT_DIR%\.venv
set PYTHON_BIN=%VENV_DIR%\Scripts\python.exe

cd /d "%ROOT_DIR%"

echo Updating project...
git pull

echo Checking venv...

if not exist "%VENV_DIR%" (
    python -m venv "%VENV_DIR%"
)

echo Installing dependencies...
"%PYTHON_BIN%" -m pip install -r "%ROOT_DIR%\requirements.txt"

echo Restarting bot...

taskkill /f /im python.exe >nul 2>&1
start "" "%PYTHON_BIN%" "%ROOT_DIR%\main.py"

echo Updated and restarted: telegrambot
pause