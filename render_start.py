#!/usr/bin/env python3
"""
Render startup script — WhatsApp Bot only.
Freqtrade runs separately on Railway.
"""

import os
import sys

port = int(os.environ.get("PORT", 10000))

print("=== Neuronaq WhatsApp Bot ===")
print(f"Python: {sys.version}")
print(f"Port: {port}")
print(f"Freqtrade URL: {os.environ.get('FREQTRADE_API_URL', 'NOT SET')}")

# Start the bot
os.execvp("uvicorn", [
    "uvicorn", "whatsapp_bot.app:app",
    "--host", "0.0.0.0",
    "--port", str(port),
])
