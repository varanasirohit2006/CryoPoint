$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'process_helpers.ps1')
$jitProject = Split-Path -Parent $PSScriptRoot
$jitStatePath = Join-Path $jitProject '.data\services.json'
if (-not (Test-Path -LiteralPath $jitStatePath)) {
    Write-Host 'No recorded project services.'
    return
}
$jitState = Get-Content -LiteralPath $jitStatePath -Raw | ConvertFrom-Json
if ($jitState.project_root -ne $jitProject) { throw 'The service record belongs to a different project.' }
Stop-JitRecordedServices -Services $jitState.services
Remove-Item -LiteralPath $jitStatePath
