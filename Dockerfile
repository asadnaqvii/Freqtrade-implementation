FROM freqtradeorg/freqtrade:stable

USER root

# Install envsubst
RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*

# Copy configuration and strategies as root to ensure permissions
COPY config/config.render.json /freqtrade/config/config.template.json
COPY strategies/ /freqtrade/user_data/strategies/

# Fix permissions
RUN chown -R ftuser:ftuser /freqtrade/config /freqtrade/user_data

USER ftuser

# Set working directory
WORKDIR /freqtrade

# Expose API port
EXPOSE 8080

# Start command
CMD sh -c "envsubst < /freqtrade/config/config.template.json > /freqtrade/config/config.json && freqtrade trade --config /freqtrade/config/config.json --strategy ActiveTrader"
