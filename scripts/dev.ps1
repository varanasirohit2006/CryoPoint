param([switch]$Detach, [switch]$Preview, [ValidateRange(15, 600)][int]$StartupTimeoutSeconds = 120)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'process_helpers.ps1')
$jitProject = Split-Path -Parent $PSScriptRoot
$jitPython = Join-Path $jitProject '.venv\Scripts\python.exe'
$jitVite = Join-Path $jitProject 'node_modules\vite\bin\vite.js'
if (-not (Test-Path -LiteralPath $jitPython) -or -not (Test-Path -LiteralPath $jitVite)) {
    throw 'Run .\scripts\setup.ps1 first.'
}
if ($Preview -and (-not (Test-Path -LiteralPath (Join-Path $jitProject 'frontend\dist\index.html')) -or -not (Test-Path -LiteralPath (Join-Path $jitProject 'chaos_testbed\web\dist\index.html')))) {
    throw 'Run npm.cmd run build before starting preview mode.'
}
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $jitProject '.data\browsers'
$env:PYTHONPATH = (Join-Path $jitProject 'backend\src') + ';' + $jitProject
$jitLogs = Join-Path $jitProject '.data\logs'
New-Item -ItemType Directory -Path $jitLogs -Force | Out-Null
$jitStatePath = Join-Path $jitProject '.data\services.json'
if (Test-Path -LiteralPath $jitStatePath) {
    $jitPrevious = Get-Content -LiteralPath $jitStatePath -Raw | ConvertFrom-Json
    foreach ($jitService in $jitPrevious.services) {
        $jitExisting = Get-Process -Id $jitService.pid -ErrorAction SilentlyContinue
        if ($jitExisting -and $jitExisting.StartTime.ToUniversalTime().Ticks -eq ([datetime]$jitService.started_at).ToUniversalTime().Ticks) {
            throw 'Project services are already running. Use .\scripts\stop.ps1 before restarting.'
        }
    }
}
$jitPorts = @(8000, 8001, 5173, 3000)
foreach ($jitPort in $jitPorts) {
    $jitSocket = [System.Net.Sockets.TcpClient]::new()
    try {
        if ($jitSocket.ConnectAsync('127.0.0.1', $jitPort).Wait(300) -and $jitSocket.Connected) {
            throw "Port $jitPort is already in use. Stop its service before launching this project."
        }
    }
    catch [System.AggregateException] { }
    finally { $jitSocket.Dispose() }
}
$jitNode = (Get-Command node.exe -ErrorAction Stop).Source
& (Join-Path $PSScriptRoot 'start_ollama.ps1')
$jitViteCommand = @()
if ($Preview) { $jitViteCommand = @('preview') }
$jitDefinitions = @(
    @{ name = 'testbed-api'; executable = $jitPython; arguments = @('-m', 'uvicorn', 'chaos_testbed.api.main:app', '--host', '127.0.0.1', '--port', '8001') },
    @{ name = 'runtime-api'; executable = $jitPython; arguments = @('-m', 'uvicorn', 'jit_ui.api.main:app', '--host', '127.0.0.1', '--port', '8000') },
    @{ name = 'testbed-web'; executable = $jitNode; arguments = @(('"' + $jitVite + '"')) + $jitViteCommand + @('chaos_testbed/web', '--configLoader', 'native', '--host', '127.0.0.1', '--port', '3000', '--strictPort') },
    @{ name = 'dashboard'; executable = $jitNode; arguments = @(('"' + $jitVite + '"')) + $jitViteCommand + @('frontend', '--configLoader', 'native', '--host', '127.0.0.1', '--port', '5173', '--strictPort') }
)
$jitStarted = @()
$jitKeepRunning = $false
try {
    foreach ($jitDefinition in $jitDefinitions) {
        $windowStyle = if ($jitDefinition.name -eq 'runtime-api') { 'Minimized' } else { 'Hidden' }
        $jitProcess = Start-Process -FilePath $jitDefinition.executable -ArgumentList $jitDefinition.arguments -WorkingDirectory $jitProject -WindowStyle $windowStyle -PassThru -RedirectStandardOutput (Join-Path $jitLogs ($jitDefinition.name + '.out.log')) -RedirectStandardError (Join-Path $jitLogs ($jitDefinition.name + '.err.log'))
        $jitStarted += @{ name = $jitDefinition.name; pid = $jitProcess.Id; started_at = $jitProcess.StartTime.ToUniversalTime().ToString('o') }
    }
    @{ project_root = $jitProject; services = $jitStarted } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $jitStatePath -Encoding utf8
    $jitDeadline = [DateTime]::UtcNow.AddSeconds($StartupTimeoutSeconds)
    $jitPending = @{
        'runtime-api' = 'http://127.0.0.1:8000/api/ready'
        'testbed-api' = 'http://127.0.0.1:8001/api/health'
        'testbed-web' = 'http://127.0.0.1:3000/shop'
        'dashboard' = 'http://127.0.0.1:5173'
    }
    $jitLastPending = ''
    $jitReady = $false
    while ([DateTime]::UtcNow -lt $jitDeadline) {
        $jitReady = $true
        foreach ($jitService in $jitStarted) {
            if (-not (Get-Process -Id $jitService.pid -ErrorAction SilentlyContinue)) {
                throw "The $($jitService.name) service exited. Read .data\logs\$($jitService.name).err.log."
            }
        }
        foreach ($jitName in @($jitPending.Keys)) {
            try {
                Invoke-WebRequest -Uri $jitPending[$jitName] -UseBasicParsing -TimeoutSec 5 | Out-Null
                $jitPending.Remove($jitName)
            }
            catch { $jitReady = $false }
        }
        $jitReady = $jitPending.Count -eq 0
        if ($jitReady) { break }
        $jitPendingNames = ($jitPending.Keys | Sort-Object) -join ', '
        if ($jitPendingNames -ne $jitLastPending) {
            Write-Host "Waiting for: $jitPendingNames"
            $jitLastPending = $jitPendingNames
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $jitReady) {
        throw "Startup timed out after $StartupTimeoutSeconds seconds waiting for: $jitPendingNames. Inspect their logs under .data\logs."
    }
    foreach ($jitService in $jitStarted) {
        if ($jitService.name -in @('runtime-api', 'testbed-api')) {
            $jitLog = Get-Content -LiteralPath (Join-Path $jitLogs ($jitService.name + '.err.log'))
            $jitWorkerLine = $jitLog | Select-String -Pattern 'Started server process \[(\d+)\]' | Select-Object -Last 1
            if ($jitWorkerLine) {
                $jitWorker = Get-Process -Id ([int]$jitWorkerLine.Matches[0].Groups[1].Value) -ErrorAction Stop
                if ($jitWorker.StartTime.ToUniversalTime().Ticks -ge ([datetime]$jitService.started_at).ToUniversalTime().Ticks) {
                    $jitService.worker_pid = $jitWorker.Id
                    $jitService.worker_started_at = $jitWorker.StartTime.ToUniversalTime().ToString('o')
                }
            }
        }
    }
    @{ project_root = $jitProject; services = $jitStarted } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $jitStatePath -Encoding utf8
    Write-Host 'Dashboard: http://127.0.0.1:5173'
    Write-Host 'Testbed:   http://127.0.0.1:3000/shop and /admin'
    Write-Host 'API docs:  http://127.0.0.1:8000/docs'
    if ($Detach) {
        $jitKeepRunning = $true
        Write-Host 'Services are running in the background. Stop them with .\scripts\stop.ps1.'
    }
    else {
        Write-Host 'Press Ctrl+C to stop all four services.'
        while ($true) {
            foreach ($jitService in $jitStarted) {
                if (-not (Get-Process -Id $jitService.pid -ErrorAction SilentlyContinue)) {
                    throw "The $($jitService.name) service exited. Inspect .data\logs."
                }
            }
            Start-Sleep -Milliseconds 500
        }
    }
}
finally {
    if (-not $jitKeepRunning) {
        Stop-JitRecordedServices -Services $jitStarted
        if (Test-Path -LiteralPath $jitStatePath) { Remove-Item -LiteralPath $jitStatePath }
    }
}
