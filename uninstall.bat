@echo off
setlocal

cd /d "%~dp0"

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%.venv"
set "EGG_INFO_DIR=%PROJECT_DIR%photo_card_organizer.egg-info"
set "AUTOSTART_FILE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\PhotoCardOrganizer.cmd"
set "CONFIG_DIR=%APPDATA%\PhotoCardOrganizer"
set "PYTHON_MARKER=%PROJECT_DIR%.photocard-python-installed-by-installer"
set "FAILED="
set "REMOVE_USER_DATA="

echo Photo Card Organizer Uninstaller
echo.
echo Close Photo Card Organizer before continuing.
echo This will remove:
echo   - The project virtual environment
echo   - Editable-install metadata
echo   - The Windows login autostart entry
if exist "%PYTHON_MARKER%" echo   - Python 3.12 installed by this installer, after a separate confirmation
echo.
echo Per-user settings, card profiles, and local application logs are preserved by default.
echo Imported media, destination transfer records, and card identity folders are always preserved.
echo.
choice /C YN /N /M "Continue with uninstall? [Y/N] "
if errorlevel 2 goto :cancelled

echo.
if exist "%VENV_DIR%" (
    echo Removing the virtual environment...
    rmdir /S /Q "%VENV_DIR%"
    if exist "%VENV_DIR%" set "FAILED=1"
)

if exist "%EGG_INFO_DIR%" (
    echo Removing editable-install metadata...
    rmdir /S /Q "%EGG_INFO_DIR%"
    if exist "%EGG_INFO_DIR%" set "FAILED=1"
)

if exist "%AUTOSTART_FILE%" (
    echo Removing the login autostart entry...
    del /F /Q "%AUTOSTART_FILE%"
    if exist "%AUTOSTART_FILE%" set "FAILED=1"
)

if not exist "%CONFIG_DIR%" goto :python_option
echo.
choice /C NY /N /M "Also remove per-user settings, card profiles, and local application logs? [N/Y] "
if errorlevel 2 set "REMOVE_USER_DATA=1"
if not defined REMOVE_USER_DATA (
    echo Preserving per-user application data.
    goto :python_option
)
echo Removing per-user application data...
rmdir /S /Q "%CONFIG_DIR%"
if exist "%CONFIG_DIR%" set "FAILED=1"

:python_option
if not exist "%PYTHON_MARKER%" goto :verify_uninstall
echo.
choice /C YN /N /M "Also uninstall Python 3.12 that this installer added? [Y/N] "
if errorlevel 2 goto :keep_python
where winget.exe >nul 2>nul
if errorlevel 1 (
    echo Windows Package Manager is unavailable; Python could not be removed.
    set "FAILED=1"
    goto :verify_uninstall
)
echo Removing installer-managed Python 3.12...
winget uninstall --exact --id Python.Python.3.12 --scope user --silent --accept-source-agreements
if errorlevel 1 (
    set "FAILED=1"
) else (
    del /F /Q "%PYTHON_MARKER%"
)
goto :verify_uninstall

:keep_python
echo Keeping Python 3.12 installed.
del /F /Q "%PYTHON_MARKER%"

:verify_uninstall
echo.
echo Verifying uninstall operations...
if exist "%VENV_DIR%" set "FAILED=1"
if exist "%EGG_INFO_DIR%" set "FAILED=1"
if exist "%AUTOSTART_FILE%" set "FAILED=1"
if defined REMOVE_USER_DATA if exist "%CONFIG_DIR%" set "FAILED=1"

echo.
if defined FAILED (
    echo Uninstall finished with one or more items still present.
    echo Close any running Photo Card Organizer process and run uninstall.bat again.
    echo.
    echo Press any key to close this uninstaller.
    pause >nul
    exit /b 1
)

echo Uninstall verification passed.
if defined REMOVE_USER_DATA (
    echo Per-user application data was removed as requested.
) else (
    echo Per-user settings, card profiles, and local application logs were preserved.
)
echo Imported media, destination transfer records, and card metadata were preserved.
echo.
echo Press any key to close this uninstaller.
pause >nul
exit /b 0

:cancelled
echo.
echo Uninstall cancelled. No uninstall operations were performed.
echo.
echo Press any key to close this uninstaller.
pause >nul
exit /b 0
