@echo off
title GPA AI Paper Trading Bot — Uninstaller
echo.
echo  ============================================
echo   GPA AI Paper Trading Bot — Uninstaller
echo  ============================================
echo.

:: Stop any running instance
echo  Stopping bot if running...
taskkill /f /im python.exe /fi "WINDOWTITLE eq GPA*" >nul 2>&1
echo.

:: Remove scheduled task
echo  Removing auto-start task...
schtasks /delete /tn "GPA_TradingBot_AutoStart" /f >nul 2>&1
echo  Done.
echo.

:: Remove startup folder entry
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\GPA Trading Bot.bat" (
    del "%STARTUP%\GPA Trading Bot.bat" >nul 2>&1
    echo  Removed from Startup folder.
)

:: Remove desktop shortcut
for /f "tokens=*" %%D in ('powershell -Command "[Environment]::GetFolderPath(''Desktop'')"') do set "DESKTOP=%%D"
if not defined DESKTOP set "DESKTOP=%USERPROFILE%\Desktop"
if exist "%DESKTOP%\GPA Trading Bot.lnk" (
    del "%DESKTOP%\GPA Trading Bot.lnk" >nul 2>&1
    echo  Removed desktop shortcut.
)

echo.
echo  Uninstall complete.
echo  Project files are still in this folder.
echo  Delete this folder to fully remove.
echo.
pause
