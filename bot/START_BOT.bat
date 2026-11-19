@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   AvoPerenos - start bot
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found.
    echo Run setup_windows.bat first.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [ERROR] .env not found.
    echo Copy .env.example to .env and set BOT_TOKEN and ADMIN_TELEGRAM_ID
    pause
    exit /b 1
)

echo Starting... Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" -m bot
set EXITCODE=%ERRORLEVEL%
echo.
if not %EXITCODE%==0 (
    echo Bot exited with error code: %EXITCODE%
    pause
)
exit /b %EXITCODE%
