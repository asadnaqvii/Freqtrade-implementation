#!/bin/bash
set -e

echo "=== Build environment ==="
echo "HOME=$HOME"
echo "PWD=$PWD"

echo "=== Installing TA-Lib C library ==="
cd /tmp
curl -L -o ta-lib-0.4.0-src.tar.gz https://github.com/TA-Lib/ta-lib/releases/download/v0.4.0/ta-lib-0.4.0-src.tar.gz
tar -xzf ta-lib-0.4.0-src.tar.gz
cd ta-lib/
./configure --prefix=$HOME/ta-lib
make
make install
echo "TA-Lib installed to $HOME/ta-lib"
ls -la $HOME/ta-lib/lib/

cd /opt/render/project/src
rm -rf /tmp/ta-lib /tmp/ta-lib-0.4.0-src.tar.gz

echo "=== Installing Python dependencies ==="
export LD_LIBRARY_PATH=$HOME/ta-lib/lib:$LD_LIBRARY_PATH
export TA_LIBRARY_PATH=$HOME/ta-lib/lib
export TA_INCLUDE_PATH=$HOME/ta-lib/include

pip install --upgrade pip
pip install numpy
TA_LIBRARY_PATH=$HOME/ta-lib/lib TA_INCLUDE_PATH=$HOME/ta-lib/include pip install TA-Lib
pip install freqtrade

echo "=== Creating user_data directories ==="
mkdir -p user_data/strategies user_data/data user_data/logs user_data/backtest_results

echo "=== Build complete ==="
