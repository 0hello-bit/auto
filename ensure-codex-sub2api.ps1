<#
.SYNOPSIS
    Check and optionally start the local Sub2API gateway used by Codex.

.DESCRIPTION
    This is a non-spending health check. It does not call /responses.
    It verifies:
      - Sub2API is listening on 127.0.0.1:8080
      - Codex config points OpenAI provider at http://127.0.0.1:8080
      - Codex model is gpt-5.5, not gpt-5.4
      - The local proxy on 127.0.0.1:7890 is available for upstream traffic

.EXAMPLE
    .\ensure-codex-sub2api.ps1
    .\ensure-codex-sub2api.ps1 -Start
#>
[CmdletBinding()]
param(
    [switch]$Start,
    [switch]$FixCodexConfig
)

$ErrorActionPreference = "Stop"

$Sub2ApiDir = "C:\Users\24668\Documents\Codex\2026-06-05\wei-shaw-sub2api-https-github-com\work\sub2api-win"
$Sub2ApiExe = Join-Path $Sub2ApiDir "sub2api.exe"
$CodexConfig = "C:\Users\24668\.codex\config.toml"
$ExpectedBaseUrl = "http://127.0.0.1:8080"
$ExpectedModel = "gpt-5.5"

function Test-Port([int]$Port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800) -and $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

function Get-Listener([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Start-Sub2ApiNoProxy {
    if (-not (Test-Path $Sub2ApiExe)) {
        throw "sub2api.exe not found: $Sub2ApiExe"
    }

    $oldHttpProxy = $env:HTTP_PROXY
    $oldHttpsProxy = $env:HTTPS_PROXY
    $oldLowerHttpProxy = $env:http_proxy
    $oldLowerHttpsProxy = $env:https_proxy
    try {
        $env:HTTP_PROXY = ""
        $env:HTTPS_PROXY = ""
        $env:http_proxy = ""
        $env:https_proxy = ""
        Start-Process -FilePath $Sub2ApiExe -WorkingDirectory $Sub2ApiDir -WindowStyle Hidden
    } finally {
        $env:HTTP_PROXY = $oldHttpProxy
        $env:HTTPS_PROXY = $oldHttpsProxy
        $env:http_proxy = $oldLowerHttpProxy
        $env:https_proxy = $oldLowerHttpsProxy
    }
}

function Get-CodexConfigValue([string]$Key) {
    if (-not (Test-Path $CodexConfig)) { return $null }
    $line = Get-Content -LiteralPath $CodexConfig |
        Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" } |
        Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -replace "^\s*$([regex]::Escape($Key))\s*=\s*", "").Trim().Trim('"')
}

function Repair-CodexConfig {
    if (-not (Test-Path $CodexConfig)) {
        throw "Codex config not found: $CodexConfig"
    }

    $backup = "{0}.bak-{1}" -f $CodexConfig, (Get-Date -Format "yyyyMMdd-HHmmss")
    Copy-Item -LiteralPath $CodexConfig -Destination $backup -Force

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.AddRange([string[]](Get-Content -LiteralPath $CodexConfig))

    $firstTableIndex = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[.+\]\s*$') {
            $firstTableIndex = $i
            break
        }
    }

    $topKeys = [ordered]@{
        model_provider = 'model_provider = "OpenAI"'
        model = 'model = "gpt-5.5"'
        review_model = 'review_model = "gpt-5.5"'
    }
    foreach ($key in $topKeys.Keys) {
        $found = $false
        for ($i = 0; $i -lt $firstTableIndex; $i++) {
            if ($lines[$i] -match "^\s*$([regex]::Escape($key))\s*=") {
                $lines[$i] = $topKeys[$key]
                $found = $true
                break
            }
        }
        if (-not $found) {
            $lines.Insert($firstTableIndex, $topKeys[$key])
            $firstTableIndex++
        }
    }

    $providerHeader = '[model_providers.OpenAI]'
    $providerIndex = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].Trim() -eq $providerHeader) {
            $providerIndex = $i
            break
        }
    }
    if ($providerIndex -lt 0) {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim() -ne "") {
            $lines.Add("")
        }
        $lines.Add($providerHeader)
        $lines.Add('name = "OpenAI"')
        $lines.Add('base_url = "http://127.0.0.1:8080"')
        $lines.Add('wire_api = "responses"')
        $lines.Add('requires_openai_auth = true')
    } else {
        $sectionEnd = $lines.Count
        for ($i = $providerIndex + 1; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^\s*\[.+\]\s*$') {
                $sectionEnd = $i
                break
            }
        }

        $baseUrlFound = $false
        for ($i = $providerIndex + 1; $i -lt $sectionEnd; $i++) {
            if ($lines[$i] -match '^\s*base_url\s*=') {
                $lines[$i] = 'base_url = "http://127.0.0.1:8080"'
                $baseUrlFound = $true
                break
            }
        }
        if (-not $baseUrlFound) {
            $lines.Insert($providerIndex + 1, 'base_url = "http://127.0.0.1:8080"')
        }
    }

    [System.IO.File]::WriteAllLines($CodexConfig, [string[]]$lines, [System.Text.UTF8Encoding]::new($true))
    Write-Host ("Backup saved: {0}" -f $backup) -ForegroundColor DarkGray
}

Write-Host "== Codex -> Sub2API health check ==" -ForegroundColor Cyan

if ($FixCodexConfig) {
    Repair-CodexConfig
    Write-Host "Codex config repaired." -ForegroundColor Green
}

$listener = Get-Listener 8080
if (-not $listener -and $Start) {
    Write-Host "Sub2API is not listening on 8080; starting it..." -ForegroundColor Yellow
    Start-Sub2ApiNoProxy
    foreach ($i in 1..20) {
        Start-Sleep -Milliseconds 500
        $listener = Get-Listener 8080
        if ($listener) { break }
    }
}

if ($listener) {
    $proc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    Write-Host ("Sub2API 8080: OK pid={0} process={1}" -f $listener.OwningProcess, $proc.ProcessName) -ForegroundColor Green
    Write-Host ("Sub2API path: {0}" -f $proc.Path)
} else {
    Write-Host "Sub2API 8080: DOWN" -ForegroundColor Red
}

if (Test-Port 7890) {
    Write-Host "Local proxy 7890: OK" -ForegroundColor Green
} else {
    Write-Host "Local proxy 7890: DOWN (Sub2API may fail upstream requests if accounts/proxy settings need it)" -ForegroundColor Yellow
}

$modelProvider = Get-CodexConfigValue "model_provider"
$model = Get-CodexConfigValue "model"
$reviewModel = Get-CodexConfigValue "review_model"
$baseUrl = Get-CodexConfigValue "base_url"

Write-Host ("Codex model_provider: {0}" -f $modelProvider)
Write-Host ("Codex model:          {0}" -f $model)
Write-Host ("Codex review_model:   {0}" -f $reviewModel)
Write-Host ("Codex base_url:       {0}" -f $baseUrl)

if ($modelProvider -ne "OpenAI" -or $baseUrl -ne $ExpectedBaseUrl -or $model -ne $ExpectedModel) {
    Write-Host "Codex config: MISMATCH" -ForegroundColor Red
    Write-Host "Expected: model_provider=OpenAI, model=gpt-5.5, base_url=http://127.0.0.1:8080"
    Write-Host "Run: .\ensure-codex-sub2api.ps1 -FixCodexConfig"
} else {
    Write-Host "Codex config: OK" -ForegroundColor Green
}

try {
    $resp = Invoke-WebRequest -Uri "$ExpectedBaseUrl/api/v1/settings/public" -UseBasicParsing -TimeoutSec 5
    Write-Host ("Sub2API public API: OK status={0}" -f $resp.StatusCode) -ForegroundColor Green
} catch {
    Write-Host ("Sub2API public API: FAIL {0}" -f $_.Exception.Message) -ForegroundColor Red
}

Write-Host ""
Write-Host "Important: gpt-5.4 caused prior 502s with ChatGPT-backed Codex accounts; keep Codex on gpt-5.5." -ForegroundColor Yellow
