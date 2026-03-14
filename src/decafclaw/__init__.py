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

    # Load system prompt from workspace file if it exists
    from pathlib import Path
    prompt_path = config.workspace_path / "SYSTEM_PROMPT.md"
    if prompt_path.exists():
        config.system_prompt = prompt_path.read_text().strip()
        logging.info(f"Loaded system prompt from {prompt_path}")

    bus = EventBus()
    app_ctx = Context(config=config, event_bus=bus)

    # Initialize Tabstack if configured
    if config.tabstack_api_key:
        from .tools.tabstack_tools import init_tabstack
        init_tabstack(config.tabstack_api_key, config.tabstack_api_url or None)

    # If Mattermost is configured, run as a bot. Otherwise, interactive mode.
    if config.mattermost_url and config.mattermost_token:
        from .mattermost import MattermostClient
        client = MattermostClient(config)
        asyncio.run(client.run(app_ctx))
    else:
        from .agent import run_interactive
        asyncio.run(run_interactive(app_ctx))
