function Stop-JitRecordedServices {
    param([object[]]$Services)

    $jitRuntime = $Services | Where-Object { $_.name -eq 'runtime-api' } | Select-Object -First 1
    if ($jitRuntime) {
        $jitRuntimeProcess = Get-Process -Id $jitRuntime.pid -ErrorAction SilentlyContinue
        if ($jitRuntimeProcess -and $jitRuntimeProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$jitRuntime.started_at).ToUniversalTime().Ticks) {
            try {
                $jitRuns = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/runs' -TimeoutSec 3
                foreach ($jitRun in $jitRuns) {
                    if ($jitRun.status -in @('QUEUED', 'RUNNING', 'CANCELLING', 'AWAITING_CONFIRMATION')) {
                        Invoke-RestMethod -Uri ('http://127.0.0.1:8000/api/runs/' + $jitRun.run_id + '/cancel') -Method Post -TimeoutSec 3 | Out-Null
                    }
                }
                $jitDeadline = [datetime]::UtcNow.AddSeconds(15)
                do {
                    $jitCheckRuns = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/runs' -TimeoutSec 3
                    $jitPending = @($jitCheckRuns | Where-Object { $_.status -in @('QUEUED', 'RUNNING', 'CANCELLING', 'AWAITING_CONFIRMATION') })
                    if ($jitPending.Count -eq 0) { break }
                    Start-Sleep -Milliseconds 200
                } while ([datetime]::UtcNow -lt $jitDeadline)
            }
            catch { Write-Verbose 'The runtime could not respond to shutdown cancellation.' }
        }
    }
    foreach ($jitService in $Services) {
        $jitIdentities = @()
        if ($jitService.worker_pid) {
            $jitIdentities += @{ pid = $jitService.worker_pid; started_at = $jitService.worker_started_at }
        }
        $jitIdentities += @{ pid = $jitService.pid; started_at = $jitService.started_at }
        foreach ($jitIdentity in $jitIdentities) {
            $jitProcess = Get-Process -Id $jitIdentity.pid -ErrorAction SilentlyContinue
            if ($jitProcess -and $jitProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$jitIdentity.started_at).ToUniversalTime().Ticks) {
                try {
                    Stop-Process -Id $jitProcess.Id -ErrorAction Stop
                    $jitProcess.WaitForExit(3000) | Out-Null
                }
                catch {
                    $jitStillRunning = Get-Process -Id $jitIdentity.pid -ErrorAction SilentlyContinue
                    if ($jitStillRunning -and $jitStillRunning.StartTime.ToUniversalTime().Ticks -eq ([datetime]$jitIdentity.started_at).ToUniversalTime().Ticks) { throw }
                }
            }
        }
        Write-Host "Stopped $($jitService.name)."
    }
}
