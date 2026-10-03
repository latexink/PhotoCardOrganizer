[CmdletBinding()]
param([switch]$Capture, [switch]$Check, [string]$DebuggerPath = "")
$ErrorActionPreference = "Stop"
try {
    $Application = Join-Path $PSScriptRoot "PhotoCardOrganizer\PhotoCardOrganizer.exe"
    if (-not (Test-Path -LiteralPath $Application)) { throw "Extract the entire diagnostic ZIP first." }
    $State = Join-Path $PSScriptRoot "diagnostic-state"
    New-Item -ItemType Directory -Force -Path $State | Out-Null
    foreach ($Name in @("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME")) {
        [Environment]::SetEnvironmentVariable($Name, $State, "Process")
    }
    foreach ($Name in @("PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH")) {
        [Environment]::SetEnvironmentVariable($Name, $null, "Process")
    }
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $Config = Join-Path $State "config.json"
    if (-not (Test-Path -LiteralPath $Config)) {
        $Initial = @{ library_destinations=@(); library_setup_complete=$true; default_library_id="";
           destination_root=(Join-Path $State "unused-library");
           identification=@{auto_detect=$false; configured_roots=@()};
           diagnostics=@{detailed_logging=$true};
           local_history=@{directory=(Join-Path $State "history")}
        } | ConvertTo-Json -Depth 5
        [IO.File]::WriteAllText($Config, $Initial, (New-Object Text.UTF8Encoding($false)))
    }
    Write-Host "DIAGNOSTIC PREVIEW: use disposable copies only. Close the installed app first."
    Write-Host "Settings and logs: $State"
    $Arguments = @("--config", $Config)
    if ($Check) {
        $ReportPath = Join-Path $State "gui-check.json"
        if (Test-Path -LiteralPath $ReportPath) { Remove-Item -LiteralPath $ReportPath }
        $Arguments = @("--check-gui", $ReportPath)
    }
    if ($Capture) {
        Write-Host "Full crash dumps can be large and contain private data. They stay local; nothing is uploaded."
        if ((Read-Host "Type CAPTURE to launch with full-memory crash capture") -cne "CAPTURE") { return }
        if (-not $DebuggerPath) {
            $DebuggerPath = "C:\Users\vroom\Documents\Codex\2026-07-12\i\work\PhotoCardOrganizer-ui\build\crash-analysis\tools\windbg\amd64\cdb.exe"
        }
        if (-not (Test-Path -LiteralPath $DebuggerPath)) {
            throw "CDB was not found. Pass -DebuggerPath with your Microsoft Windows Debugger cdb.exe path."
        }
        $Signature = Get-AuthenticodeSignature -LiteralPath $DebuggerPath
        if ($Signature.Status -ne "Valid" -or $Signature.SignerCertificate.Subject -notmatch "Microsoft") {
            throw "The debugger does not have a valid Microsoft signature."
        }
        $Run = Join-Path $State ("capture-" + (Get-Date -Format "yyyyMMdd-HHmmss") + "-" + [guid]::NewGuid().ToString("N").Substring(0,6))
        New-Item -ItemType Directory -Path $Run | Out-Null
        # Relative dump paths avoid interpolating user-controlled folder names into debugger commands.
        $Commands = Join-Path $Run "capture.txt"
        @('sxe -c ".dump /ma crash.dmp; .ecxr; ~*kv; q" av', 'sxd e06d7363', 'sxi ibp', 'g') |
            Set-Content -LiteralPath $Commands -Encoding ASCII
        Push-Location $Run
        try {
            # The onedir app runs in one process. Do not debug unrelated console/helper children.
            & $DebuggerPath -G -cf $Commands -logo (Join-Path $Run "debugger.txt") $Application @Arguments
            if ($LASTEXITCODE -ne 0 -and -not (Test-Path -LiteralPath (Join-Path $Run "crash.dmp"))) {
                throw "Debugger exited with code $LASTEXITCODE without a crash dump. Review debugger.txt."
            }
        }
        finally { Pop-Location }
        Write-Host "Capture folder: $Run"
    } else {
        $Quoted = ($Arguments | ForEach-Object { '"' + $_ + '"' }) -join ' '
        $Process = Start-Process -FilePath $Application -ArgumentList $Quoted -WorkingDirectory $PSScriptRoot -PassThru -Wait -WindowStyle Hidden
        if ($Process.ExitCode -ne 0) { throw "Application exited with code $($Process.ExitCode). Inspect the diagnostic logs." }
    }
    if ($Check) {
        $Report = Get-Content -LiteralPath (Join-Path $State "gui-check.json") -Raw | ConvertFrom-Json
        if ($Report.status -ne "passed") { throw "Packaged GUI check did not pass." }
        Write-Host "Packaged GUI check passed."
    }
} catch {
    Write-Error $_
    exit 1
}
