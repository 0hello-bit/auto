# Owned Mail Verification Code Service - Windows launcher (PowerShell)
#
# Usage:  right-click -> Run with PowerShell, or:  .\run.ps1
#
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. Create a virtual environment on first run.
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..." -ForegroundColor Cyan
    python -m venv .venv
}

# 2. Activate it.
. .\.venv\Scripts\Activate.ps1

# 3. Install dependencies.
Write-Host "Installing dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip | Out-Null
pip install -r requirements.txt

# 4. Create .env from the example on first run.
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - EDIT IT and set a strong API_KEY before use." -ForegroundColor Yellow
}

# 5. Read HOST/PORT from .env (simple parse) with sane fallbacks.
$bindHost = "127.0.0.1"
$bindPort = "5050"
foreach ($line in Get-Content ".env") {
    if ($line -match "^\s*HOST\s*=\s*(.+?)\s*$") { $bindHost = $Matches[1] }
    if ($line -match "^\s*PORT\s*=\s*(.+?)\s*$") { $bindPort = $Matches[1] }
}

Write-Host "Starting service on http://${bindHost}:${bindPort} ..." -ForegroundColor Green
uvicorn app.main:app --host $bindHost --port $bindPort
