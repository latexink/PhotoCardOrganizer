$ErrorActionPreference = 'Stop'
if ($env:USERNAME -ne 'WDAGUtilityAccount') { throw 'This lifecycle test must run only inside Windows Sandbox.' }
$result = [ordered]@{ version = '0.11.3.dev2'; status = 'failed'; steps = @() }
$assets = 'C:\PhotoCardAssets'
$application = Join-Path $env:LOCALAPPDATA 'Programs\PhotoCardOrganizer\PhotoCardOrganizer.exe'
$marker = Join-Path $env:APPDATA 'PhotoCardOrganizer\preview-marker.txt'
function Assert([bool]$condition, [string]$message) { if (-not $condition) { throw $message } }
function Run([string]$path, [string[]]$arguments) {
    $process = Start-Process -FilePath $path -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    Assert ($process.ExitCode -eq 0) "Process failed ($($process.ExitCode)): $path"
}
function Install([string]$version) {
    Run (Join-Path $assets "PhotoCardOrganizer-Installer-$version.exe") @('/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    Assert ((Get-ItemProperty 'HKCU:\Software\PhotoCardOrganizer').Version -eq $version) 'Installed version mismatch.'
}
function Uninstall {
    Run (Join-Path (Split-Path $application) 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    Assert (-not (Test-Path -LiteralPath $application)) 'Application remained after uninstall.'
}
try {
    Install '0.11.3.dev2'
    $result.steps += 'clean install'
    Uninstall
    $result.steps += 'clean uninstall'
    Install '0.11.2'
    New-Item -ItemType Directory -Force -Path (Split-Path $marker) | Out-Null
    Set-Content -LiteralPath $marker -Value 'sandbox-only user data' -NoNewline
    $markerHash = (Get-FileHash -LiteralPath $marker).Hash
    Install '0.11.3.dev2'
    Assert ((Get-FileHash -LiteralPath $marker).Hash -eq $markerHash) 'Upgrade changed user data.'
    $result.steps += 'upgrade from 0.11.2 preserving user data'
    Install '0.11.3.dev2'
    Assert ((Get-FileHash -LiteralPath $marker).Hash -eq $markerHash) 'Repair changed user data.'
    $result.steps += 'repair preserving user data'
    $report = 'C:\PhotoCardResults\sandbox-gui-dev2.json'
    Remove-Item -LiteralPath $report -ErrorAction SilentlyContinue
    Run $application @('--check-gui', $report)
    Assert ((Get-Content -LiteralPath $report -Raw | ConvertFrom-Json).status -eq 'passed') 'GUI check failed.'
    $result.steps += 'packaged GUI startup and navigation'
    Uninstall
    Assert ((Get-FileHash -LiteralPath $marker).Hash -eq $markerHash) 'Uninstall removed user data.'
    $result.steps += 'uninstall preserving user data'
    $result.status = 'passed'
} catch { $result.error = $_.Exception.Message }
finally {
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath 'C:\PhotoCardResults\sandbox-preview-dev2.json' -Encoding utf8
    Start-Process shutdown.exe -ArgumentList @('/s', '/t', '3') -WindowStyle Hidden
}
