"""
FastAPI entry point — WhatsApp webhook + health check + FreqUI proxy + scheduler.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse

from whatsapp_bot.config import settings
from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.core.message_router import MessageRouter
from whatsapp_bot.jobs.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

router = MessageRouter()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Evzino WhatsApp Bot starting on port %s", settings.port)
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Evzino WhatsApp Bot stopped.")


app = FastAPI(title="Evzino WhatsApp Trading Bot", lifespan=lifespan)


# ── Health Check ───────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "neuronaq-whatsapp-bot"}


# ── WhatsApp Webhook Verification ──────────────────────────

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta sends a GET request to verify the webhook URL."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully.")
        return Response(content=challenge, media_type="text/plain")

    logger.warning("Webhook verification failed: mode=%s token=%s", mode, token)
    raise HTTPException(status_code=403, detail="Verification failed")


# ── WhatsApp Webhook — Incoming Messages ───────────────────

@app.post("/webhook")
async def receive_webhook(request: Request):
    """Handle incoming WhatsApp messages."""
    body = await request.json()

    # Extract message
    message = WhatsAppClient.extract_message(body)
    if not message:
        # Status update or non-message event — acknowledge
        return {"status": "ok"}

    phone = message["phone"]
    text = message["text"]
    msg_type = message["type"]
    button_id = message.get("button_id")

    logger.info("Message from %s: %s (type=%s, button=%s)", phone, text[:100], msg_type, button_id)

    # Route the message (runs async, errors handled inside)
    await router.route(phone, text, msg_type, button_id)

    return {"status": "ok"}


# ── Freqtrade Dashboard ───────────────────────────────────

@app.get("/dashboard")
async def dashboard():
    """Redirect user to their Freqtrade dashboard on Railway."""
    ft_url = settings.freqtrade_api_url.rstrip("/")
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="refresh" content="0; url={ft_url}">
    <title>Evzino — Freqtrade Dashboard</title>
</head>
<body>
    <p>Redirecting to your Freqtrade dashboard at <a href="{ft_url}">{ft_url}</a>...</p>
</body>
</html>"""
    )


# ── Run ────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
