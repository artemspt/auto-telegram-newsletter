@echo off
setlocal EnableExtensions

set "ROOT_DIR=%~dp0.."
set "VENV_DIR=%ROOT_DIR%\.venv"
set "PYTHON_BIN=%VENV_DIR%\Scripts\python.exe"
set "RUN_FILE=%ROOT_DIR%\run_bot.bat"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "STARTUP_RUN_FILE=%STARTUP_DIR%\run_bot.bat"
set "ENV_FILE=%ROOT_DIR%\.env"

call :log INFO Starting Windows installation
call :log INFO Project root: %ROOT_DIR%

cd /d "%ROOT_DIR%" || call :die Failed to enter project directory

if not exist "%ENV_FILE%" call :die File .env not found. Copy .env.exemple to .env, fill in secrets, then run this script again.

call :detect_python
call :log INFO Using Python launcher: %PY_CMD%

if exist "%VENV_DIR%" (
    call :log INFO Virtual environment already exists: %VENV_DIR%
) else (
    call :log INFO Creating virtual environment: %VENV_DIR%
    %PY_CMD% -m venv "%VENV_DIR%" || call :die Failed to create virtual environment
)

if not exist "%PYTHON_BIN%" call :die Python binary not found in virtual environment

call :log INFO Upgrading pip
"%PYTHON_BIN%" -m pip install --upgrade pip || call :die Failed to upgrade pip

call :log INFO Installing dependencies
"%PYTHON_BIN%" -m pip install -r requirements.txt || call :die Failed to install dependencies

call :log INFO Writing run file: %RUN_FILE%
(
    echo @echo off
    echo setlocal EnableExtensions
    echo cd /d "%ROOT_DIR%"
    echo if not exist ".env" exit /b 1
    echo for /f "usebackq tokens=* delims=" %%%%x in ^(".env"^) do set %%%%x
    echo start "Telegram Broadcast Bot" /min "%PYTHON_BIN%" "%ROOT_DIR%\main.py"
) > "%RUN_FILE%" || call :die Failed to write run_bot.bat

if not exist "%STARTUP_DIR%" call :die Startup directory not found: %STARTUP_DIR%

if exist "%STARTUP_RUN_FILE%" (
    fc /b "%RUN_FILE%" "%STARTUP_RUN_FILE%" >nul 2>&1
    if errorlevel 1 (
        call :log INFO Updating Startup run file
        copy /y "%RUN_FILE%" "%STARTUP_RUN_FILE%" >nul || call :die Failed to update Startup run file
    ) else (
        call :log INFO Startup run file already up to date
    )
) else (
    call :log INFO Installing Startup run file
    copy /y "%RUN_FILE%" "%STARTUP_RUN_FILE%" >nul || call :die Failed to copy run file to Startup
)

call :stop_existing_bot

call :log INFO Starting bot
start "Telegram Broadcast Bot" /min "%PYTHON_BIN%" "%ROOT_DIR%\main.py"

call :log INFO Installation completed
pause
exit /b 0

:detect_python
where python >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    exit /b 0
)
where py >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py"
    exit /b 0
)
call :die Python not found in PATH

:stop_existing_bot
call :log INFO Searching for existing bot processes
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$root=[regex]::Escape('%ROOT_DIR:\=\\%');" ^
    "$procs=Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'main\.py' -and $_.CommandLine -match $root };" ^
    "if ($procs) { $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Output ('Stopped PID ' + $_.ProcessId) } }"
if %errorlevel% neq 0 (
    call :log WARN Unable to stop existing bot processes automatically
)
exit /b 0

:log
echo [%~1] %~2
exit /b 0

:die
echo [ERROR] %*
pause
exit /b 1
