@echo off
setlocal
echo Photo Card Organizer - DIAGNOSTIC PREVIEW
echo Use a disposable library copy. Your installed settings are not imported.
echo 1. Launch with local diagnostic logs
echo 2. Launch with full crash capture (separate consent required)
choice /C 12 /N /M "Choose 1 or 2: "
if errorlevel 2 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Diagnostic.ps1" -Capture
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Diagnostic.ps1"
)
echo.
echo Diagnostic session ended. Review any messages above.
pause
