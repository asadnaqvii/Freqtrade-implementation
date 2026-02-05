#!/bin/bash

# Replace environment variables in config
envsubst < /freqtrade/config/config.template.json > /freqtrade/config/config.json

# Start freqtrade
exec freqtrade trade --config /freqtrade/config/config.json --strategy ActiveTrader
