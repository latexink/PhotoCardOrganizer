@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" goto :not_installed
if not exist ".venv\Scripts\python.exe" goto :not_installed

".venv\Scripts\python.exe" -c "import PIL, exifread, PySide6, photocard; from photocard.gui import PhotoCardApp" >nul 2>nul
if errorlevel 1 (
    echo Photo Card Organizer's installation is incomplete or its Python runtime moved.
    echo Run install.bat to repair and verify the environment.
    echo.
    pause
    exit /b 1
)

start "Photo Card Organizer" ".venv\Scripts\pythonw.exe" app.py
if errorlevel 1 (
    echo Photo Card Organizer could not be launched.
    echo Run install.bat to repair and verify the environment.
    echo.
    pause
    exit /b 1
)
exit /b 0

:not_installed
    echo Photo Card Organizer is not installed yet.
    echo Run install.bat first.
    echo.
    pause
    exit /b 1
