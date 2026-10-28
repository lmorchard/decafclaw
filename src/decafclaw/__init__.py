"""DecafClaw — a minimal AI agent for learning."""

import asyncio
import logging
import os
import sys
from typing import Any, Callable

from .config import load_config
from .context import Context
from .events import EventBus

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )


def start_app(extra_provider_hook: Callable[[Any], None] | None = None) -> None:
    """Shared startup for `decafclaw` and the ephemeral test server (#957).

    Loads the config, initializes telemetry, the LLM provider registry, and
    the widget catalog, assembles the system prompt, and runs the server
    (Mattermost and/or HTTP) or the interactive terminal — exactly the
    steps `main()` used to perform inline. Both entry points call this
    function; nothing is duplicated.

    ``extra_provider_hook``, when given, is called with the loaded config
    after ``init_providers(config)`` and before the server starts. The
    ephemeral test server uses it to register the scripted provider
    (``decafclaw.testing``) in-process, so a production ``config.json``
    can never select the scripted provider.
    """
    _setup_logging()

    config = load_config()

    # Initialize OpenTelemetry native tracing
    from .telemetry import init_tracer

    init_tracer(config)

    # Initialize LLM provider registry from config
    from .llm import init_providers

    init_providers(config)

    if extra_provider_hook is not None:
        extra_provider_hook(config)

    # Initialize the widget catalog registry (bundled + admin tiers).
    # Shared across server and interactive modes; agent loop consults it
    # when validating widget payloads on tool results.
    from .widgets import init_widgets

    init_widgets(config)

    # Ensure vault directories exist
    config.vault_root.mkdir(parents=True, exist_ok=True)
    config.vault_agent_pages_dir.mkdir(parents=True, exist_ok=True)
    config.vault_agent_journal_dir.mkdir(parents=True, exist_ok=True)

    # Assemble system prompt from markdown files (bundled + workspace overrides)
    from .prompts import load_system_prompt
    from .skills import build_skill_tool_owners

    config.system_prompt, config.discovered_skills = load_system_prompt(config)
    config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)

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


def main():
    _setup_logging()

    # CLI subcommand: python -m decafclaw config ...
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        sys.argv = sys.argv[1:]  # shift so argparse sees "config show"
        from .config_cli import main as config_main

        config_main()
        return

    start_app()
