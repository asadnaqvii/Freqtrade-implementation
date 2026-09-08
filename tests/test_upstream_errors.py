"""What the dashboard shows when the call to the bot fails.

A deploy restarts the bot, and for about thirty seconds the platform's proxy
answers requests to it with a 502 and a full HTML error page. That body was
being pasted into the error message and rendered in the Live bot panel, so the
screen filled with minified CSS and base64 font data under the heading "BOT
UNREACHABLE". Seen on 2026-09-07. Nothing was actually wrong -- the bot was
mid-restart and serving normally a minute later -- but it read like the system
had come apart.

An error message is read by a person deciding whether to act. It has to say
what happened; it must never be a web page.
"""

from __future__ import annotations

import pytest

from app.bot_api.client import _upstream_message


class Response:
    def __init__(self, status_code, text="", content_type="application/json"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}


RENDER_502 = (
    '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
    '<title>502</title><style>@font-face{font-family:"Roobert";'
    'src:url("data:font/woff2;base64,AAEAAAASAQAABAAgR0RFRgAB' + "A" * 4000
)


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_proxy_error_is_explained_not_quoted(status):
    message = _upstream_message(Response(status, RENDER_502, "text/html"))
    assert "<" not in message and "base64" not in message
    assert str(status) in message
    assert "restarting" in message, "say why, so nobody goes looking for a fault"
    assert len(message) < 300


def test_no_html_ever_reaches_the_message():
    """Any content type that is not JSON is the infrastructure talking."""
    message = _upstream_message(Response(500, RENDER_502, "text/html"))
    assert "<!DOCTYPE" not in message
    assert "@font-face" not in message
    assert "500" in message


def test_the_bots_own_json_error_is_still_quoted():
    """The bot's own message is the useful part when the bot is the one
    complaining -- this must not become an information-free wrapper."""
    message = _upstream_message(
        Response(400, '{"error": "Pair XRP/USDT is not in whitelist"}'))
    assert "not in whitelist" in message
    assert "400" in message


def test_a_long_json_error_is_truncated_and_flattened():
    """Long enough to break the layout, and newlines that would break it
    differently, both get handled."""
    body = '{"error": "' + ("x" * 500) + '\\n\\nmore"}'
    message = _upstream_message(Response(400, body))
    assert len(message) < 300
    assert "\n" not in message


def test_a_missing_content_type_is_not_assumed_to_be_json():
    message = _upstream_message(Response(500, RENDER_502, ""))
    assert "<!DOCTYPE" not in message


def test_the_message_never_exceeds_what_a_panel_can_show():
    for response in (Response(502, RENDER_502, "text/html"),
                     Response(500, RENDER_502, "text/html"),
                     Response(400, '{"error":"' + "y" * 9000 + '"}')):
        assert len(_upstream_message(response)) < 300
