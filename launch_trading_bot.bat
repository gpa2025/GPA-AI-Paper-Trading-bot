@echo off
title GPA AI Paper Trading Bot
echo.
echo  ============================================
echo   GPA AI Paper Trading Bot
echo  ============================================
echo.

:: Check if already running by trying to connect
powershell -Command "try { $r = Invoke-WebRequest -Uri http://127.0.0.1:5000/api/state -UseBasicParsing -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel%==0 (
    echo   Dashboard is already running!
    echo   Opening browser...
    echo.
    goto :openbrowser
)

echo   Starting dashboard...
echo.

cd /d "%~dp0etrade_python_client"

:: Activate venv
if exist "%~dp0.venv\Scripts\activate.bat" (
    call "%~dp0.venv\Scripts\activate.bat"
)

:: Open browser after 4 second delay in background
start "" cmd /c "timeout /t 4 /nobreak >nul & powershell -Command Start-Process http://127.0.0.1:5000"

:: Start the dashboard
python run_dashboard.py

pause
exit /b 0

:openbrowser
powershell -Command "Start-Process http://127.0.0.1:5000"
timeout /t 2 /nobreak >nul
exit /b 0
