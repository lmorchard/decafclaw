"""DecafClaw — a minimal AI agent for learning."""

import asyncio
import logging
import time

from .config import load_config
from .context import Context
from .events import EventBus

log = logging.getLogger(__name__)

# Max restart attempts before giving up, and backoff between restarts
MAX_RESTARTS = 10
RESTART_BACKOFF_SEC = 5


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

    # If Mattermost is configured, run as a bot. Otherwise, interactive mode.
    if config.mattermost_url and config.mattermost_token:
        _run_with_restart(config)
    else:
        bus = EventBus()
        app_ctx = Context(config=config, event_bus=bus)
        from .agent import run_interactive
        asyncio.run(run_interactive(app_ctx))


def _run_with_restart(config):
    """Run the Mattermost bot with automatic restart on crashes."""
    restart_count = 0
    last_restart = 0

    while restart_count < MAX_RESTARTS:
        bus = EventBus()
        app_ctx = Context(config=config, event_bus=bus)

        try:
            from .mattermost import MattermostClient
            client = MattermostClient(config)
            asyncio.run(client.run(app_ctx))
            # Clean exit (e.g., SIGTERM) — don't restart
            log.info("Bot exited cleanly")
            return

        except KeyboardInterrupt:
            log.info("Interrupted by user")
            return

        except BaseException as e:
            now = time.monotonic()
            # Reset counter if it's been a while since last crash
            if now - last_restart > 300:  # 5 minutes
                restart_count = 0

            restart_count += 1
            last_restart = now

            log.error(f"Bot crashed ({restart_count}/{MAX_RESTARTS}): {e}",
                      exc_info=True)

            if restart_count >= MAX_RESTARTS:
                log.critical(f"Too many restarts ({MAX_RESTARTS}), giving up")
                return

            log.info(f"Restarting in {RESTART_BACKOFF_SEC}s...")
            time.sleep(RESTART_BACKOFF_SEC)
