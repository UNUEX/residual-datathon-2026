param([string]$Model = 'qwen3:1.7b', [switch]$StartOnly, [switch]$WithGpu)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPrivate = Join-Path $taskRoot 'private_outputs'
$taskRuntime = Join-Path $taskPrivate 'runtime\ollama'
$taskModels = Join-Path $taskPrivate 'models'
$taskExe = Join-Path $taskRuntime 'ollama.exe'
$taskVersion = 'v0.40.3'
$taskBaseUrl = "https://github.com/ollama/ollama/releases/download/$taskVersion"
$taskArch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
$taskArchiveName = "ollama-windows-$taskArch.zip"
$taskDownloads = Join-Path $taskPrivate 'runtime\downloads'
$taskArchive = Join-Path $taskDownloads $taskArchiveName
$taskApi = 'http://127.0.0.1:11434'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = $taskModels
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_CONTEXT_LENGTH = '8192'
$env:OLLAMA_NUM_PARALLEL = '1'
$env:OLLAMA_MAX_LOADED_MODELS = '1'

function Test-LocalOllama {
    try {
        $taskVersionInfo = Invoke-RestMethod -Uri "$taskApi/api/version" -TimeoutSec 2
        $taskTags = Invoke-RestMethod -Uri "$taskApi/api/tags" -TimeoutSec 2
        return [bool]($taskVersionInfo.version -and $null -ne $taskTags.models)
    } catch { return $false }
}
function Get-TaskDownload([string]$url, [string]$destination) {
    $taskCurl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($taskCurl) {
        & $taskCurl.Source --fail --location --retry 3 --continue-at - --progress-bar --output $destination $url
        if ($LASTEXITCODE -ne 0) { throw 'Download interrupted. Run setup_local_ai.cmd again to resume.' }
    } else { Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing }
}

function Install-CpuRuntime {
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Open start_residual.cmd first to prepare Python, then run setup_local_ai.cmd.' }
    $taskCpuCode = @'
import io, urllib.request, zipfile, pathlib, hashlib, json, sys, zlib
url, destination = sys.argv[1:3]
class RemoteZip(io.RawIOBase):
    def __init__(self):
        self.pos=0
        with urllib.request.urlopen(urllib.request.Request(url,method="HEAD"),timeout=30) as response:
            self.size=int(response.headers["Content-Length"])
        self.cache={}; self.block=2*1024*1024
    def seekable(self): return True
    def seek(self,offset,whence=0):
        self.pos=offset if whence==0 else self.pos+offset if whence==1 else self.size+offset
        return self.pos
    def tell(self): return self.pos
    def read(self,size=-1):
        size=self.size-self.pos if size<0 else min(size,self.size-self.pos)
        chunks=[]
        while size>0:
            start=(self.pos//self.block)*self.block
            if start not in self.cache:
                end=min(start+self.block,self.size)-1
                req=urllib.request.Request(url,headers={"Range":f"bytes={start}-{end}","User-Agent":"Residual-Local-Setup"})
                with urllib.request.urlopen(req,timeout=120) as response:
                    if response.status!=206: raise RuntimeError("Partial downloads unsupported; use setup_local_ai.cmd -WithGpu for the full archive.")
                    raw=response.read()
                if len(raw)!=end-start+1: raise RuntimeError("Interrupted range. Run setup again to resume.")
                self.cache[start]=raw
            data=self.cache[start]; offset=self.pos-start; count=min(size,len(data)-offset)
            chunks.append(data[offset:offset+count]); size-=count; self.pos+=count
        return b"".join(chunks)
root=pathlib.Path(destination).resolve(); root.mkdir(parents=True,exist_ok=True)
manifest={"source":url,"mode":"CPU-only; official HTTPS origin, archive CRC32 and executable signature; not full archive SHA256","files":[]}
with zipfile.ZipFile(RemoteZip()) as archive:
    selected=[i for i in archive.infolist() if not i.is_dir() and (i.filename=="ollama.exe" or i.filename.startswith("lib/ollama/") and len(pathlib.PurePosixPath(i.filename).parts)==3)]
    if not any(i.filename=="ollama.exe" for i in selected): raise RuntimeError("Unexpected archive layout")
    for index,info in enumerate(selected,1):
        target=(root/info.filename).resolve()
        if not target.is_relative_to(root): raise RuntimeError("Unsafe archive path")
        print(f"[{index}/{len(selected)}] {info.filename}",flush=True)
        data=target.read_bytes() if target.is_file() else b""
        if len(data)!=info.file_size or zlib.crc32(data)!=info.CRC:
            data=archive.read(info)
            target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
        manifest["files"].append({"path":info.filename,"sha256":hashlib.sha256(data).hexdigest(),"archive_crc32":info.CRC})
(root/"cpu-install-manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
'@
    Write-Host 'Downloading CPU components from the official Ollama archive (about 30 MB).'
    $taskCpuCode | & $taskPython -X utf8 - "$taskBaseUrl/$taskArchiveName" $taskRuntime
    if ($LASTEXITCODE -ne 0) { throw 'CPU runtime download failed. Run setup again to resume.' }
    $taskSignature = Get-AuthenticodeSignature -LiteralPath $taskExe
    if ($taskSignature.Status -ne 'Valid') { throw 'Windows could not validate the Ollama executable signature. Runtime was not started. Check internet/date/certificate settings or install Ollama from ollama.com.' }
    Write-Host ("Verified executable publisher: " + $taskSignature.SignerCertificate.Subject)
}

try {
    $taskReady = Test-LocalOllama
    if (-not $taskReady) {
        $taskProbe = New-Object System.Net.Sockets.TcpClient
        try {
            $taskProbe.Connect('127.0.0.1',11434)
            if ($taskProbe.Connected) { throw 'Port 11434 is occupied by a service that is not a responding Ollama. Stop that service before setup.' }
        } catch [System.Net.Sockets.SocketException] { } finally { $taskProbe.Dispose() }
        $taskMarker = Join-Path $taskRuntime 'residual-installed-version.txt'
        if (-not (Test-Path -LiteralPath $taskExe) -or -not (Test-Path -LiteralPath $taskMarker)) {
            if ($StartOnly) { Write-Host 'Local model is not installed yet. Open setup_local_ai.cmd.'; return }
            New-Item -ItemType Directory -Force -Path $taskDownloads, $taskRuntime, $taskModels | Out-Null
            if ($WithGpu -or $taskArch -ne 'amd64') {
            Write-Host 'Downloading official portable Ollama (about 1.5 GB on x64). No administrator access or system installation.'
            Write-Host 'Allow approximately 7 GB of disk space for download, extracted runtime and model.'
            Get-TaskDownload "$taskBaseUrl/$taskArchiveName" $taskArchive
            Write-Host 'Checking the official SHA256 checksum...'
            $taskChecksumsPath = Join-Path $taskDownloads "$taskVersion-sha256sum.txt"
            Invoke-WebRequest -Uri "$taskBaseUrl/sha256sum.txt" -OutFile $taskChecksumsPath -UseBasicParsing
            $taskChecksumLine = Get-Content -LiteralPath $taskChecksumsPath | Where-Object { $_ -match ("\s(?:\./)?" + [regex]::Escape($taskArchiveName) + '$') } | Select-Object -First 1
            if (-not $taskChecksumLine) { throw 'Official archive checksum was not found. Archive was not executed.' }
            $taskExpected = ($taskChecksumLine -split '\s+')[0]
            $taskActual = (Get-FileHash -LiteralPath $taskArchive -Algorithm SHA256).Hash
            if ($taskActual -ne $taskExpected) { throw "Checksum mismatch. Remove only $taskArchive and rerun setup. Archive was not executed." }
            Write-Host 'Extracting the verified portable runtime...'
            Expand-Archive -LiteralPath $taskArchive -DestinationPath $taskRuntime -Force
            if (-not (Test-Path -LiteralPath $taskExe)) { throw 'Ollama executable not found after extraction.' }
            } else { Install-CpuRuntime }
            Set-Content -LiteralPath $taskMarker -Value $taskVersion -Encoding ASCII
        }
        New-Item -ItemType Directory -Force -Path $taskPrivate, $taskModels | Out-Null
        Write-Host 'Starting local-only Ollama on 127.0.0.1:11434...'
        $taskProcess = Start-Process -FilePath $taskExe -ArgumentList 'serve' -WorkingDirectory $taskRuntime -WindowStyle Hidden -RedirectStandardOutput (Join-Path $taskPrivate 'ollama.stdout.log') -RedirectStandardError (Join-Path $taskPrivate 'ollama.stderr.log') -PassThru
        Set-Content -LiteralPath (Join-Path $taskPrivate 'ollama.pid') -Value $taskProcess.Id -Encoding ASCII
        for ($taskTry=0; $taskTry -lt 80; $taskTry++) {
            if (Test-LocalOllama) { $taskReady = $true; break }
            if ($taskProcess.HasExited) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $taskReady) { throw 'Ollama did not start. See private_outputs/ollama.stderr.log.' }
    } else { Write-Host 'Using the running Ollama on 127.0.0.1:11434. Its storage/settings are managed by its owner.' }
    if ($StartOnly) { return }
    if ($Model -notmatch '^[a-zA-Z0-9_.:/-]+$' -or $Model -match '(?i)(cloud|https?://)') { throw 'Select a local model tag, for example qwen3:1.7b.' }
    $taskAvailable = Invoke-RestMethod -Uri "$taskApi/api/tags" -TimeoutSec 5
    if (@($taskAvailable.models | Where-Object { $_.name -eq $Model -or $_.model -eq $Model }).Count -gt 0) {
        Write-Host "Model is already downloaded: $Model"
    } else {
        Write-Host "Downloading $Model (qwen3:1.7b is approximately 1.4 GB). Progress is shown below."
        if (Test-Path -LiteralPath $taskExe) {
            & $taskExe pull $Model
            if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Run setup_local_ai.cmd again to resume.' }
        } else {
            $taskBody = @{model=$Model; stream=$false} | ConvertTo-Json -Compress
            Invoke-RestMethod -Method Post -Uri "$taskApi/api/pull" -ContentType 'application/json' -Body $taskBody -TimeoutSec 7200 | Out-Null
        }
    }
    Write-Host 'Ready. Open Residual, choose Local Ollama in model settings, check the connection, then ask a question.'
    Write-Host "Endpoint: $taskApi    Model: $Model"
    Write-Host 'After the initial downloads this local model can work without internet.'
} catch {
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
    if ($StartOnly) { return }
    exit 1
}
