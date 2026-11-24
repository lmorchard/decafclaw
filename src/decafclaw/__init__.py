"""DecafClaw — a minimal AI agent for learning."""

import asyncio
import logging
import os
import sys

# Disable mem0 telemetry globally to prevent cloud egress and migration SQLite lock conflicts
os.environ.setdefault("MEM0_TELEMETRY", "false")
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
    the widget catalog, assembles the system prompt, and runs the server via
    the single process root `runner.run_all` — exactly the steps `main()` used
    to perform inline. Both entry points call this function; nothing is
    duplicated. There is no second process root: the legacy stdin/stdout
    interactive terminal was removed (#1149), so a process either runs the
    server (Mattermost and/or HTTP) or serves headless duties with no inbound
    chat transport.

    ``extra_provider_hook``, when given, is called with the loaded config
    after ``init_providers(config)`` and before the server starts. The
    ephemeral test server uses it to register the scripted provider
    (``decafclaw.testing``) in-process, so a production ``config.json``
    can never select the scripted provider.

    The server path does not return: after the graceful shutdown sequence the
    process exits through ``runner.exit_process`` (see #1799), because returning
    through interpreter shutdown can block on a stuck non-daemon thread.
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
    # Shared across server modes; the agent loop consults it when validating
    # widget payloads on tool results.
    from .widgets import init_widgets

    init_widgets(config)

    # Ensure vault directories exist
    config.vault_root.mkdir(parents=True, exist_ok=True)
    config.vault_agent_pages_dir.mkdir(parents=True, exist_ok=True)
    config.vault_agent_journal_dir.mkdir(parents=True, exist_ok=True)

    # Seed the operator-editable shell activity vocabulary from bundled examples.
    from .activity import ensure_activity_domains_file

    ensure_activity_domains_file(config)

    # Assemble system prompt from markdown files (bundled + workspace overrides)
    from .prompts import load_system_prompt
    from .skills import build_skill_tool_owners

    config.system_prompt, config.discovered_skills = load_system_prompt(config)
    config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)

    bus = EventBus()
    app_ctx = Context(config=config, event_bus=bus)

    # Single process root: the server (Mattermost and/or HTTP). There is no
    # interactive-terminal fallback (#1149): presentation is supplied by
    # clients over the gateway, and a process with no inbound chat transport
    # still runs headless duties through the same root.
    try:
        from .runner import arm_shutdown_watchdog, exit_process, run_all

        asyncio.run(run_all(app_ctx, on_shutdown_complete=arm_shutdown_watchdog))
        # The graceful sequence is complete. Returning through interpreter
        # shutdown joins every non-daemon thread, so a worker stuck in a sync
        # tool call could keep a finished daemon alive forever (#1799).
        exit_process("shutdown complete")
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        exit_process("interrupted")
    except BaseException as e:
        log.error(f"Bot crashed: {e}", exc_info=True)
        exit_process("crash", 1)  # non-zero exit → systemd Restart=on-failure kicks in


def main():
    _setup_logging()

    # CLI subcommand: python -m decafclaw config ...
    if len(sys.argv) > 1 and sys.argv[1] == "config":
        sys.argv = sys.argv[1:]  # shift so argparse sees "config show"
        from .config_cli import main as config_main

        config_main()
        return

    # CLI subcommand: python -m decafclaw toil audit ...
    if len(sys.argv) > 1 and sys.argv[1] == "toil":
        from .toil import main as toil_main

        raise SystemExit(toil_main(sys.argv[2:]))

    # CLI subcommand: python -m decafclaw survey stage0|devset|score ...
    if len(sys.argv) > 1 and sys.argv[1] == "survey":
        from .survey.__main__ import main as survey_main

        raise SystemExit(survey_main(sys.argv[2:]))

    # CLI subcommand: python -m decafclaw approvals report ...
    if len(sys.argv) > 1 and sys.argv[1] == "approvals":
        from .approval_report import main as approvals_main

        raise SystemExit(approvals_main(sys.argv[2:]))

    start_app()
