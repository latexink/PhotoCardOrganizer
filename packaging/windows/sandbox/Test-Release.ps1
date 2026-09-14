$ErrorActionPreference = 'Stop'

$assets = 'C:\PhotoCardAssets'
$output = 'C:\PhotoCardResults\sandbox-release-0.11.1.json'
$oldInstaller = Join-Path $assets 'PhotoCardOrganizer-Installer-0.11.0.exe'
$newInstaller = Join-Path $assets 'PhotoCardOrganizer-Installer-0.11.1.exe'
$result = [ordered]@{
    version = '0.11.1'
    started_at = (Get-Date).ToUniversalTime().ToString('o')
    steps = @()
    status = 'failed'
}

function Add-Step([string] $name, [scriptblock] $action) {
    & $action
    $script:result.steps += $name
}

function Assert-Condition([bool] $condition, [string] $message) {
    if (-not $condition) { throw $message }
}

function Run-Installer([string] $path) {
    $process = Start-Process -FilePath $path -ArgumentList @('/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
    Assert-Condition ($process.ExitCode -eq 0) "Installer failed with exit code $($process.ExitCode): $path"
}

try {
    Add-Step 'install 0.11.0' { Run-Installer $oldInstaller }
    $registry = Get-ItemProperty 'HKCU:\Software\PhotoCardOrganizer'
    Assert-Condition ($registry.Version -eq '0.11.0') "Expected installed version 0.11.0; found $($registry.Version)"

    $dataDirectory = Join-Path $env:APPDATA 'PhotoCardOrganizer'
    New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
    $marker = Join-Path $dataDirectory 'sandbox-release-marker.txt'
    Set-Content -Path $marker -Value 'preserve this sandbox-only settings marker' -NoNewline
    $markerHash = (Get-FileHash $marker -Algorithm SHA256).Hash

    Add-Step 'upgrade to 0.11.1' { Run-Installer $newInstaller }
    $registry = Get-ItemProperty 'HKCU:\Software\PhotoCardOrganizer'
    Assert-Condition ($registry.Version -eq '0.11.1') "Expected upgraded version 0.11.1; found $($registry.Version)"
    Assert-Condition ((Get-FileHash $marker -Algorithm SHA256).Hash -eq $markerHash) 'User data marker changed during upgrade.'

    Add-Step 'repair 0.11.1' { Run-Installer $newInstaller }
    Assert-Condition ((Get-FileHash $marker -Algorithm SHA256).Hash -eq $markerHash) 'User data marker changed during repair.'

    $application = Join-Path $env:LOCALAPPDATA 'Programs\PhotoCardOrganizer\PhotoCardOrganizer.exe'
    Assert-Condition (Test-Path -LiteralPath $application) 'Installed application executable is missing.'
    $guiReport = 'C:\PhotoCardResults\sandbox-gui-0.11.1.json'
    Add-Step 'launch packaged GUI check' {
        $process = Start-Process -FilePath $application -ArgumentList @('--check-gui', $guiReport) -Wait -PassThru
        Assert-Condition ($process.ExitCode -eq 0) "Packaged GUI check failed with exit code $($process.ExitCode)"
        $report = Get-Content $guiReport -Raw | ConvertFrom-Json
        Assert-Condition ($report.status -eq 'passed') "Packaged GUI report status is $($report.status)"
    }

    $uninstaller = Join-Path (Split-Path $application) 'unins000.exe'
    Assert-Condition (Test-Path -LiteralPath $uninstaller) 'Installed uninstaller is missing.'
    Add-Step 'uninstall preserving user data' {
        $process = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
        Assert-Condition ($process.ExitCode -eq 0) "Uninstaller failed with exit code $($process.ExitCode)"
    }
    Assert-Condition (-not (Test-Path -LiteralPath $application)) 'Application remained after uninstall.'
    Assert-Condition ((Get-FileHash $marker -Algorithm SHA256).Hash -eq $markerHash) 'User data marker was not preserved by uninstall.'
    $result.status = 'passed'
}
catch {
    $result.error = $_.Exception.Message
}
finally {
    $result.finished_at = (Get-Date).ToUniversalTime().ToString('o')
    $result | ConvertTo-Json -Depth 5 | Set-Content -Path $output -Encoding utf8
    Start-Process shutdown.exe -ArgumentList @('/s', '/t', '3')
}

if ($result.status -ne 'passed') { exit 1 }
