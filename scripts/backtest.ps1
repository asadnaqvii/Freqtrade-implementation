# Strategy Backtesting Script for Windows
# Powered by Freqtrade

param(
    [string]$Strategy
)

$ErrorActionPreference = "Stop"

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host $text.PadLeft(30 + ($text.Length / 2)).PadRight(60) -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host ""
}

Write-Header "Strategy Backtesting"

# Check if config exists
if (-not (Test-Path "config/config.json")) {
    Write-Host "[X] Error: Configuration file not found!" -ForegroundColor Red
    Write-Host "Please run: python config_wizard.py" -ForegroundColor White
    exit 1
}

# Check if virtual environment exists
if (-not (Test-Path "venv")) {
    Write-Host "[X] Error: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Please run: .\setup.ps1" -ForegroundColor White
    exit 1
}

# Activate virtual environment
& ".\venv\Scripts\Activate.ps1"

# Get strategy name if not provided
if (-not $Strategy) {
    Write-Host "Available strategies:" -ForegroundColor White
    Write-Host "1. ConservativeRSI (Low Risk)" -ForegroundColor Green
    Write-Host "2. EMACrossover (Medium Risk)" -ForegroundColor Yellow
    Write-Host "3. BollingerBreakout (Medium-High Risk)" -ForegroundColor Red
    Write-Host ""

    $choice = Read-Host "Select strategy (1-3)"

    switch ($choice) {
        "1" { $Strategy = "ConservativeRSI" }
        "2" { $Strategy = "EMACrossover" }
        "3" { $Strategy = "BollingerBreakout" }
        default {
            Write-Host "[X] Invalid choice" -ForegroundColor Red
            exit 1
        }
    }
}

Write-Host ""
Write-Host "Selected strategy: $Strategy" -ForegroundColor Green

# Get time range
Write-Host ""
Write-Host "Select time range for backtest:" -ForegroundColor White
Write-Host "1. Last 7 days"
Write-Host "2. Last 30 days"
Write-Host "3. Last 90 days"
Write-Host "4. Custom range"

$rangeChoice = Read-Host "Select (1-4)"

$today = Get-Date
switch ($rangeChoice) {
    "1" {
        $startDate = $today.AddDays(-7).ToString("yyyyMMdd")
        $timeRange = "$startDate-"
    }
    "2" {
        $startDate = $today.AddDays(-30).ToString("yyyyMMdd")
        $timeRange = "$startDate-"
    }
    "3" {
        $startDate = $today.AddDays(-90).ToString("yyyyMMdd")
        $timeRange = "$startDate-"
    }
    "4" {
        $startDate = Read-Host "Start date (YYYYMMDD)"
        $endDate = Read-Host "End date (YYYYMMDD, leave empty for today)"
        if ([string]::IsNullOrEmpty($endDate)) {
            $timeRange = "$startDate-"
        } else {
            $timeRange = "$startDate-$endDate"
        }
    }
    default {
        Write-Host "[X] Invalid choice" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Downloading historical data..." -ForegroundColor Yellow

# Download data first
$downloadArgs = @(
    "download-data",
    "--config", "config/config.json",
    "--datadir", "user_data/data",
    "--timerange", $timeRange,
    "--timeframe", "5m", "15m", "1h"
)

& freqtrade @downloadArgs

Write-Host ""
Write-Host "Running backtest for $Strategy..." -ForegroundColor Yellow
Write-Host ""

# Create backtest results directory if it doesn't exist
if (-not (Test-Path "user_data/backtest_results")) {
    New-Item -ItemType Directory -Path "user_data/backtest_results" -Force | Out-Null
}

# Generate filename with timestamp
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$exportFile = "user_data/backtest_results/${Strategy}_$timestamp.json"

# Run backtest
$backtestArgs = @(
    "backtesting",
    "--config", "config/config.json",
    "--strategy", $Strategy,
    "--strategy-path", "strategies",
    "--datadir", "user_data/data",
    "--timerange", $timeRange,
    "--export", "trades",
    "--export-filename", $exportFile
)

& freqtrade @backtestArgs

Write-Host ""
Write-Host "[OK] Backtest completed!" -ForegroundColor Green
Write-Host ""
Write-Host "Results saved in: $exportFile" -ForegroundColor Cyan
Write-Host ""
Write-Host "To view detailed results, run:" -ForegroundColor White
Write-Host "  freqtrade plot-dataframe --strategy $Strategy --config config/config.json" -ForegroundColor Green
