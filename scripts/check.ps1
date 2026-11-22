param([switch]$BrowserSmoke)

$ErrorActionPreference = 'Stop'
$jitProject = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $jitProject
try {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $jitProject '.data\browsers'
    $env:PYTHONPATH = (Join-Path $jitProject 'backend\src') + ';' + $jitProject
    $jitPython = Join-Path $jitProject '.venv\Scripts\python.exe'
    $jitTestTemp = Join-Path $jitProject ('.data\tests-' + [Guid]::NewGuid().ToString('N'))
    & $jitPython -m ruff check backend chaos_testbed/api chaos_testbed/tests benchmarks scripts/provision_embeddings.py
    if ($LASTEXITCODE -ne 0) { throw 'Python lint checks failed.' }
    & $jitPython -m pytest --basetemp $jitTestTemp
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }
    npm.cmd run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'TypeScript checks failed.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend builds failed.' }
    if ($BrowserSmoke) {
        & $jitPython benchmarks\run_suite.py --provider fixture --trials 1 --sites shop --mutations obstacle --skip-baselines
        if ($LASTEXITCODE -ne 0) { throw 'Browser smoke verification failed.' }
    }
    Write-Host 'Checks passed.'
}
finally { Pop-Location }
