@echo off
title GPA AI Paper Trading Bot — Installer
echo.
echo  ============================================================
echo   GPA AI Paper Trading Bot — Installer
echo  ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo  Please install Python 3.10+ from https://www.python.org
    echo  Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo  [1/5] Python found:
python --version
echo.

:: Create virtual environment
echo  [2/5] Creating virtual environment...
if not exist ".venv" (
    python -m venv .venv
    echo        Created .venv
) else (
    echo        .venv already exists, skipping
)
echo.

:: Activate venv and install dependencies
echo  [3/5] Installing dependencies...
call .venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
echo        Dependencies installed
echo.

:: Create icon
echo  [4/5] Creating icon and desktop shortcut...
python -c "from create_shortcut import create_ico; create_ico('trading_bot.ico')"

:: Create desktop shortcut using VBScript (most reliable method)
:: Detect actual desktop path (handles OneDrive redirection)
for /f "tokens=*" %%D in ('powershell -Command "[Environment]::GetFolderPath('Desktop')"') do set "DESKTOP=%%D"
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"

echo        Desktop path: %DESKTOP%

set "SHORTCUT=%DESKTOP%\GPA Trading Bot.lnk"
set "TARGET=%~dp0launch_trading_bot.bat"
set "ICON=%~dp0trading_bot.ico"
set "WORKDIR=%~dp0"
set "VBS=%TEMP%\gpa_create_shortcut.vbs"

echo Set ws = CreateObject("WScript.Shell") > "%VBS%"
echo Set sc = ws.CreateShortcut("%SHORTCUT%") >> "%VBS%"
echo sc.TargetPath = "%TARGET%" >> "%VBS%"
echo sc.WorkingDirectory = "%WORKDIR%" >> "%VBS%"
echo sc.IconLocation = "%ICON%" >> "%VBS%"
echo sc.Description = "GPA AI Paper Trading Bot" >> "%VBS%"
echo sc.Save >> "%VBS%"

cscript //nologo "%VBS%"
del "%VBS%" 2>nul

if exist "%SHORTCUT%" (
    echo        Desktop shortcut created.
) else (
    echo        [NOTE] Could not create shortcut.
    echo        Manually create a shortcut to: %TARGET%
)
echo.

:: Set up auto-start on reboot
echo  [5/5] Setting up auto-start on reboot...
set "TASK_NAME=GPA_TradingBot_AutoStart"
set "BAT_PATH=%~dp0launch_trading_bot.bat"

schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1
schtasks /create /tn "%TASK_NAME%" /tr "\"%BAT_PATH%\"" /sc onlogon /rl highest /f >nul 2>&1
if %errorlevel%==0 (
    echo        Auto-start task created.
) else (
    echo        [NOTE] Could not create scheduled task.
    echo        Try running install.bat as Administrator.
)

:: Also copy to Startup folder as backup
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%" (
    copy /y "%BAT_PATH%" "%STARTUP%\GPA Trading Bot.bat" >nul 2>&1
    echo        Added to Startup folder as backup.
)
echo.

echo  ============================================================
echo   Installation complete!
echo  ============================================================
echo.
echo   To start now:  Double-click "GPA Trading Bot" on desktop
echo                   Or run: launch_trading_bot.bat
echo   On reboot:     Bot starts automatically on login
echo   Dashboard:     http://127.0.0.1:5000
echo.
pause
