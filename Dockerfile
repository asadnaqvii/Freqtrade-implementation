FROM freqtradeorg/freqtrade:stable

USER root

# Install envsubst
RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*

# Create config template inline
RUN echo '{"max_open_trades":2,"stake_currency":"USDT","stake_amount":1.0,"tradable_balance_ratio":0.99,"fiat_display_currency":"USD","dry_run":false,"dry_run_wallet":1000,"cancel_open_orders_on_exit":false,"unfilledtimeout":{"entry":10,"exit":10,"exit_timeout_count":0,"unit":"minutes"},"entry_pricing":{"price_side":"same","use_order_book":true,"order_book_top":1,"price_last_balance":0.0,"check_depth_of_market":{"enabled":false,"bids_to_ask_delta":1}},"exit_pricing":{"price_side":"same","use_order_book":true,"order_book_top":1},"exchange":{"name":"kucoin","key":"${FREQTRADE__EXCHANGE__KEY}","secret":"${FREQTRADE__EXCHANGE__SECRET}","password":"${FREQTRADE__EXCHANGE__PASSWORD}","ccxt_config":{},"ccxt_async_config":{"aiohttp_trust_env":true},"pair_whitelist":["BTC/USDT","ETH/USDT","SOL/USDT","ADA/USDT"],"pair_blacklist":[]},"pairlists":[{"method":"StaticPairList"}],"edge":{"enabled":false},"api_server":{"enabled":true,"listen_ip_address":"0.0.0.0","listen_port":8080,"verbosity":"error","enable_openapi":true,"jwt_secret_key":"${JWT_SECRET_KEY}","CORS_origins":["*"],"username":"${API_USERNAME}","password":"${API_PASSWORD}"},"bot_name":"freqtrade","initial_state":"running","force_entry_enable":true,"internals":{"process_throttle_secs":5},"strategy":"ActiveTrader","stoploss":-0.05}' > /freqtrade/config/config.template.json

# Copy strategies
COPY strategies/ /freqtrade/user_data/strategies/

# Fix permissions
RUN chown -R ftuser:ftuser /freqtrade/config /freqtrade/user_data

USER ftuser

WORKDIR /freqtrade

EXPOSE 8080

CMD sh -c "envsubst < /freqtrade/config/config.template.json > /freqtrade/config/config.json && freqtrade trade --config /freqtrade/config/config.json --strategy ActiveTrader"
