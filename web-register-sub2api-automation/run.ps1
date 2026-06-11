<#
.SYNOPSIS
    Run / set up the Web Register And Sub2API Import Automation service.

.DESCRIPTION
    Default: start the FastAPI service (reads HOST/PORT from .env).
    -Setup : create a virtualenv, install requirements, install the Playwright
             Chromium browser, and copy .env.example -> .env if missing.

.EXAMPLE
    .\run.ps1 -Setup     # one-time setup
    .\run.ps1            # start the service
#>
[CmdletBinding()]
param(
    [switch]$Setup
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if ($Setup) {
    Write-Host "==> Creating virtual environment (.venv)..." -ForegroundColor Cyan
    if (-not (Test-Path $venvPython)) {
        python -m venv .venv
    }

    Write-Host "==> Upgrading pip..." -ForegroundColor Cyan
    & $venvPython -m pip install --upgrade pip

    Write-Host "==> Installing Python requirements..." -ForegroundColor Cyan
    & $venvPython -m pip install -r requirements.txt

    Write-Host "==> Installing Playwright Chromium browser..." -ForegroundColor Cyan
    & $venvPython -m playwright install chromium

    if (-not (Test-Path ".\.env")) {
        Write-Host "==> Creating .env from .env.example (please edit it!)" -ForegroundColor Yellow
        Copy-Item ".\.env.example" ".\.env"
    }

    if (-not (Test-Path ".\emails.txt")) {
        Write-Host "==> Creating emails.txt from emails.example.txt (please edit it!)" -ForegroundColor Yellow
        Copy-Item ".\emails.example.txt" ".\emails.txt"
    }

    Write-Host "==> Setup complete. Edit .env and emails.txt, then run: .\run.ps1" -ForegroundColor Green
    return
}

# ---- normal run ----
if (-not (Test-Path ".\.env")) {
    Write-Warning ".env not found. Run '.\run.ps1 -Setup' first, or copy .env.example to .env."
}

if (Test-Path $venvPython) {
    Write-Host "==> Starting service with .venv python..." -ForegroundColor Cyan
    & $venvPython -m app.main
}
else {
    Write-Host "==> .venv not found; starting service with system python..." -ForegroundColor Yellow
    python -m app.main
}
