"""
Strategy handlers — strategy viewing, switching, parameter updates.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Strategy management is handled through Claude's tool use in message_router.
# Claude calls switch_strategy and update_strategy_params tools which
# are executed by StrategyManager.
