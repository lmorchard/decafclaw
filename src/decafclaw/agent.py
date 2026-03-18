"""The agent loop — the core of DecafClaw.

This is where the interesting stuff happens. The loop:
1. Receives a message (from stdin or Mattermost)
2. Builds a prompt with system prompt + history + tools
3. Calls the LLM
4. If the LLM wants to use tools, executes them and loops
5. Returns the final text response
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from .archive import append_message
from .compaction import compact_history
from .llm import call_llm
from .tools import TOOL_DEFINITIONS, execute_tool

if TYPE_CHECKING:
    from .media import ToolResult

log = logging.getLogger(__name__)


def _conv_id(ctx) -> str:
    """Get conversation ID from context."""
    return getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", None) or "unknown"


def _archive(ctx, msg) -> None:
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
            asyncio.create_task(_index_conversation_message(ctx, msg))


async def _index_conversation_message(ctx, msg) -> None:
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


async def _maybe_compact(ctx, config, history, prompt_tokens) -> None:
    """Trigger compaction if token budget is exceeded."""
    log.info(f"Compaction check: prompt_tokens={prompt_tokens}, "
             f"threshold={config.compaction_max_tokens}")
    if prompt_tokens and prompt_tokens > config.compaction_max_tokens:
        log.info(f"Token budget exceeded ({prompt_tokens} > {config.compaction_max_tokens}), "
                 f"triggering compaction")
        try:
            await compact_history(ctx, history)
        except Exception as e:
            log.error(f"Compaction failed: {e}")


# -- Agent turn helpers --------------------------------------------------------


def _check_cancelled(ctx, history):
    """Check if the agent turn has been cancelled. Returns ToolResult or None."""
    from .media import ToolResult
    if getattr(ctx, "cancelled", None) and ctx.cancelled.is_set():
        log.info("Agent turn cancelled by user")
        msg = "[Agent turn cancelled by user]"
        final_msg = {"role": "assistant", "content": msg}
        history.append(final_msg)
        _archive(ctx, final_msg)
        return ToolResult(text=msg)
    return None


def _collect_all_tool_defs(ctx) -> list:
    """Gather all available tool definitions (core + skill + MCP + extra).

    Does NOT apply allowed_tools filter — returns the full unfiltered set
    so classification can see everything before deciding what to defer.
    """
    all_tools = list(TOOL_DEFINITIONS) + getattr(ctx, "extra_tool_definitions", [])

    # Pre-load tool definitions from discovered skills (stable tool list).
    # Cached on config to avoid re-executing tools.py every iteration.
    _cached = getattr(ctx.config, "_preloaded_skill_defs", None)
    if _cached is None:
        _cached = []
        for skill_info in getattr(ctx.config, "discovered_skills", []):
            if skill_info.has_native_tools:
                try:
                    from .tools.skill_tools import _load_native_tools
                    _, tool_defs, _ = _load_native_tools(skill_info)
                    _cached.extend(tool_defs)
                except Exception as e:
                    log.warning(f"Failed to pre-load skill '{skill_info.name}' tools: {e}")
        ctx.config._preloaded_skill_defs = _cached

    preloaded_names = {t.get("function", {}).get("name") for t in all_tools}
    for td in _cached:
        name = td.get("function", {}).get("name")
        if name and name not in preloaded_names:
            all_tools.append(td)
            preloaded_names.add(name)

    from .mcp_client import get_registry
    mcp_registry = get_registry()
    if mcp_registry:
        all_tools = all_tools + mcp_registry.get_tool_definitions()

    return all_tools


def _build_tool_list(ctx) -> tuple[list, str | None]:
    """Build the tool list, with optional deferred mode.

    Returns (tool_definitions, deferred_text) where deferred_text is
    None if all tools fit in the budget, or a system prompt block
    listing deferred tools when the budget is exceeded.
    """
    from .tools.search_tools import SEARCH_TOOL_DEFINITIONS
    from .tools.tool_registry import (
        build_deferred_list_text,
        classify_tools,
        get_fetched_tools,
    )

    all_defs = _collect_all_tool_defs(ctx)
    fetched = get_fetched_tools(ctx)
    active, deferred = classify_tools(all_defs, ctx.config, fetched)

    # Apply allowed_tools filter to the active set only
    allowed = getattr(ctx, "allowed_tools", None)
    if allowed is not None:
        active = [
            t for t in active
            if t.get("function", {}).get("name") in allowed
        ]

    if not deferred:
        return active, None

    # Deferred mode: set the pool on ctx and add tool_search
    ctx.deferred_tool_pool = deferred
    active = active + SEARCH_TOOL_DEFINITIONS

    # Build deferred list text for system prompt
    core_names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}
    deferred_text = build_deferred_list_text(deferred, core_names=core_names)

    return active, deferred_text


async def _call_llm_with_events(ctx, config, messages, tools) -> dict:
    """Call the LLM with event publishing for progress tracking."""
    iteration = getattr(ctx, "_current_iteration", 1)
    await ctx.publish("llm_start", iteration=iteration)
    if config.llm_streaming:
        from .llm import call_llm_streaming
        on_chunk = getattr(ctx, "on_stream_chunk", None)
        cancel_event = getattr(ctx, "cancelled", None)
        response = await call_llm_streaming(
            config, messages, tools=tools, on_chunk=on_chunk, cancel_event=cancel_event
        )
    else:
        cancel_event = getattr(ctx, "cancelled", None)
        if cancel_event:
            llm_task = asyncio.create_task(call_llm(config, messages, tools=tools))
            cancel_task = asyncio.create_task(cancel_event.wait())
            done, _ = await asyncio.wait(
                [llm_task, cancel_task], return_when=asyncio.FIRST_COMPLETED
            )
            cancel_task.cancel()
            if llm_task not in done:
                llm_task.cancel()
                try:
                    await llm_task
                except (asyncio.CancelledError, Exception):
                    pass
                response = {"content": "", "tool_calls": None, "role": "assistant", "usage": {}}
            else:
                response = llm_task.result()
        else:
            response = await call_llm(config, messages, tools=tools)
    await ctx.publish("llm_end", iteration=iteration,
                      content=response.get("content"),
                      has_tool_calls=bool(response.get("tool_calls")))
    return response


async def _execute_single_tool(call_ctx, tc, semaphore):
    """Execute one tool call. Returns (tool_msg, media_list).

    Designed to run concurrently — uses its own forked ctx so
    current_tool_call_id doesn't race with other calls.
    """
    from .media import ToolResult

    tool_call_id = tc["id"]
    fn_name = tc["function"]["name"]
    try:
        fn_args = json.loads(tc["function"]["arguments"])
    except json.JSONDecodeError as e:
        log.error(f"Malformed tool call arguments for {fn_name}: {e}")
        fn_args = {}

    log.info(f"Tool call: {fn_name}({fn_args})")

    result = ToolResult(text=f"[error: {fn_name} did not complete]")
    async with semaphore:
        try:
            await call_ctx.publish("tool_start", tool=fn_name, args=fn_args,
                                   tool_call_id=tool_call_id)
            result = await execute_tool(call_ctx, fn_name, fn_args)
            log.debug(f"Tool result [{fn_name}]: {result.text[:200]}...")
        except asyncio.CancelledError:
            result = ToolResult(text=f"[cancelled: {fn_name}]")
        except Exception as e:
            log.error(f"Tool call {fn_name} failed: {e}", exc_info=True)
            result = ToolResult(text=f"[error executing {fn_name}: {e}]")
        finally:
            await call_ctx.publish("tool_end", tool=fn_name,
                                   result_text=result.text,
                                   display_text=getattr(result, "display_text", None),
                                   media=result.media or [],
                                   tool_call_id=tool_call_id)

    tool_msg = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": result.text,
    }
    _archive(call_ctx, tool_msg)
    return tool_msg, result.media or []


async def _execute_tool_calls(ctx, tool_calls, history, messages, pending_media):
    """Execute tool calls concurrently, add results to history.

    Returns ToolResult if cancelled, None otherwise.
    """
    cancelled = _check_cancelled(ctx, history)
    if cancelled:
        return cancelled

    semaphore = asyncio.Semaphore(ctx.config.max_concurrent_tools)

    # Fork ctx per tool call so concurrent tools don't race on current_tool_call_id
    tasks = []
    for tc in tool_calls:
        call_ctx = ctx.fork_for_tool_call(tc["id"])
        task = asyncio.create_task(
            _execute_single_tool(call_ctx, tc, semaphore),
            name=f"tool-{tc['function']['name']}-{tc['id'][:8]}",
        )
        tasks.append(task)

    # Cancel watcher: if the cancel event fires, cancel all in-flight tasks
    cancel_event = getattr(ctx, "cancelled", None)

    async def _cancel_watcher():
        if cancel_event:
            await cancel_event.wait()
            for t in tasks:
                t.cancel()

    watcher = asyncio.create_task(_cancel_watcher()) if cancel_event else None

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if watcher:
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass

    # Check if we were cancelled during execution
    cancelled = _check_cancelled(ctx, history)
    if cancelled:
        return cancelled

    # Collect results in original call order (gather preserves order)
    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            # Task was cancelled or failed — gather with return_exceptions.
            # _execute_single_tool normally handles errors internally,
            # so this only fires for unexpected failures (e.g. CancelledError
            # from the cancel watcher).
            err_type = type(result).__name__
            err_text = str(result) or err_type
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_calls[i]["id"],
                "content": f"[error: {err_text}]",
            }
            history.append(tool_msg)
            messages.append(tool_msg)
            _archive(ctx, tool_msg)
        else:
            tool_msg, media = result  # type: ignore[misc]
            history.append(tool_msg)
            messages.append(tool_msg)
            if media:
                pending_media.extend(media)

    return None


# -- Main agent turn -----------------------------------------------------------


async def run_agent_turn(ctx, user_message: str, history: list) -> "ToolResult":
    """Process a single user message through the agent loop.

    Args:
        ctx: Runtime context (carries config, event bus, etc.)
        user_message: The user's message text
        history: Conversation history (list of message dicts, mutated in place)

    Returns:
        ToolResult with the agent's text response and any accumulated media.
    """
    from .archive import read_skill_data, read_skills_state, write_skill_data, write_skills_state
    from .media import ToolResult, extract_workspace_media
    from .tools.skill_tools import restore_skills

    config = ctx.config
    ctx.history = history

    # Restore previously-activated skills from the sidecar (survives restarts).
    # Merge with any skills already on ctx (e.g. set by Mattermost in-session state).
    conv_id = getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", "")
    if conv_id:
        persisted = read_skills_state(config, conv_id)
        existing = set(getattr(ctx, "activated_skills", set()))
        if persisted - existing:
            ctx.activated_skills = existing | persisted
        # Restore skill_data (e.g. vault base path) from sidecar
        persisted_data = read_skill_data(config, conv_id)
        existing_data = getattr(ctx, "skill_data", {})
        ctx.skill_data = {**persisted_data, **existing_data}
    await restore_skills(ctx)
    ctx.total_prompt_tokens = getattr(ctx, "total_prompt_tokens", 0)
    ctx.total_completion_tokens = getattr(ctx, "total_completion_tokens", 0)

    pending_media = []

    try:
        # Add user message to history
        # Truncate oversized user messages
        max_len = config.max_message_length
        if max_len and len(user_message) > max_len:
            original_len = len(user_message)
            user_message = (
                user_message[:max_len]
                + f"\n\n[truncated at {max_len:,} chars, original was {original_len:,}]"
            )
            log.warning(f"User message truncated: {original_len:,} -> {max_len:,} chars")

        user_msg = {"role": "user", "content": user_message}
        history.append(user_msg)
        _archive(ctx, user_msg)

        # Build the messages array: system prompt + history
        messages = [{"role": "system", "content": config.system_prompt}] + history
        ctx.messages = messages

        # Slot for deferred tools system message (replaced each iteration)
        deferred_msg: dict | None = None

        prompt_tokens = 0
        empty_retries = 0

        accumulated_text_parts = []  # text from iterations that also had tool calls

        for iteration in range(config.max_tool_iterations):
            cancelled = _check_cancelled(ctx, history)
            if cancelled:
                return cancelled

            log.debug(f"Agent iteration {iteration + 1}")
            ctx._current_iteration = iteration + 1

            all_tools, deferred_text = _build_tool_list(ctx)

            # Inject/update deferred tool list in messages
            if deferred_text:
                new_msg = {"role": "system", "content": deferred_text}
                if deferred_msg is not None and deferred_msg in messages:
                    idx = messages.index(deferred_msg)
                    messages[idx] = new_msg
                else:
                    # Insert after the first system message
                    messages.insert(1, new_msg)
                deferred_msg = new_msg
            elif deferred_msg is not None and deferred_msg in messages:
                # No longer in deferred mode — remove the block
                messages.remove(deferred_msg)
                deferred_msg = None

            response = await _call_llm_with_events(ctx, config, messages, all_tools)

            # Track token usage
            usage = response.get("usage")
            if usage:
                prompt_tokens = usage.get("prompt_tokens", 0)
                ctx.total_prompt_tokens += prompt_tokens
                ctx.total_completion_tokens += usage.get("completion_tokens", 0)
                ctx.last_prompt_tokens = prompt_tokens

            tool_calls = response.get("tool_calls")
            if tool_calls:
                # Add the assistant's tool-call message to history
                iter_content = response.get("content")
                assistant_msg = {"role": "assistant", "content": iter_content}
                assistant_msg["tool_calls"] = tool_calls
                history.append(assistant_msg)
                messages.append(assistant_msg)
                _archive(ctx, assistant_msg)

                # Flush any text content to the UI before starting tool execution.
                # Without this, text like "Let me check the weather..." appears
                # after tool results instead of before.
                if iter_content:
                    accumulated_text_parts.append(iter_content)
                    await ctx.publish("text_before_tools", text=iter_content)

                cancelled = await _execute_tool_calls(
                    ctx, tool_calls, history, messages, pending_media
                )
                if cancelled:
                    return cancelled
                continue

            # No tool calls — final response
            content = response.get("content") or ""
            if not content:
                # Retry once on empty response — Gemini sometimes returns
                # 0 completion tokens, especially after tool list changes.
                if empty_retries < 1:
                    empty_retries += 1
                    log.warning("LLM returned empty response, retrying")
                    continue
                log.warning("LLM returned empty content with no tool calls (after retry)")
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

        # Hit max iterations — preserve accumulated text from tool-call iterations
        limit_note = (f"\n\n[Agent reached max tool iterations "
                      f"({config.max_tool_iterations}) without a final response]")
        accumulated = "\n\n".join(accumulated_text_parts)
        msg = accumulated + limit_note if accumulated else limit_note.strip()
        final_msg = {"role": "assistant", "content": msg}
        history.append(final_msg)
        _archive(ctx, final_msg)
        await _maybe_compact(ctx, config, history, prompt_tokens)
        return ToolResult(text=msg)

    finally:
        # Persist activated skills and skill_data after every turn
        if conv_id:
            activated = getattr(ctx, "activated_skills", None)
            if activated:
                write_skills_state(config, conv_id, activated)
            skill_data = getattr(ctx, "skill_data", {})
            if skill_data:
                write_skill_data(config, conv_id, skill_data)


# -- Interactive mode helpers --------------------------------------------------


def _setup_interactive_context(ctx) -> None:
    """Populate context defaults and media handler for interactive mode."""
    from .media import TerminalMediaHandler
    config = ctx.config

    ctx.user_id = getattr(ctx, "user_id", None) or config.agent_user_id
    ctx.channel_id = getattr(ctx, "channel_id", "") or "interactive"
    ctx.channel_name = getattr(ctx, "channel_name", "") or "interactive"
    ctx.thread_id = getattr(ctx, "thread_id", "") or ""
    ctx.conv_id = "interactive"
    ctx.media_handler = TerminalMediaHandler(config.workspace_path)

    if config.llm_streaming:
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
    from .heartbeat import run_heartbeat_timer
    from .mcp_client import init_mcp, shutdown_mcp

    config = ctx.config

    _setup_interactive_context(ctx)
    await init_mcp(config)
    _print_banner(config)

    sub_id = ctx.event_bus.subscribe(_create_interactive_progress_subscriber(ctx))

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
