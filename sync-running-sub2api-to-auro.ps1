<#
.SYNOPSIS
    Sync the currently running local Sub2API settings into this auro-reg folder.

.DESCRIPTION
    Reads the active Sub2API .env from the local sub2api-win directory and updates
    web-register-sub2api-automation\.env so Project B can log in to the same
    Sub2API instance that Codex is using on 127.0.0.1:8080.
#>
[CmdletBinding()]
param(
    [string]$Sub2ApiDir = "C:\Users\24668\Documents\Codex\2026-06-05\wei-shaw-sub2api-https-github-com\work\sub2api-win"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$ProjectBEnv = Join-Path $Root "web-register-sub2api-automation\.env"
$Sub2ApiEnv = Join-Path $Sub2ApiDir ".env"

function Read-DotEnv {
    param([string]$Path)

    $map = @{}
    foreach ($line in [System.IO.File]::ReadAllLines($Path, [System.Text.UTF8Encoding]::new($false))) {
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $value = $line.Substring($idx + 1)
        $map[$key] = $value
    }
    return $map
}

function Set-DotEnvValue {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [string]$Key,
        [string]$Value
    )

    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match "^\s*$([regex]::Escape($Key))=") {
            $Lines[$i] = "$Key=$Value"
            return
        }
    }
    $Lines.Add("$Key=$Value")
}

if (-not (Test-Path -LiteralPath $Sub2ApiEnv)) {
    throw "Sub2API .env not found: $Sub2ApiEnv"
}
if (-not (Test-Path -LiteralPath $ProjectBEnv)) {
    throw "Project B .env not found: $ProjectBEnv"
}

$sub = Read-DotEnv $Sub2ApiEnv
$port = if ($sub["SERVER_PORT"]) { $sub["SERVER_PORT"] } else { "8080" }
if (-not $sub["ADMIN_EMAIL"] -or -not $sub["ADMIN_PASSWORD"]) {
    throw "Sub2API ADMIN_EMAIL or ADMIN_PASSWORD is empty in: $Sub2ApiEnv"
}

$backup = "{0}.bak-{1}" -f $ProjectBEnv, (Get-Date -Format "yyyyMMdd-HHmmss")
Copy-Item -LiteralPath $ProjectBEnv -Destination $backup -Force

$lines = [System.Collections.Generic.List[string]]::new()
$lines.AddRange([string[]][System.IO.File]::ReadAllLines($ProjectBEnv, [System.Text.UTF8Encoding]::new($false)))

Set-DotEnvValue $lines "SUB2API_BASE" "http://127.0.0.1:$port"
Set-DotEnvValue $lines "SUB2API_ADMIN_EMAIL" $sub["ADMIN_EMAIL"]
Set-DotEnvValue $lines "SUB2API_ADMIN_PASSWORD" $sub["ADMIN_PASSWORD"]

[System.IO.File]::WriteAllLines($ProjectBEnv, [string[]]$lines, [System.Text.UTF8Encoding]::new($true))

Write-Host "Synced running Sub2API into auro-reg Project B." -ForegroundColor Green
Write-Host ("Sub2API dir:  {0}" -f $Sub2ApiDir)
Write-Host ("Sub2API base: http://127.0.0.1:{0}" -f $port)
Write-Host ("Project B env: {0}" -f $ProjectBEnv)
Write-Host ("Backup saved:  {0}" -f $backup) -ForegroundColor DarkGray
