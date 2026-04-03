"""
Central configuration — all environment variables and settings.
"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    # --- Meta WhatsApp Cloud API ---
    whatsapp_token: str = os.environ.get("WHATSAPP_TOKEN", "")
    whatsapp_phone_number_id: str = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    whatsapp_verify_token: str = os.environ.get("WHATSAPP_VERIFY_TOKEN", "neuronaq-verify")
    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"

    # --- OpenAI (GPT-4) ---
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o")

    # --- Supabase ---
    supabase_url: str = os.environ.get("SUPABASE_URL", "")
    supabase_key: str = os.environ.get("SUPABASE_KEY", "")

    # --- Freqtrade REST API ---
    freqtrade_api_url: str = os.environ.get("FREQTRADE_API_URL", "http://localhost:8080")
    freqtrade_api_username: str = os.environ.get("FREQTRADE_API_USERNAME", "freqtrader")
    freqtrade_api_password: str = os.environ.get("FREQTRADE_API_PASSWORD", "freqtrader")

    # --- Macro Intelligence APIs ---
    cryptopanic_api_key: str = os.environ.get("CRYPTOPANIC_API_KEY", "")
    newsapi_key: str = os.environ.get("NEWSAPI_KEY", "")

    # --- Encryption ---
    encryption_key: str = os.environ.get("ENCRYPTION_KEY", "")  # 32-byte hex string for AES-256

    # --- Server ---
    port: int = int(os.environ.get("PORT", "8001"))
    debug: bool = os.environ.get("DEBUG", "false").lower() == "true"

    # --- Scheduler ---
    macro_scan_interval_hours: int = 6
    fear_greed_alert_low: int = 25
    fear_greed_alert_high: int = 80

    # --- Available strategies (maps to strategies/*.py) ---
    available_strategies: tuple = (
        "ConservativeRSI",
        "EMACrossover",
        "BollingerBreakout",
        "ActiveTrader",
        "MeanReversionScalper",
        "EMACrossoverScalper",
    )

    # --- Default trading pairs ---
    default_pairs: tuple = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT")


settings = Settings()
