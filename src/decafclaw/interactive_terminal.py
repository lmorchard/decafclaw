"""Interactive terminal mode for DecafClaw."""

from __future__ import annotations

import asyncio
import logging

from .tools import TOOL_DEFINITIONS

log = logging.getLogger(__name__)


def _setup_interactive_context(ctx) -> None:
    """Populate context defaults and media handler for interactive mode."""
    from .media import LocalFileMediaHandler
    config = ctx.config

    ctx.user_id = ctx.user_id or config.agent_user_id
    ctx.channel_id = ctx.channel_id or "interactive"
    ctx.channel_name = ctx.channel_name or "interactive"
    ctx.thread_id = ctx.thread_id or ""
    ctx.conv_id = "interactive"
    ctx.media_handler = LocalFileMediaHandler(config)

    if config.llm.streaming:
        async def _terminal_stream_chunk(chunk_type, data):
            if chunk_type == "text":
                print(data, end="", flush=True)
            elif chunk_type == "tool_call_start":
                print(f"\n  [calling {data['name']}...]", flush=True)

        ctx.on_stream_chunk = _terminal_stream_chunk


def _print_banner(config) -> None:
    """Print startup banner showing model, tools, skills, and MCP info."""
    from .mcp_client import get_registry

    print("DecafClaw interactive mode. Type 'quit' to exit.")
    print(f"Model: {config.llm.model}")
    print(f"Tools: {', '.join(t['function']['name'] for t in TOOL_DEFINITIONS)}")
    skills = getattr(config, "discovered_skills", [])
    if skills:
        print(f"Skills: {', '.join(s.name for s in skills)} (activate to use)")
    mcp_registry = get_registry()
    if mcp_registry and mcp_registry.servers:
        parts = []
        for name, state in mcp_registry.servers.items():
            parts.append(f"{name} ({len(state.tools)} tools, {state.status})")
        print(f"MCP: {', '.join(parts)}")
    print()


def _create_interactive_progress_subscriber(ctx):
    """Create the on_progress callback for interactive mode."""
    async def on_progress(event):
        event_type = event.get("type")
        if event_type == "tool_status":
            print(f"  [{event.get('tool', 'tool')}] {event['message']}")
        elif event_type == "tool_start":
            print(f"  [running {event.get('tool', 'tool')}...]")
        elif event_type == "llm_start" and event.get("iteration", 1) > 1:
            print("  [thinking...]")
        elif event_type == "compaction_start":
            print("  [compacting conversation...]")
        elif event_type == "compaction_end":
            print("  [compaction complete]")
        elif event_type == "tool_confirm_request":
            command = event.get("command", "")
            tool_name = event.get("tool", "tool")
            suggested_pattern = event.get("suggested_pattern", "")
            print(f"\n  \U0001f6a8 Confirm {tool_name}: {command}")
            if suggested_pattern and tool_name == "shell":
                prompt = f"  Approve? [y]es / [n]o / [a]lways / [p]attern ({suggested_pattern}): "
            else:
                prompt = "  Approve? [y]es / [n]o / [a]lways: "
            answer = await asyncio.to_thread(input, prompt)
            choice = answer.strip().lower()
            approved = choice in ("y", "yes", "a", "always", "p", "pattern")
            always = choice in ("a", "always")
            add_pattern = choice in ("p", "pattern")
            await ctx.event_bus.publish({
                "type": "tool_confirm_response",
                "context_id": event.get("context_id"),
                "tool": tool_name,
                "approved": approved,
                "always": always,
                **({"add_pattern": True} if add_pattern else {}),
            })

    return on_progress


# -- Interactive mode ----------------------------------------------------------


async def run_interactive(ctx):
    """Run the agent in interactive terminal mode (stdin/stdout)."""
    from .agent import run_agent_turn
    from .archive import read_archive
    from .heartbeat import run_heartbeat_timer
    from .mcp_client import init_mcp, shutdown_mcp

    config = ctx.config

    _setup_interactive_context(ctx)
    await init_mcp(config)
    _print_banner(config)

    sub_id = ctx.event_bus.subscribe(_create_interactive_progress_subscriber(ctx))

    # Resume from archive if available
    history = read_archive(config, ctx.conv_id)
    if history:
        log.info(f"Resumed interactive session from archive ({len(history)} messages)")
        print(f"  (resumed {len(history)} messages from previous session)")
    else:
        history = []

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
            config, ctx.event_bus, shutdown_event,
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

            result = await run_agent_turn(ctx, user_input, history)

            if config.llm.streaming:
                print()  # final newline after streamed text
            else:
                print(f"\nagent> {result.text}\n")
    finally:
        shutdown_event.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        ctx.event_bus.unsubscribe(sub_id)
        await shutdown_mcp()
