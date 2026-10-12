param([ValidateRange(1024,65535)][int]$Port = 8766, [switch]$NoBrowser, [string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskUrl = "http://127.0.0.1:$Port"
$taskLogs = Join-Path $taskRoot 'private_outputs'
$taskPidName = if ($Port -eq 8766) { 'server.pid' } else { "server-$Port.pid" }

function Get-ResidualHealth {
    try { return Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 2 } catch { return $null }
}
function Test-PythonVersion([string]$candidate) {
    if (-not $candidate -or -not (Test-Path -LiteralPath $candidate)) { return $false }
    if ($candidate -match '\\Microsoft\\WindowsApps\\') { return $false }
    try {
        & $candidate -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)' 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
function Start-PortableModelIfPresent {
    if (Test-Path -LiteralPath (Join-Path $taskLogs 'runtime\ollama\ollama.exe')) {
        & (Join-Path $taskRoot 'setup_local_ai.ps1') -StartOnly
    }
}

try {
    $taskHealth = Get-ResidualHealth
    if ($taskHealth.ok -and $taskHealth.app -eq 'residual' -and $taskHealth.version -eq '0.4') {
        Start-PortableModelIfPresent
        Write-Host "Residual is already running: $taskUrl"
        if (-not $NoBrowser) { Start-Process $taskUrl }
        exit 0
    }
    $taskProbe = New-Object System.Net.Sockets.TcpClient
    try {
        $taskProbe.Connect('127.0.0.1', $Port)
        if ($taskProbe.Connected) { throw "Port $Port is occupied by another app or an older Residual. Stop that app, or run: powershell -File start_residual.ps1 -Port 8767" }
    } catch [System.Net.Sockets.SocketException] { } finally { $taskProbe.Dispose() }

    $taskVenv = Join-Path $taskRoot '.venv\Scripts\python.exe'
    if (-not (Test-PythonVersion $taskVenv)) {
        $taskCandidates = [System.Collections.Generic.List[string]]::new()
        if ($PythonPath) { $taskCandidates.Add($PythonPath) }
        foreach ($taskName in @('python.exe', 'python3.exe')) {
            foreach ($taskCommand in @(Get-Command $taskName -All -ErrorAction SilentlyContinue)) {
                if ($taskCommand.Source) { $taskCandidates.Add($taskCommand.Source) }
            }
        }
        $taskPyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($taskPyLauncher -and $taskPyLauncher.Source -notmatch '\\Microsoft\\WindowsApps\\') {
            foreach ($taskMinor in @('3.13','3.12','3.11')) {
                try {
                    $taskFound = & $taskPyLauncher.Source "-$taskMinor" -c 'import sys; print(sys.executable)' 2>$null
                    if ($LASTEXITCODE -eq 0 -and $taskFound) { $taskCandidates.Add([string]$taskFound) }
                } catch { }
            }
        }
        foreach ($taskBase in @((Join-Path $env:LOCALAPPDATA 'Programs\Python'), $env:ProgramFiles)) {
            if ($taskBase -and (Test-Path -LiteralPath $taskBase)) {
                foreach ($taskDir in @(Get-ChildItem -LiteralPath $taskBase -Directory -Filter 'Python*' -ErrorAction SilentlyContinue)) {
                    $taskCandidates.Add((Join-Path $taskDir.FullName 'python.exe'))
                }
            }
        }
        $taskPython = $null
        foreach ($taskCandidate in $taskCandidates) {
            if (Test-PythonVersion $taskCandidate) { $taskPython = $taskCandidate; break }
        }
        if (-not $taskPython) { throw 'Python 3.11-3.13 is required. Install from https://www.python.org/downloads/ (enable Add python.exe to PATH), then open start_residual.cmd again.' }
        Write-Host '[1/3] Preparing an isolated Python environment (.venv)...'
        & $taskPython -m venv (Join-Path $taskRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Check Python and write access to this folder.' }
    }
    $taskRequirements = Join-Path $taskRoot 'requirements.txt'
    $taskHash = (Get-FileHash -LiteralPath $taskRequirements -Algorithm SHA256).Hash
    $taskStamp = Join-Path $taskRoot '.venv\residual-requirements.sha256'
    $taskInstalledHash = if (Test-Path -LiteralPath $taskStamp) { (Get-Content -LiteralPath $taskStamp -Raw).Trim() } else { '' }
    $env:PYTHONPATH = ''
    $env:PYTHONNOUSERSITE = '1'
    & $taskVenv -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ('pandas', 'sklearn', 'openpyxl', 'xlrd')) else 1)" 2>$null
    if ($LASTEXITCODE -ne 0 -or $taskInstalledHash -ne $taskHash) {
        Write-Host '[2/3] Installing dependencies. Internet is needed only for this first setup...'
        & $taskVenv -m pip install --disable-pip-version-check -r $taskRequirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependencies could not be installed. Check your internet connection and open start_residual.cmd again.' }
        Set-Content -LiteralPath $taskStamp -Value $taskHash -Encoding ASCII
    }
    New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
    Start-PortableModelIfPresent
    Write-Host '[3/3] Starting Residual on this computer...'
    $taskProcess = Start-Process -FilePath $taskVenv -ArgumentList @('-X','utf8','-m','src.serve','--port',"$Port") -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskLogs "server-$Port.stdout.log") -RedirectStandardError (Join-Path $taskLogs "server-$Port.stderr.log") -PassThru
    Set-Content -LiteralPath (Join-Path $taskLogs $taskPidName) -Value $taskProcess.Id -Encoding ASCII
    for ($taskTry = 0; $taskTry -lt 120; $taskTry++) {
        $taskHealth = Get-ResidualHealth
        if ($taskHealth.ok -and $taskHealth.app -eq 'residual' -and $taskHealth.version -eq '0.4') {
            Write-Host "Ready: $taskUrl"
            Write-Host 'If the local model is not installed yet: open setup_local_ai.cmd once.'
            Write-Host 'To stop the app: open stop_residual.cmd. Your data will remain saved.'
            if (-not $NoBrowser) { Start-Process $taskUrl }
            exit 0
        }
        if ($taskProcess.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    throw "Residual did not start. See private_outputs/server-$Port.stderr.log."
} catch {
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
