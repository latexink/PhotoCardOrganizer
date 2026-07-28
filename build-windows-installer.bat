@echo off
setlocal
cd /d "%~dp0"

echo Building the Photo Card Organizer Windows installer...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "packaging\windows\build-installer.ps1" %*
if errorlevel 1 (
    echo.
    echo Windows installer build failed. Review the error above.
    pause
    exit /b 1
)

echo.
echo Windows installer build completed and verified.
pause
exit /b 0
