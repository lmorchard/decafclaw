"""DecafClaw — a minimal AI agent for learning."""

import asyncio
import logging
import sys

from .config import load_config
from .context import Context
from .events import EventBus

log = logging.getLogger(__name__)


def main():
    import os
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # CLI subcommand: python -m decafclaw config ...
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        sys.argv = sys.argv[1:]  # shift so argparse sees "config show"
        from .config_cli import main as config_main
        config_main()
        return

    config = load_config()

    # Initialize LLM provider registry from config
    from .llm import init_providers
    init_providers(config)

    # Ensure vault directories exist
    config.vault_root.mkdir(parents=True, exist_ok=True)
    config.vault_agent_pages_dir.mkdir(parents=True, exist_ok=True)
    config.vault_agent_journal_dir.mkdir(parents=True, exist_ok=True)

    # Assemble system prompt from markdown files (bundled + workspace overrides)
    from .prompts import load_system_prompt
    config.system_prompt, config.discovered_skills = load_system_prompt(config)

    bus = EventBus()
    app_ctx = Context(config=config, event_bus=bus)

    # Server mode (Mattermost and/or HTTP) vs interactive terminal mode
    if config.mattermost.url or config.http.enabled:
        try:
            from .runner import run_all
            asyncio.run(run_all(app_ctx))
        except KeyboardInterrupt:
            log.info("Interrupted by user")
        except BaseException as e:
            log.error(f"Bot crashed: {e}", exc_info=True)
            sys.exit(1)  # non-zero exit → systemd Restart=on-failure kicks in
    else:
        from .interactive_terminal import run_interactive
        asyncio.run(run_interactive(app_ctx))
