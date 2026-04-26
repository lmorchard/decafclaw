"""Interactive terminal mode for DecafClaw.

Transport adapter that uses the ConversationManager for agent loop
lifecycle. Handles stdin/stdout display and confirmation prompts.
"""

from __future__ import annotations

import asyncio
import logging

from .config import resolve_streaming
from .tools import TOOL_DEFINITIONS

log = logging.getLogger(__name__)


def _print_banner(config) -> None:
    """Print startup banner showing model, tools, skills, and MCP info."""
    from .mcp_client import get_registry

    print("DecafClaw interactive mode. Type 'quit' to exit.")
    print(f"Model: {config.default_model or config.llm.model}")
    print(f"Tools: {', '.join(t['function']['name'] for t in TOOL_DEFINITIONS)}")
    skills = config.discovered_skills
    if skills:
        print(f"Skills: {', '.join(s.name for s in skills)} (activate to use)")
    mcp_registry = get_registry()
    if mcp_registry and mcp_registry.servers:
        parts = []
        for name, state in mcp_registry.servers.items():
            parts.append(f"{name} ({len(state.tools)} tools, {state.status})")
        print(f"MCP: {', '.join(parts)}")
    print()


# -- Interactive mode ----------------------------------------------------------


async def run_interactive(ctx):
    """Run the agent in interactive terminal mode (stdin/stdout)."""
    from .conversation_manager import ConversationManager
    from .heartbeat import run_heartbeat_timer
    from .mcp_client import init_mcp, shutdown_mcp
    from .media import LocalFileMediaHandler

    config = ctx.config
    conv_id = "interactive"

    # Set up context identity
    ctx.user_id = ctx.user_id or config.agent_user_id
    ctx.channel_id = ctx.channel_id or "interactive"
    ctx.channel_name = ctx.channel_name or "interactive"
    ctx.conv_id = conv_id

    await init_mcp(config)
    _print_banner(config)

    # Create conversation manager
    from .widget_input import register_widget_handler
    manager = ConversationManager(config, ctx.event_bus)
    register_widget_handler(manager.confirmation_registry)

    # Track turn completion
    turn_done = asyncio.Event()
    last_response_text = {"text": ""}

    # Subscribe to conversation events for terminal display
    async def on_event(event):
        event_type = event.get("type", "")

        if event_type == "chunk":
            print(event.get("text", ""), end="", flush=True)

        elif event_type == "tool_call_start":
            name = event.get("name", "tool")
            print(f"\n  [calling {name}...]", flush=True)

        elif event_type == "tool_start":
            print(f"  [running {event.get('tool', 'tool')}...]")

        elif event_type == "tool_status":
            print(f"  [{event.get('tool', 'tool')}] {event.get('message', '')}")

        elif event_type == "llm_start" and event.get("iteration", 1) > 1:
            print("  [thinking...]")

        elif event_type == "compaction_start":
            print("  [compacting conversation...]")

        elif event_type == "compaction_end":
            print("  [compaction complete]")

        elif event_type == "confirmation_request":
            confirmation_id = event.get("confirmation_id", "")
            message = event.get("message", "")
            action_type = event.get("action_type", "")
            action_data = event.get("action_data", {})

            command = action_data.get("command", message)
            suggested_pattern = action_data.get("suggested_pattern", "")

            print(f"\n  \U0001f6a8 Confirm ({action_type}): {command}")
            if suggested_pattern and action_type == "run_shell_command":
                prompt = (f"  Approve? [y]es / [n]o / [a]lways / "
                          f"[p]attern ({suggested_pattern}): ")
            else:
                prompt = "  Approve? [y]es / [n]o / [a]lways: "

            answer = await asyncio.to_thread(input, prompt)
            choice = answer.strip().lower()
            approved = choice in ("y", "yes", "a", "always", "p", "pattern")
            always = choice in ("a", "always")
            add_pattern = choice in ("p", "pattern")

            await manager.respond_to_confirmation(
                conv_id, confirmation_id,
                approved=approved, always=always, add_pattern=add_pattern,
            )

        elif event_type == "message_complete":
            if event.get("suppress_user_message"):
                return  # WAKE turn ended with BACKGROUND_WAKE_OK — silent end
            if event.get("final"):
                last_response_text["text"] = event.get("text", "")

        elif event_type == "turn_complete":
            turn_done.set()

        elif event_type == "error":
            print(f"\n[error: {event.get('message', '')}]")
            turn_done.set()

    manager.subscribe(conv_id, on_event)

    # Transport-specific context setup
    def terminal_context_setup(ctx_arg):
        ctx_arg.media_handler = LocalFileMediaHandler(config)
        ctx_arg.channel_name = "interactive"

    # Start heartbeat timer
    shutdown_event = asyncio.Event()
    suppress_ok = config.heartbeat_suppress_ok

    async def interactive_heartbeat_reporter(results):
        from datetime import datetime
        has_alerts = any(not r["is_ok"] for r in results)
        if not has_alerts and suppress_ok:
            return
        print(f"\n--- Heartbeat \u2014 {datetime.now().strftime('%Y-%m-%d %H:%M')} ---")
        for result in results:
            if result["is_ok"] and suppress_ok:
                continue
            print(f"[{result['title']}] {result['response']}")
        print()

    heartbeat_task = asyncio.create_task(
        run_heartbeat_timer(
            config, ctx.event_bus, manager, shutdown_event,
            on_results=interactive_heartbeat_reporter,
        )
    )

    try:
        while True:
            try:
                user_input = (await asyncio.to_thread(input, "you> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye!")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break

            turn_done.clear()
            last_response_text["text"] = ""

            await manager.send_message(
                conv_id, user_input,
                user_id=ctx.user_id,
                context_setup=terminal_context_setup,
            )

            # Wait for the turn to complete
            await turn_done.wait()

            if resolve_streaming(config):
                print()  # final newline after streamed text
            else:
                text = last_response_text["text"]
                if text:
                    print(f"\nagent> {text}\n")
    finally:
        shutdown_event.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        await shutdown_mcp()
