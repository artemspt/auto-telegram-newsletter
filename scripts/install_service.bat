@echo off

set ROOT_DIR=%~dp0..
set VENV_DIR=%ROOT_DIR%\.venv
set PYTHON_BIN=%VENV_DIR%\Scripts\python.exe

echo Creating virtual environment...

if not exist "%VENV_DIR%" (
    python -m venv "%VENV_DIR%"
)

echo Installing dependencies...

"%PYTHON_BIN%" -m pip install --upgrade pip
"%PYTHON_BIN%" -m pip install -r "%ROOT_DIR%\requirements.txt"

echo Setup complete
pause