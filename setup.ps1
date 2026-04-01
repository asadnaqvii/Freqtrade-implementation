# Crypto Investment Platform Setup for Windows
# Powered by Freqtrade

$ErrorActionPreference = "Stop"

function Write-Header($text) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host $text.PadLeft(30 + ($text.Length / 2)).PadRight(60) -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host ""
}

function Write-Success($text) {
    Write-Host "[OK] $text" -ForegroundColor Green
}

function Write-Warning($text) {
    Write-Host "[!] $text" -ForegroundColor Yellow
}

function Write-Error($text) {
    Write-Host "[X] $text" -ForegroundColor Red
}

Write-Header "Crypto Investment Platform Setup"

# Check if Python is installed
Write-Host "Checking prerequisites..." -ForegroundColor Yellow

$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $version = & $cmd --version 2>&1
        if ($version -match "Python (\d+\.\d+)") {
            $pythonCmd = $cmd
            Write-Success "Python found: $version"
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonCmd) {
    Write-Error "Python is not installed. Please install Python 3.8 or higher."
    Write-Host "Download from: https://www.python.org/downloads/" -ForegroundColor Cyan
    exit 1
}

# Create directory structure
Write-Host ""
Write-Host "Creating directory structure..." -ForegroundColor Yellow

$directories = @(
    "config/templates",
    "config/strategies",
    "strategies/beginner",
    "strategies/intermediate",
    "strategies/custom",
    "scripts",
    "user_data/data",
    "user_data/logs",
    "user_data/backtest_results",
    "docs"
)

foreach ($dir in $directories) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Success "Directories created"

# Create virtual environment
Write-Host ""
Write-Host "Creating Python virtual environment..." -ForegroundColor Yellow

if (-not (Test-Path "venv")) {
    & $pythonCmd -m venv venv
    Write-Success "Virtual environment created"
} else {
    Write-Success "Virtual environment already exists"
}

# Activate virtual environment
Write-Host ""
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& ".\venv\Scripts\Activate.ps1"
Write-Success "Virtual environment activated"

# Upgrade pip
Write-Host ""
Write-Host "Upgrading pip..." -ForegroundColor Yellow
& python -m pip install --upgrade pip

# Install Freqtrade
Write-Host ""
Write-Host "Installing Freqtrade and dependencies..." -ForegroundColor Yellow
Write-Host "This may take several minutes..." -ForegroundColor Yellow

try {
    & pip install freqtrade
    Write-Success "Freqtrade installed successfully"
} catch {
    Write-Error "Failed to install Freqtrade. Error: $_"
    Write-Host "You may need to install Visual C++ Build Tools" -ForegroundColor Yellow
    Write-Host "Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/" -ForegroundColor Cyan
}

# Install additional packages
Write-Host ""
Write-Host "Installing additional packages..." -ForegroundColor Yellow

$packages = @(
    "pandas>=1.5.0",
    "numpy>=1.23.0",
    "requests>=2.28.0",
    "python-telegram-bot>=20.0",
    "colorama>=0.4.6",
    "tabulate>=0.9.0",
    "plotly>=5.11.0",
    "matplotlib>=3.6.0"
)

foreach ($package in $packages) {
    try {
        & pip install $package
    } catch {
        Write-Warning "Could not install $package"
    }
}

# Try to install TA-Lib (often problematic on Windows)
Write-Host ""
Write-Host "Attempting to install TA-Lib..." -ForegroundColor Yellow
try {
    & pip install ta-lib-bin
    Write-Success "TA-Lib installed via ta-lib-bin"
} catch {
    try {
        & pip install TA-Lib
        Write-Success "TA-Lib installed"
    } catch {
        Write-Warning "TA-Lib installation failed. Some strategies may not work."
        Write-Host "You can manually install TA-Lib from: https://github.com/cgohlke/talib-build/releases" -ForegroundColor Cyan
    }
}

Write-Success "Additional packages installed"

# Summary
Write-Host ""
Write-Header "Installation Complete!"

Write-Host "Next steps:" -ForegroundColor White
Write-Host ""
Write-Host "1. Activate the virtual environment:" -ForegroundColor White
Write-Host "   .\venv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host ""
Write-Host "2. Run the configuration wizard:" -ForegroundColor White
Write-Host "   python config_wizard.py" -ForegroundColor Green
Write-Host ""
Write-Host "3. Start the bot in dry-run mode (recommended):" -ForegroundColor White
Write-Host "   .\start_bot.ps1 -DryRun" -ForegroundColor Green
Write-Host ""
Write-Host "For help, see README.md or docs/GETTING_STARTED.md" -ForegroundColor Cyan
Write-Host ""
