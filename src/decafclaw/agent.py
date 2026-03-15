"""The agent loop — the core of DecafClaw.

This is where the interesting stuff happens. The loop:
1. Receives a message (from stdin or Mattermost)
2. Builds a prompt with system prompt + history + tools
3. Calls the LLM
4. If the LLM wants to use tools, executes them and loops
5. Returns the final text response
"""

import asyncio
import json
import logging

from .archive import append_message
from .compaction import compact_history
from .llm import call_llm
from .tools import TOOL_DEFINITIONS, execute_tool

log = logging.getLogger(__name__)


def _conv_id(ctx):
    """Get conversation ID from context."""
    return getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", "unknown")


def _archive(ctx, msg):
    """Archive a message, logging errors but never raising."""
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
    except Exception as e:
        log.error(f"Archive write failed: {e}")

    # Index user/assistant messages for conversation search (fire and forget)
    # Requires an embedding model to be configured
    if ctx.config.embedding_model and msg.get("role") in ("user", "assistant"):
        content = msg.get("content")
        if content and len(content) > 20:  # skip trivial messages
            import asyncio
            asyncio.create_task(_index_conversation_message(ctx, msg))


async def _index_conversation_message(ctx, msg):
    """Index a conversation message for semantic search (background)."""
    try:
        from .embeddings import index_entry
        conv_id = _conv_id(ctx)
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        entry_text = f"{role}: {content}"
        await index_entry(ctx.config, conv_id, entry_text, source_type="conversation")
    except Exception as e:
        log.debug(f"Conversation indexing failed: {e}")


async def _maybe_compact(ctx, config, history, prompt_tokens):
    """Trigger compaction if token budget is exceeded."""
    if prompt_tokens and prompt_tokens > config.compaction_max_tokens:
        log.info(f"Token budget exceeded ({prompt_tokens} > {config.compaction_max_tokens}), "
                 f"triggering compaction")
        try:
            await compact_history(ctx, history)
        except Exception as e:
            log.error(f"Compaction failed: {e}")


async def run_agent_turn(ctx, user_message: str, history: list):
    """Process a single user message through the agent loop.

    Args:
        ctx: Runtime context (carries config, event bus, etc.)
        user_message: The user's message text
        history: Conversation history (list of message dicts, mutated in place)

    Returns:
        ToolResult with the agent's text response and any accumulated media.
    """
    from .media import ToolResult, extract_workspace_media

    config = ctx.config
    # Expose history on context so tools (e.g., compact_conversation) can access it
    ctx.history = history
    ctx.total_prompt_tokens = getattr(ctx, "total_prompt_tokens", 0)
    ctx.total_completion_tokens = getattr(ctx, "total_completion_tokens", 0)

    # Accumulate media from tool results across the turn
    pending_media = []

    # Add user message to history
    user_msg = {"role": "user", "content": user_message}
    history.append(user_msg)
    _archive(ctx, user_msg)

    # Build the messages array: system prompt + history
    messages = [{"role": "system", "content": config.system_prompt}] + history
    # Expose messages on context so debug tools can inspect them
    ctx.messages = messages

    prompt_tokens = 0

    for iteration in range(config.max_tool_iterations):
        log.debug(f"Agent iteration {iteration + 1}")

        # Build tool list: base + skill-activated + MCP tools
        all_tools = TOOL_DEFINITIONS + getattr(ctx, "extra_tool_definitions", [])
        from .mcp_client import get_registry
        mcp_registry = get_registry()
        if mcp_registry:
            all_tools = all_tools + mcp_registry.get_tool_definitions()

        # Call the LLM (streaming or all-at-once based on config)
        await ctx.publish("llm_start", iteration=iteration + 1)
        if config.llm_streaming:
            from .llm import call_llm_streaming
            on_chunk = getattr(ctx, "on_stream_chunk", None)
            response = await call_llm_streaming(
                config, messages, tools=all_tools, on_chunk=on_chunk
            )
        else:
            response = await call_llm(config, messages, tools=all_tools)
        await ctx.publish("llm_end", iteration=iteration + 1)

        # Track token usage
        usage = response.get("usage")
        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            ctx.total_prompt_tokens += usage.get("prompt_tokens", 0)
            ctx.total_completion_tokens += usage.get("completion_tokens", 0)

        # If there are tool calls, execute them
        tool_calls = response.get("tool_calls")
        if tool_calls:
            # Add the assistant's tool-call message to history
            assistant_msg = {"role": "assistant", "content": response.get("content")}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            history.append(assistant_msg)
            messages.append(assistant_msg)
            _archive(ctx, assistant_msg)

            # Execute each tool and add results
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])
                log.info(f"Tool call: {fn_name}({fn_args})")

                await ctx.publish("tool_start", tool=fn_name, args=fn_args)
                result = await execute_tool(ctx, fn_name, fn_args)
                await ctx.publish("tool_end", tool=fn_name)
                log.debug(f"Tool result: {result.text[:200]}...")

                # Accumulate media from tool results
                if result.media:
                    pending_media.extend(result.media)

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result.text,
                }
                history.append(tool_msg)
                messages.append(tool_msg)
                _archive(ctx, tool_msg)

            # Loop back to call the LLM again with tool results
            continue

        # No tool calls — we have a final response
        content = response.get("content", "")
        final_msg = {"role": "assistant", "content": content}
        history.append(final_msg)
        _archive(ctx, final_msg)
        await _maybe_compact(ctx, config, history, prompt_tokens)

        # Scan for workspace image references and combine with tool media
        cleaned_text, workspace_media = extract_workspace_media(
            content or "", config.workspace_path
        )
        all_media = pending_media + workspace_media

        if all_media:
            return ToolResult(text=cleaned_text, media=all_media)
        return ToolResult(text=content or "")

    # Hit max iterations
    msg = "[Agent reached max tool iterations without a final response]"
    final_msg = {"role": "assistant", "content": msg}
    history.append(final_msg)
    _archive(ctx, final_msg)
    await _maybe_compact(ctx, config, history, prompt_tokens)
    return ToolResult(text=msg)


async def run_interactive(ctx):
    """Run the agent in interactive terminal mode (stdin/stdout)."""
    config = ctx.config

    # Populate context defaults for interactive mode
    ctx.user_id = getattr(ctx, "user_id", None) or config.agent_user_id
    ctx.channel_id = getattr(ctx, "channel_id", "") or "interactive"
    ctx.channel_name = getattr(ctx, "channel_name", "") or "interactive"
    ctx.thread_id = getattr(ctx, "thread_id", "") or ""
    ctx.conv_id = "interactive"

    # Set up media handler for terminal mode
    from .media import TerminalMediaHandler
    ctx.media_handler = TerminalMediaHandler(config.workspace_path)

    # Set up streaming callback for terminal mode
    if config.llm_streaming:
        async def _terminal_stream_chunk(chunk_type, data):
            if chunk_type == "text":
                print(data, end="", flush=True)
            elif chunk_type == "tool_call_start":
                print(f"\n  [calling {data['name']}...]", flush=True)

        ctx.on_stream_chunk = _terminal_stream_chunk

    # Connect MCP servers
    from .heartbeat import run_heartbeat_timer
    from .mcp_client import get_registry, init_mcp, shutdown_mcp
    await init_mcp(config)

    print("DecafClaw interactive mode. Type 'quit' to exit.")
    print(f"Model: {config.llm_model}")
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
            print(f"\n  \U0001f6a8 Confirm {tool_name}: {command}")
            answer = await asyncio.to_thread(input, "  Approve? [y]es / [n]o / [a]lways: ")
            choice = answer.strip().lower()
            approved = choice in ("y", "yes", "a", "always")
            always = choice in ("a", "always")
            await ctx.event_bus.publish({
                "type": "tool_confirm_response",
                "context_id": event.get("context_id"),
                "tool": tool_name,
                "approved": approved,
                "always": always,
            })

    sub_id = ctx.event_bus.subscribe(on_progress)

    # Resume from archive if available
    from .archive import read_archive
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
            from .media import process_media_for_terminal

            if config.llm_streaming:
                # Text was already printed token-by-token; handle media only
                if result.media:
                    output = process_media_for_terminal(result, config.workspace_path)
                    # Print just the media lines (text was already streamed)
                    media_lines = [line for line in output.split("\n")
                                   if line.startswith("[file saved:") or line.startswith("[image:")]
                    if media_lines:
                        print("\n" + "\n".join(media_lines))
                print()  # final newline after streamed text
            else:
                output = process_media_for_terminal(result, config.workspace_path)
                print(f"\nagent> {output}\n")
    finally:
        shutdown_event.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        ctx.event_bus.unsubscribe(sub_id)
        await shutdown_mcp()
