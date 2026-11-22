param(
    [switch]$Semantic,
    [switch]$SkipBrowsers
)

$ErrorActionPreference = 'Stop'
$jitProject = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $jitProject
try {
    $env:UV_CACHE_DIR = Join-Path $jitProject '.data\cache\uv'
    $env:PIP_CACHE_DIR = Join-Path $jitProject '.data\cache\pip'
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $jitProject '.data\browsers'
    $jitPython = Join-Path $jitProject '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $jitPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed. Install Python 3.12 or newer.' }
    }
    $jitUv = Join-Path $jitProject '.venv\Scripts\uv.exe'
    if (-not (Test-Path -LiteralPath $jitUv)) {
        & $jitPython -m pip install --disable-pip-version-check uv
        if ($LASTEXITCODE -ne 0) { throw 'Installing uv failed.' }
    }
    $jitSync = @('sync', '--frozen', '--extra', 'dev')
    if ($Semantic) { $jitSync += @('--extra', 'semantic') }
    & $jitUv @jitSync
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if ($Semantic) {
        & $jitPython scripts\provision_embeddings.py
        if ($LASTEXITCODE -ne 0) { throw 'Local semantic model provisioning failed.' }
    }
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed. Install Node 22.12 or newer.' }
    if (-not $SkipBrowsers) {
        & $jitPython -m playwright install chromium
        if ($LASTEXITCODE -ne 0) { throw 'Chromium installation failed.' }
    }
    $jitEnv = Join-Path $jitProject '.env'
    if (-not (Test-Path -LiteralPath $jitEnv)) {
        Copy-Item -LiteralPath (Join-Path $jitProject '.env.example') -Destination $jitEnv
    }
    Write-Host 'Setup complete. Configure .env, then run .\scripts\dev.ps1.'
    if ($Semantic) { Write-Host 'Set JIT_EMBEDDING_BACKEND=chroma in .env to use the provisioned semantic router.' }
}
finally { Pop-Location }
