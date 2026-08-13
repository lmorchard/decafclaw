"""Interactive terminal mode for DecafClaw.

Transport adapter that uses the ConversationManager for agent loop
lifecycle. Handles stdin/stdout display and confirmation prompts.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


# -- Interactive mode ----------------------------------------------------------


async def run_interactive(ctx: "Context"):
    """Run the agent in interactive terminal mode using Textual."""
    from .conversation_manager import ConversationManager
    from .mcp_client import init_mcp, shutdown_mcp
    from .widget_input import register_widget_handler
    from decafclaw.tui.app import DecafClawApp

    config = ctx.config
    conv_id = "interactive"

    ctx.user_id = ctx.user_id or config.agent_user_id
    ctx.channel_id = ctx.channel_id or "interactive"
    ctx.channel_name = ctx.channel_name or "interactive"
    ctx.conv_id = conv_id

    await init_mcp(config)

    manager = ConversationManager(config, ctx.event_bus)
    register_widget_handler(manager.confirmation_registry)

    app = DecafClawApp(manager=manager, config=config, event_bus=ctx.event_bus)
    
    try:
        await app.run_async()
    finally:
        await shutdown_mcp()
