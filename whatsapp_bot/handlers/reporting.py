"""
Reporting handlers — on-demand portfolio and P&L queries.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# On-demand reporting is handled through Claude's tool use in message_router.
# This module exists for any reporting-specific helpers needed later.
