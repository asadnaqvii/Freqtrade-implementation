"""
Macro event handlers — process user responses to macro alerts.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Macro alert responses (agree/disagree/discuss) are handled through
# Claude's conversational flow in message_router. The AI engine has
# full context of the macro alert and can process the response naturally.
