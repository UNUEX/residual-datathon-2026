param([ValidateRange(1024,65535)][int]$Port = 8766, [switch]$KeepModel)
$ErrorActionPreference = 'Stop'
$taskRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$taskLogs = Join-Path $taskRoot 'private_outputs'
$taskPidName = if ($Port -eq 8766) { 'server.pid' } else { "server-$Port.pid" }
try {
    $taskPidFile = Join-Path $taskLogs $taskPidName
    if (Test-Path -LiteralPath $taskPidFile) {
        $taskServerPid = [int](Get-Content -LiteralPath $taskPidFile -Raw)
        $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskServerPid" -ErrorAction SilentlyContinue
        $taskExpectedPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
        if ($taskProcess -and $taskProcess.ExecutablePath -eq $taskExpectedPython -and $taskProcess.CommandLine -match 'src\.serve' -and $taskProcess.CommandLine -match "--port\s+$Port(?:\s|$)") {
            Stop-Process -Id $taskServerPid
            Write-Host 'Residual stopped. Saved packages and reports were preserved.'
        } elseif ($taskProcess) { throw 'PID belongs to another process. Nothing was stopped.' }
        else { Write-Host 'Residual is already stopped.' }
    } else { Write-Host 'No Residual process was started from this folder.' }
    $taskModelPidFile = Join-Path $taskLogs 'ollama.pid'
    if (-not $KeepModel -and (Test-Path -LiteralPath $taskModelPidFile)) {
        $taskModelPid = [int](Get-Content -LiteralPath $taskModelPidFile -Raw)
        $taskModelProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskModelPid" -ErrorAction SilentlyContinue
        $taskExpectedModel = Join-Path $taskLogs 'runtime\ollama\ollama.exe'
        if ($taskModelProcess -and $taskModelProcess.ExecutablePath -eq $taskExpectedModel -and $taskModelProcess.CommandLine -match '\bserve\b') {
            Stop-Process -Id $taskModelPid
            Write-Host 'The portable local model server stopped.'
        }
    }
    exit 0
} catch { Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red; exit 1 }
