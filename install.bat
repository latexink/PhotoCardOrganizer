@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "PYTHON_CMD="
set "CHECK_ONLY="
set "EXPLICIT_PYTHON="
set "PYTHON_INSTALL_ATTEMPTED="
set "VENV_PYTHON=.venv\Scripts\python.exe"
set "PYTHON_MARKER=.photocard-python-installed-by-installer"

if /I "%~1"=="--check-python" (
    set "CHECK_ONLY=1"
    set "EXPLICIT_PYTHON=%~2"
    goto :detect_python
)

echo Photo Card Organizer Installer
echo.
echo This will create a project virtual environment and install the required Python packages.
echo If Python 3.11 or newer is missing, the installer can offer to install Python 3.12 with winget.
echo.
choice /C YN /N /M "Continue with installation? [Y/N] "
if errorlevel 2 goto :cancelled

echo.
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 goto :venv_ready
    echo The existing virtual environment is incomplete or uses an unsupported Python version.
    echo It will be repaired with a supported interpreter.
    echo.
)

:detect_python
set "PYTHON_CMD="

if defined EXPLICIT_PYTHON if exist "%EXPLICIT_PYTHON%" (
    "%EXPLICIT_PYTHON%" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%EXPLICIT_PYTHON%""
        goto :python_found
    )
)

py -3.14 -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.14"
    goto :python_found
)
py -3.13 -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.13"
    goto :python_found
)
py -3.12 -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    goto :python_found
)
py -3.11 -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.11"
    goto :python_found
)
py -3 -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)
python -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)
python3 -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_CMD=python3"
    goto :python_found
)

if exist "%LocalAppData%\Programs\Python\Python314\python.exe" (
    "%LocalAppData%\Programs\Python\Python314\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python314\python.exe""
        goto :python_found
    )
)
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    "%LocalAppData%\Programs\Python\Python313\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python313\python.exe""
        goto :python_found
    )
)
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python312\python.exe""
        goto :python_found
    )
)
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    "%LocalAppData%\Programs\Python\Python311\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%LocalAppData%\Programs\Python\Python311\python.exe""
        goto :python_found
    )
)
if exist "%ProgramFiles%\Python314\python.exe" (
    "%ProgramFiles%\Python314\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%ProgramFiles%\Python314\python.exe""
        goto :python_found
    )
)
if exist "%ProgramFiles%\Python313\python.exe" (
    "%ProgramFiles%\Python313\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%ProgramFiles%\Python313\python.exe""
        goto :python_found
    )
)
if exist "%ProgramFiles%\Python312\python.exe" (
    "%ProgramFiles%\Python312\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%ProgramFiles%\Python312\python.exe""
        goto :python_found
    )
)
if exist "%ProgramFiles%\Python311\python.exe" (
    "%ProgramFiles%\Python311\python.exe" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%ProgramFiles%\Python311\python.exe""
        goto :python_found
    )
)

for /f "delims=" %%P in ('where python.exe 2^>nul') do (
    "%%P" -c "import sys; raise SystemExit(sys.version_info.__lt__((3, 11)))" >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD="%%P""
        goto :python_found
    )
)

if defined CHECK_ONLY (
    echo No supported Python interpreter was found.
    exit /b 1
)
if defined PYTHON_INSTALL_ATTEMPTED goto :python_restart_needed

echo Python 3.11 or newer is not installed.
where winget.exe >nul 2>nul
if errorlevel 1 goto :python_missing

echo.
choice /C YN /N /M "Install Python 3.12 for this user with winget? [Y/N] "
if errorlevel 2 goto :python_missing

echo.
echo Installing Python 3.12...
winget install --exact --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :failed
> "%PYTHON_MARKER%" echo Python.Python.3.12
set "PYTHON_INSTALL_ATTEMPTED=1"
goto :detect_python

:python_found
if defined CHECK_ONLY (
    echo Python command: %PYTHON_CMD%
    %PYTHON_CMD% --version
    exit /b %errorlevel%
)

:create_venv
echo Creating the virtual environment...
%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :failed
if not exist "%VENV_PYTHON%" goto :failed

:venv_ready
echo Installing Photo Card Organizer...
"%VENV_PYTHON%" -m pip install -e .
if errorlevel 1 goto :failed

:verify_installation
echo Verifying the installation...
"%VENV_PYTHON%" -c "import PIL, exifread, PySide6, photocard; from photocard.cli import build_parser; from photocard.gui import PhotoCardApp; build_parser()"
if errorlevel 1 goto :verification_failed
"%VENV_PYTHON%" app.py --help >nul 2>nul
if errorlevel 1 goto :verification_failed

echo.
echo Installation verification passed.
echo Start the application with PhotoCardOrganizer.bat or:
echo   .venv\Scripts\python.exe app.py
echo.
choice /C YN /N /M "Launch Photo Card Organizer now? [Y/N] "
if errorlevel 2 goto :verified_complete
if not exist ".venv\Scripts\pythonw.exe" goto :verification_failed
start "Photo Card Organizer" ".venv\Scripts\pythonw.exe" app.py
if errorlevel 1 goto :verification_failed
echo Launch command completed.

:verified_complete
echo.
echo Press any key to confirm the verified installation and close this installer.
pause >nul
exit /b 0

:verification_failed
echo.
echo Installation files were created, but the verification check failed.
choice /C RC /N /M "Retry package installation or close? [R/C] "
if errorlevel 2 goto :failed_close
goto :venv_ready

:python_restart_needed
echo.
echo Python was installed, but this Command Prompt cannot locate it yet.
echo Close this installer, then run install.bat again.
echo.
echo Press any key to confirm and close this installer.
pause >nul
exit /b 1

:python_missing
echo.
echo Python 3.11 or newer is required and was not found.
echo Install it from https://www.python.org/downloads/windows/ and run install.bat again.
echo During Python setup, enable the option to add Python to PATH.
echo.
echo Press any key to confirm and close this installer.
pause >nul
exit /b 1

:cancelled
echo.
echo Installation cancelled. No installation operations were performed.
echo.
echo Press any key to confirm and close this installer.
pause >nul
exit /b 0

:failed
echo.
echo Installation failed. Review the error above and try again.
choice /C RC /N /M "Retry installation or close? [R/C] "
if errorlevel 2 goto :failed_close
if exist "%VENV_PYTHON%" goto :venv_ready
goto :detect_python

:failed_close
echo.
echo Press any key to confirm and close this installer.
pause >nul
exit /b 1
