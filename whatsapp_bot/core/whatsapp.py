"""
Meta WhatsApp Cloud API client — send/receive messages and media.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from whatsapp_bot.config import settings
from whatsapp_bot.utils.retry import async_retry

logger = logging.getLogger(__name__)


class WhatsAppClient:
    """Async client for the Meta WhatsApp Cloud API."""

    def __init__(self):
        self.base_url = f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_number_id}"
        self.headers = {
            "Authorization": f"Bearer {settings.whatsapp_token}",
            "Content-Type": "application/json",
        }

    # ── Send Messages ──────────────────────────────────────

    @async_retry(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    async def send_text(self, to: str, body: str) -> dict:
        """Send a plain text message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    @async_retry(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    async def send_image(self, to: str, image_url: str, caption: str = "") -> dict:
        """Send an image by URL."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": image_url, "caption": caption},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    @async_retry(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    async def send_image_bytes(self, to: str, image_bytes: bytes, caption: str = "") -> dict:
        """Upload image bytes and send via media ID."""
        # Step 1: Upload media
        async with httpx.AsyncClient(timeout=30) as client:
            upload_resp = await client.post(
                f"{settings.whatsapp_api_url}/{settings.whatsapp_phone_number_id}/media",
                headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
                files={"file": ("chart.png", image_bytes, "image/png")},
                data={"messaging_product": "whatsapp"},
            )
            upload_resp.raise_for_status()
            media_id = upload_resp.json().get("id")

        # Step 2: Send using media ID
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"id": media_id, "caption": caption},
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    @async_retry(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    async def send_interactive_buttons(
        self, to: str, body: str, buttons: list[dict[str, str]]
    ) -> dict:
        """
        Send an interactive button message.
        buttons: [{"id": "btn_1", "title": "Yes"}, {"id": "btn_2", "title": "No"}]
        Max 3 buttons.
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                        for b in buttons[:3]
                    ]
                },
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    @async_retry(max_attempts=3, base_delay=1.0, exceptions=(httpx.HTTPError,))
    async def send_interactive_list(
        self, to: str, body: str, button_text: str, sections: list[dict]
    ) -> dict:
        """
        Send an interactive list message.
        sections: [{"title": "Section", "rows": [{"id": "1", "title": "Option", "description": "..."}]}]
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body},
                "action": {
                    "button": button_text,
                    "sections": sections,
                },
            },
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/messages",
                headers=self.headers,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    # ── Parse Incoming ─────────────────────────────────────

    @staticmethod
    def extract_message(webhook_body: dict) -> Optional[dict]:
        """
        Extract the first message from a webhook payload.
        Returns dict with: phone, text, type, button_id (if interactive reply).
        """
        try:
            entry = webhook_body["entry"][0]
            changes = entry["changes"][0]
            value = changes["value"]

            if "messages" not in value:
                return None  # status update, not a message

            msg = value["messages"][0]
            phone = msg["from"]
            msg_type = msg.get("type", "text")

            result = {"phone": phone, "type": msg_type, "text": "", "button_id": None}

            if msg_type == "text":
                result["text"] = msg["text"]["body"]
            elif msg_type == "interactive":
                interactive = msg["interactive"]
                if interactive["type"] == "button_reply":
                    result["button_id"] = interactive["button_reply"]["id"]
                    result["text"] = interactive["button_reply"]["title"]
                elif interactive["type"] == "list_reply":
                    result["button_id"] = interactive["list_reply"]["id"]
                    result["text"] = interactive["list_reply"]["title"]
            elif msg_type == "image":
                result["text"] = msg.get("image", {}).get("caption", "[Image received]")

            return result
        except (KeyError, IndexError):
            logger.warning("Could not parse webhook body: %s", webhook_body)
            return None
