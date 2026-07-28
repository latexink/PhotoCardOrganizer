[CmdletBinding()]
param(
    [string]$Python = "",
    [switch]$SkipDependencyInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $ProjectRoot

if (-not $Python) {
    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "A build Python was not found at '$Python'. Run install.bat or pass -Python."
}

$Version = & $Python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    throw "Could not read the project version."
}

if (-not $SkipDependencyInstall) {
    & $Python -m pip install --disable-pip-version-check -r requirements.txt -r requirements-build.txt
    if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }
}

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw "Application tests failed." }
}

$BuildRoot = Join-Path $ProjectRoot "build\windows"
$DistRoot = Join-Path $BuildRoot "dist"
$WorkRoot = Join-Path $BuildRoot "work"
$ArtifactRoot = Join-Path $ProjectRoot "artifacts\windows"
New-Item -ItemType Directory -Force -Path $ArtifactRoot | Out-Null

& $Python packaging\build_bundle.py --dist $DistRoot --work $WorkRoot --package-kind windows
if ($LASTEXITCODE -ne 0) { throw "The Windows application bundle failed." }

$IsccCandidates = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
    "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
$Iscc = $IsccCandidates | Select-Object -First 1
if (-not $Iscc) {
    throw "Inno Setup was not found. Install Inno Setup 7, then rerun this command."
}

$SourceDir = Join-Path $DistRoot "PhotoCardOrganizer"
$Script = Join-Path $PSScriptRoot "PhotoCardOrganizer.iss"
& $Iscc "/DSourceDir=$SourceDir" "/DAppVersion=$Version" "/DOutputDir=$ArtifactRoot" $Script
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }

$Installer = Join-Path $ArtifactRoot "PhotoCardOrganizer-Installer-$Version.exe"
if (-not (Test-Path -LiteralPath $Installer)) {
    throw "Installer verification failed: '$Installer' was not created."
}
$InstallerHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Installer).Hash.ToLowerInvariant()
$HashFile = "$Installer.sha256"
"$InstallerHash  $([IO.Path]::GetFileName($Installer))" |
    Set-Content -LiteralPath $HashFile -Encoding Ascii
if (-not (Test-Path -LiteralPath $HashFile)) {
    throw "Installer checksum verification failed: '$HashFile' was not created."
}

Write-Host ""
Write-Host "Windows installer verified:" -ForegroundColor Green
Write-Host $Installer
Write-Host $HashFile
