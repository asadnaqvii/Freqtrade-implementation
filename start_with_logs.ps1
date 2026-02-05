# Start bot with timestamped log file
$timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$logFile = "user_data/logs/run_$timestamp.log"

# Read strategy from config
$config = Get-Content "config/config.json" | ConvertFrom-Json
$strategy = $config.strategy
$dryRun = $config.dry_run
$mode = if ($dryRun) { "DRY-RUN (virtual money)" } else { "LIVE TRADING" }

Write-Host "================================================"
Write-Host "   Starting Crypto Trading Bot"
Write-Host "================================================"
Write-Host ""
Write-Host "Log file: $logFile"
Write-Host "Strategy: $strategy"
Write-Host "Mode: $mode"
Write-Host ""
Write-Host "Press Ctrl+C to stop"
Write-Host ""

# Create logs directory if it doesn't exist
New-Item -ItemType Directory -Force -Path "user_data/logs" | Out-Null

# Activate venv and run
& .\venv\Scripts\Activate.ps1
freqtrade trade --config config/config.json --logfile $logFile
