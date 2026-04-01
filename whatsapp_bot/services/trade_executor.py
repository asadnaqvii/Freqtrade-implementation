"""
DEPRECATED — Trade execution is handled by each user's Freqtrade instance.

The WhatsApp bot communicates with Freqtrade via FreqtradeClient (per-user).
Each user runs their own Freqtrade with their own KuCoin API keys configured
in Freqtrade's config.json. The bot only needs the Freqtrade REST API URL
and credentials to talk to it.

See: whatsapp_bot/services/freqtrade_client.py
"""
