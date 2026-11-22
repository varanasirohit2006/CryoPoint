$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'process_helpers.ps1')
$jitProject = Split-Path -Parent $PSScriptRoot
$jitStatePath = Join-Path $jitProject '.data\services.json'
$jitState = Get-Content -LiteralPath $jitStatePath -Raw | ConvertFrom-Json
$jitRuntime = $jitState.services | Where-Object { $_.name -eq 'runtime-api' } | Select-Object -First 1
if (-not $jitRuntime) { throw 'Start project services with scripts/dev.ps1 first.' }
Stop-JitRecordedServices -Services @($jitRuntime)
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $jitProject '.data\browsers'
$env:PYTHONPATH = (Join-Path $jitProject 'backend\src') + ';' + $jitProject
$jitProcess = Start-Process -FilePath (Join-Path $jitProject '.venv\Scripts\python.exe') -ArgumentList @('-m', 'uvicorn', 'jit_ui.api.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $jitProject -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $jitProject '.data\logs\runtime-api.out.log') -RedirectStandardError (Join-Path $jitProject '.data\logs\runtime-api.err.log')
$jitRecord = @{ name='runtime-api'; pid=$jitProcess.Id; started_at=$jitProcess.StartTime.ToUniversalTime().ToString('o') }
$jitState.services = @($jitState.services | Where-Object { $_.name -ne 'runtime-api' }) + @($jitRecord)
$jitState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $jitStatePath -Encoding utf8
$jitDeadline = [datetime]::UtcNow.AddSeconds(120)
$jitReady = $false
while ([datetime]::UtcNow -lt $jitDeadline) {
    try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/ready' -TimeoutSec 5 | Out-Null; $jitReady=$true; break } catch { Start-Sleep -Milliseconds 500 }
}
if (-not $jitReady) { throw 'Runtime restart timed out. Read .data/logs/runtime-api.err.log.' }
$jitWorkerLine = Get-Content -LiteralPath (Join-Path $jitProject '.data\logs\runtime-api.err.log') | Select-String -Pattern 'Started server process \[(\d+)\]' | Select-Object -Last 1
if ($jitWorkerLine) {
    $jitWorker = Get-Process -Id ([int]$jitWorkerLine.Matches[0].Groups[1].Value)
    $jitRecord.worker_pid = $jitWorker.Id
    $jitRecord.worker_started_at = $jitWorker.StartTime.ToUniversalTime().ToString('o')
    $jitState | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $jitStatePath -Encoding utf8
}
Write-Host 'Runtime restarted: http://127.0.0.1:8000'
