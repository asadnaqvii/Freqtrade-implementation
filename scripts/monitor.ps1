# Crypto Investment Platform Monitor for Windows
# Powered by Freqtrade

$ErrorActionPreference = "SilentlyContinue"

function Write-Header {
    Clear-Host
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "       Crypto Investment Platform Monitor" -ForegroundColor Cyan
    Write-Host "              $timestamp" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host ""
}

function Test-BotRunning {
    $process = Get-Process -Name "freqtrade" -ErrorAction SilentlyContinue
    if ($process) {
        Write-Host "[OK] Bot Status: RUNNING" -ForegroundColor Green
        return $true
    } else {
        # Also check for python running freqtrade
        $pythonProcess = Get-Process -Name "python" -ErrorAction SilentlyContinue
        if ($pythonProcess) {
            Write-Host "[OK] Bot Status: RUNNING (Python)" -ForegroundColor Green
            return $true
        }
        Write-Host "[X] Bot Status: NOT RUNNING" -ForegroundColor Red
        return $false
    }
}

function Show-CurrentTrades {
    Write-Host ""
    Write-Host "Current Open Trades:" -ForegroundColor Yellow
    Write-Host "-------------------" -ForegroundColor Yellow

    if (Test-Path "user_data/tradesv3.sqlite") {
        try {
            $arguments = @(
                "show_trades",
                "--config", "config/config.json",
                "--db-url", "sqlite:///user_data/tradesv3.sqlite"
            )
            & freqtrade @arguments 2>$null
        } catch {
            Write-Host "No trades data available yet" -ForegroundColor Gray
        }
    } else {
        Write-Host "No trades database found. Start the bot first." -ForegroundColor Gray
    }
}

function Show-Performance {
    Write-Host ""
    Write-Host "Performance Statistics:" -ForegroundColor Yellow
    Write-Host "----------------------" -ForegroundColor Yellow

    if (Test-Path "user_data/tradesv3.sqlite") {
        try {
            $arguments = @(
                "profit",
                "--config", "config/config.json",
                "--db-url", "sqlite:///user_data/tradesv3.sqlite"
            )
            & freqtrade @arguments 2>$null
        } catch {
            Write-Host "No performance data available yet" -ForegroundColor Gray
        }
    } else {
        Write-Host "No trades database found. Start the bot first." -ForegroundColor Gray
    }
}

function Show-Logs {
    Write-Host ""
    Write-Host "Recent Log Entries:" -ForegroundColor Yellow
    Write-Host "-------------------" -ForegroundColor Yellow

    if (Test-Path "user_data/logs/freqtrade.log") {
        Get-Content "user_data/logs/freqtrade.log" -Tail 10
    } else {
        Write-Host "No log file found yet" -ForegroundColor Gray
    }
}

function Main {
    # Check if we're in the right directory
    if (-not (Test-Path "config/config.json")) {
        Write-Host "[X] Error: Not in project directory or config not found" -ForegroundColor Red
        exit 1
    }

    # Activate virtual environment if exists
    if (Test-Path "venv\Scripts\Activate.ps1") {
        & ".\venv\Scripts\Activate.ps1"
    }

    while ($true) {
        Write-Header

        $botRunning = Test-BotRunning

        if ($botRunning) {
            Show-CurrentTrades
            Show-Performance
            Show-Logs
        } else {
            Write-Host ""
            Write-Host "To start the bot, run:" -ForegroundColor White
            Write-Host "  .\start_bot.ps1 -DryRun   (for practice mode)" -ForegroundColor Green
            Write-Host "  .\start_bot.ps1 -Live     (for live trading)" -ForegroundColor Yellow
        }

        Write-Host ""
        Write-Host "Web Dashboard: " -NoNewline -ForegroundColor Cyan
        Write-Host "http://localhost:8080" -ForegroundColor Green
        Write-Host ""
        Write-Host "Press 'q' to quit, or wait 30 seconds for auto-refresh..." -ForegroundColor Gray

        # Wait for input with timeout
        $timeout = 30
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

        while ($stopwatch.Elapsed.TotalSeconds -lt $timeout) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.KeyChar -eq 'q' -or $key.KeyChar -eq 'Q') {
                    Write-Host ""
                    Write-Host "Exiting monitor..." -ForegroundColor Yellow
                    return
                }
            }
            Start-Sleep -Milliseconds 100
        }
    }
}

Main
