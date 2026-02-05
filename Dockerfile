FROM freqtradeorg/freqtrade:stable

USER root

# Install envsubst
RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*

USER ftuser

# Copy configuration and strategies
COPY --chown=ftuser:ftuser config/config.render.json /freqtrade/config/config.template.json
COPY --chown=ftuser:ftuser strategies/ /freqtrade/user_data/strategies/

# Set working directory
WORKDIR /freqtrade

# Expose API port
EXPOSE 8080

# Start script
COPY --chown=ftuser:ftuser start.sh /freqtrade/start.sh
RUN chmod +x /freqtrade/start.sh

CMD ["/freqtrade/start.sh"]
