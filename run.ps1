# Splice launcher (Windows). Creates the venv on first run, installs
# dependencies, then starts the app.
#
#   .\run.ps1              start the app
#   .\run.ps1 -Test        run the test suite instead
#   .\run.ps1 -Update      reinstall dependencies first

param(
    [switch]$Test,
    [switch]$Update,
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    py -3 -m venv .venv
    $Update = $true
}

if ($Update) {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $python -m pip install --upgrade pip --quiet
    & $python -m pip install -r requirements.txt --quiet
}

if ($Test) {
    & $python -m pytest -q
    exit $LASTEXITCODE
}

Write-Host "Starting Splice on http://localhost:$Port" -ForegroundColor Green
& $python -m streamlit run app.py --server.port $Port
