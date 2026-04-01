# Start Crypto Investment Platform Bot for Windows
# Powered by Freqtrade

param(
    [switch]$DryRun,
    [switch]$Live
)

$ErrorActionPreference = "Stop"

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host $text.PadLeft(30 + ($text.Length / 2)).PadRight(60) -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host ""
}

Write-Header "Starting Crypto Investment Platform"

# Check if config exists
if (-not (Test-Path "config/config.json")) {
    Write-Host "[X] Error: Configuration file not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please run the configuration wizard first:" -ForegroundColor White
    Write-Host "  python config_wizard.py" -ForegroundColor Green
    exit 1
}

# Check if virtual environment exists
if (-not (Test-Path "venv")) {
    Write-Host "[X] Error: Virtual environment not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please run setup first:" -ForegroundColor White
    Write-Host "  .\setup.ps1" -ForegroundColor Green
    exit 1
}

# Activate virtual environment
& ".\venv\Scripts\Activate.ps1"

# Determine mode
$mode = "dry_run"

if ($Live) {
    Write-Host "[!] WARNING: Starting in LIVE mode with REAL money!" -ForegroundColor Red
    $confirm = Read-Host "Are you sure? (yes/no)"
    if ($confirm -ne "yes") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        exit 0
    }
    $mode = "live"
} else {
    # Default to dry run
    Write-Host "[OK] Starting in DRY-RUN mode (virtual money)" -ForegroundColor Green
    $mode = "dry_run"
}

# Create necessary directories
if (-not (Test-Path "user_data/logs")) {
    New-Item -ItemType Directory -Path "user_data/logs" -Force | Out-Null
}
if (-not (Test-Path "user_data/data")) {
    New-Item -ItemType Directory -Path "user_data/data" -Force | Out-Null
}

# Display connection info
Write-Host ""
Write-Host "Starting Freqtrade..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Monitor your bot at: " -NoNewline -ForegroundColor Cyan
Write-Host "http://localhost:8080" -ForegroundColor Green
Write-Host "Username: " -NoNewline -ForegroundColor Cyan
Write-Host "freqtrader" -ForegroundColor Green
Write-Host "Password: " -NoNewline -ForegroundColor Cyan
Write-Host "freqtrader" -ForegroundColor Green
Write-Host ""
Write-Host "Press Ctrl+C to stop the bot" -ForegroundColor Yellow
Write-Host ""

# Run freqtrade
$arguments = @(
    "trade",
    "--config", "config/config.json",
    "--strategy-path", "strategies",
    "--datadir", "user_data/data",
    "--logfile", "user_data/logs/freqtrade.log",
    "--db-url", "sqlite:///user_data/tradesv3.sqlite"
)

& freqtrade @arguments
