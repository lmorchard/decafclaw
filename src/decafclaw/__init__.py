"""DecafClaw — a minimal AI agent for learning."""

import asyncio
import logging

from .config import load_config
from .context import Context
from .events import EventBus


def main():
    import os
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()

    # Assemble system prompt from markdown files (bundled + workspace overrides)
    from .prompts import load_system_prompt
    config.system_prompt, config.discovered_skills = load_system_prompt(config)

    bus = EventBus()
    app_ctx = Context(config=config, event_bus=bus)

    # If Mattermost is configured, run as a bot. Otherwise, interactive mode.
    if config.mattermost_url and config.mattermost_token:
        from .mattermost import MattermostClient
        client = MattermostClient(config)
        asyncio.run(client.run(app_ctx))
    else:
        from .agent import run_interactive
        asyncio.run(run_interactive(app_ctx))
