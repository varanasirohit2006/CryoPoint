$ErrorActionPreference = 'Stop'
$jitProject = Split-Path -Parent $PSScriptRoot
$jitOllamaUrl = 'http://127.0.0.1:11434'
$jitEnvPath = Join-Path $jitProject '.env'
if (Test-Path -LiteralPath $jitEnvPath) {
    $jitUrlLine = Get-Content -LiteralPath $jitEnvPath | Where-Object { $_ -match '^\s*JIT_OLLAMA_URL\s*=' } | Select-Object -Last 1
    if ($jitUrlLine) { $jitOllamaUrl = ($jitUrlLine -split '=', 2)[1].Trim().Trim('"', "'") }
}
if ($env:JIT_OLLAMA_URL) { $jitOllamaUrl = $env:JIT_OLLAMA_URL }
$jitOllamaUri = $null
if (-not [Uri]::TryCreate($jitOllamaUrl, [UriKind]::Absolute, [ref]$jitOllamaUri) -or
    $jitOllamaUri.Scheme -ne 'http' -or $jitOllamaUri.Host -notin @('127.0.0.1', 'localhost', '[::1]') -or $jitOllamaUri.Port -ne 11434) {
    return
}
$jitSocket = [System.Net.Sockets.TcpClient]::new()
try {
    try { if ($jitSocket.ConnectAsync('127.0.0.1', 11434).Wait(500) -and $jitSocket.Connected) { return } }
    catch [System.AggregateException] { }
}
finally { $jitSocket.Dispose() }
$jitCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
$jitExecutable = if ($jitCommand) { $jitCommand.Source } else { Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe' }
if (-not (Test-Path -LiteralPath $jitExecutable)) {
    Write-Host 'Ollama is not installed. The dashboard can still use Gemini.'
    return
}
$jitLogs = Join-Path $jitProject '.data\logs'
New-Item -ItemType Directory -Path $jitLogs -Force | Out-Null
$jitProcess = Start-Process -FilePath $jitExecutable -ArgumentList 'serve' -WorkingDirectory $jitProject -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $jitLogs 'ollama.out.log') -RedirectStandardError (Join-Path $jitLogs 'ollama.err.log')
@{pid=$jitProcess.Id;started_at=$jitProcess.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $jitProject '.data\ollama-service.json') -Encoding utf8
Write-Host 'Started the installed Ollama server. It stays running when project services stop.'
