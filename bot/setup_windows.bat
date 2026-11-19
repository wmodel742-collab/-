@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   AvoPerenos - Windows setup
echo ========================================
echo.
echo Need Python 3.12 ^(not 3.15^). Checking...
echo.

set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3.12"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=2 delims= " %%v in ('python -c "import sys; print(sys.version_info[0], sys.version_info[1])" 2^>nul') do (
            if "%%v"=="3 12" set "PY=python"
        )
    )
)

if not defined PY (
    echo [ERROR] Python 3.12 not found.
    echo You may have Python 3.15 - wheels for bot libs are not ready for it.
    echo.
    echo Fix:
    echo   1. Open a NEW Command Prompt
    echo   2. Run:  py install 3.12
    echo   3. Close this window, then run setup_windows.bat again
    echo.
    echo Or download Python 3.12 from:
    echo   https://www.python.org/downloads/release/python-31210/
    echo Enable: Add python.exe to PATH
    echo.
    pause
    exit /b 1
)

echo Using: %PY%
%PY% --version
echo.

if exist ".venv" (
    echo Removing old .venv ^(wrong Python version possible^)...
    rmdir /s /q ".venv" 2>nul
)

echo Creating virtual environment .venv with Python 3.12 ...
%PY% -m venv .venv
if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Created .env from .env.example
    echo Open .env in Notepad and set:
    echo   BOT_TOKEN=...
    echo   ADMIN_TELEGRAM_ID=...
    echo.
) else (
    echo .env already exists - not overwriting.
)

echo Initializing database...
".venv\Scripts\python.exe" scripts\init_db.py
if errorlevel 1 (
    echo Warning: init_db failed.
    echo Fill BOT_TOKEN and ADMIN_TELEGRAM_ID in .env, then run this script again.
    pause
    exit /b 1
)

echo.
echo Done.
echo   1. Edit .env in Notepad if needed
echo   2. Run START_BOT.bat
echo   3. In Telegram open @avoperenos_bot and send /start
echo.
pause
endlocal
