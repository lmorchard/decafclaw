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
from dataclasses import replace

from .archive import append_message
from .compaction import compact_history
from .embeddings import index_entry
from .llm import call_llm
from .media import ToolResult, extract_workspace_media
from .persistence import read_skill_data, read_skills_state, write_skill_data, write_skills_state
from .tools import TOOL_DEFINITIONS, execute_tool
from .tools.search_tools import SEARCH_TOOL_DEFINITIONS
from .tools.tool_registry import (
    build_deferred_list_text,
    classify_tools,
    get_fetched_tools,
)

# Cache preloaded skill definitions by config id, avoiding Config mutation
_skill_def_cache: dict[int, list] = {}


def invalidate_skill_cache(config) -> None:
    """Clear the cached skill definitions for a config. Call after refresh_skills."""
    _skill_def_cache.pop(id(config), None)

log = logging.getLogger(__name__)

# Track background tasks to prevent GC and surface exceptions
_background_tasks: set[asyncio.Task] = set()


def _conv_id(ctx) -> str:
    """Get conversation ID from context."""
    return ctx.conv_id or ctx.channel_id or "unknown"


def _archive(ctx, msg) -> None:
    """Archive a message, logging errors but never raising."""
    try:
        append_message(ctx.config, _conv_id(ctx), msg)
    except Exception as e:
        log.error(f"Archive write failed: {e}")

    # Index user/assistant messages for conversation search (fire and forget)
    # Requires an embedding model to be configured
    if ctx.config.embedding.model and msg.get("role") in ("user", "assistant"):
        content = msg.get("content")
        if content and len(content) > 20:  # skip trivial messages
            task = asyncio.create_task(_index_conversation_message(ctx, msg))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)


async def _index_conversation_message(ctx, msg) -> None:
    """Index a conversation message for semantic search (background)."""
    try:
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
             f"threshold={config.compaction.max_tokens}")
    if prompt_tokens and prompt_tokens > config.compaction.max_tokens:
        log.info(f"Token budget exceeded ({prompt_tokens} > {config.compaction.max_tokens}), "
                 f"triggering compaction")
        try:
            await compact_history(ctx, history)
        except Exception as e:
            log.error(f"Compaction failed: {e}")


# -- Agent turn helpers --------------------------------------------------------


def _check_cancelled(ctx, history):
    """Check if the agent turn has been cancelled. Returns ToolResult or None."""
    if ctx.cancelled and ctx.cancelled.is_set():
        log.info("Agent turn cancelled by user")
        msg = "[Agent turn cancelled by user]"
        final_msg = {"role": "assistant", "content": msg}
        history.append(final_msg)
        _archive(ctx, final_msg)
        return ToolResult(text=msg)
    return None


def _should_reflect(ctx, config, content: str, reflection_retries: int) -> bool:
    """Check whether reflection should run on this response."""
    if not config.reflection.enabled:
        return False
    if reflection_retries >= config.reflection.max_retries:
        return False
    if ctx.is_child:
        return False
    if not content or not content.strip():
        return False
    if getattr(ctx, "cancelled", None) and ctx.cancelled.is_set():
        return False
    return True


def _collect_all_tool_defs(ctx) -> list:
    """Gather all available tool definitions (core + skill + MCP + extra).

    Does NOT apply allowed_tools filter — returns the full unfiltered set
    so classification can see everything before deciding what to defer.
    """
    all_tools = list(TOOL_DEFINITIONS) + ctx.extra_tool_definitions

    # Pre-load tool definitions from discovered skills (stable tool list).
    # Cached by config id to avoid re-executing tools.py every iteration.
    config_id = id(ctx.config)
    _cached = _skill_def_cache.get(config_id)
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
        _skill_def_cache[config_id] = _cached

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
    all_defs = _collect_all_tool_defs(ctx)
    fetched = get_fetched_tools(ctx)
    active, deferred = classify_tools(all_defs, ctx.config, fetched)

    # Apply allowed_tools filter to the active set only
    allowed = ctx.allowed_tools
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


async def _call_llm_with_events(ctx, config, messages, tools,
                                llm_url=None, llm_model=None, llm_api_key=None) -> dict:
    """Call the LLM with event publishing for progress tracking."""
    llm_kwargs: dict = {}
    if llm_url:
        llm_kwargs["llm_url"] = llm_url
    if llm_model:
        llm_kwargs["llm_model"] = llm_model
    if llm_api_key:
        llm_kwargs["llm_api_key"] = llm_api_key

    iteration = ctx._current_iteration
    await ctx.publish("llm_start", iteration=iteration)
    if config.llm.streaming:
        from .llm import call_llm_streaming
        on_chunk = ctx.on_stream_chunk
        cancel_event = ctx.cancelled
        response = await call_llm_streaming(
            config, messages, tools=tools, on_chunk=on_chunk,
            cancel_event=cancel_event, **llm_kwargs
        )
    else:
        cancel_event = ctx.cancelled
        if cancel_event:
            llm_task = asyncio.create_task(
                call_llm(config, messages, tools=tools, **llm_kwargs))
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
            response = await call_llm(config, messages, tools=tools, **llm_kwargs)
    await ctx.publish("llm_end", iteration=iteration,
                      content=response.get("content"),
                      has_tool_calls=bool(response.get("tool_calls")))
    return response


async def _execute_single_tool(call_ctx, tc, semaphore):
    """Execute one tool call. Returns (tool_msg, media_list).

    Designed to run concurrently — uses its own forked ctx so
    current_tool_call_id doesn't race with other calls.
    """
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

    semaphore = asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)

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
    cancel_event = ctx.cancelled

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
    from .tools.skill_tools import restore_skills  # deferred: circular dep

    config = ctx.config
    ctx.history = history

    # Restore previously-activated skills from the sidecar (survives restarts).
    # Merge with any skills already on ctx (e.g. set by Mattermost in-session state).
    conv_id = ctx.conv_id or ctx.channel_id
    if conv_id:
        persisted = read_skills_state(config, conv_id)
        existing = set(ctx.activated_skills)
        if persisted - existing:
            ctx.activated_skills = existing | persisted
        # Restore skill_data (e.g. vault base path) from sidecar
        persisted_data = read_skill_data(config, conv_id)
        existing_data = ctx.skill_data
        ctx.skill_data = {**persisted_data, **existing_data}
    await restore_skills(ctx)

    # Restore effort level from archive (scan for last effort event)
    if ctx.effort == "default" and conv_id:
        from .archive import read_archive
        for msg in reversed(read_archive(config, conv_id)):
            if msg.get("role") == "effort":
                ctx.effort = msg.get("content", "default")
                break

    # Resolve effort level to LLM overrides (without mutating config.llm,
    # so compaction/reflection/embedding still fall back to the base model)
    from .config import resolve_effort
    effort_llm = resolve_effort(config, ctx.effort)
    effort_overrides: dict[str, str] = {}
    if effort_llm != config.llm:
        effort_overrides = {
            "llm_url": effort_llm.url,
            "llm_model": effort_llm.model,
            "llm_api_key": effort_llm.api_key,
        }
    log.info(f"Agent turn: effort={ctx.effort}, model={effort_llm.model}")

    pending_media = []

    try:
        # Add user message to history
        # Truncate oversized user messages
        max_len = config.agent.max_message_length
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
        # Filter out metadata roles (effort, reflection) that aren't valid LLM messages
        from .archive import LLM_ROLES
        llm_history = [m for m in history if m.get("role") in LLM_ROLES]
        messages = [{"role": "system", "content": config.system_prompt}] + llm_history
        ctx.messages = messages

        # Slot for deferred tools system message (replaced each iteration)
        deferred_msg: dict | None = None

        prompt_tokens = 0
        empty_retries = 0
        reflection_retries = 0
        reflection_exhausted = False
        last_reflection = None  # last ReflectionResult, for archiving after final response
        turn_start_index = len(history)  # index of user message we're about to add

        accumulated_text_parts = []  # text from iterations that also had tool calls

        for iteration in range(config.agent.max_tool_iterations):
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

            response = await _call_llm_with_events(ctx, config, messages, all_tools,
                                                     **effort_overrides)

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

            # Reflection check — evaluate before delivering
            log.debug("Reflection check: enabled=%s, retries=%d/%d, is_child=%s, has_content=%s",
                       config.reflection.enabled, reflection_retries,
                       config.reflection.max_retries, ctx.is_child, bool(content))
            if not _should_reflect(ctx, config, content, reflection_retries):
                reflection_exhausted = (
                    reflection_retries >= config.reflection.max_retries
                    and last_reflection is not None
                    and not last_reflection.passed
                )
                last_reflection = None  # clear stale result from prior retry
            else:
                from .reflection import build_tool_summary, evaluate_response

                tool_summary = build_tool_summary(history, turn_start_index)
                result = await evaluate_response(
                    config, user_message, content, tool_summary)

                last_reflection = result
                log.info("Reflection result: passed=%s, critique=%s, error=%s",
                         result.passed, result.critique[:200] if result.critique else "",
                         result.error[:100] if result.error else "")

                await ctx.publish("reflection_result",
                    passed=result.passed,
                    critique=result.critique,
                    raw_response=result.raw_response,
                    retry_number=reflection_retries + 1,
                    error=result.error)

                if not result.passed and not result.error:
                    log.info("Reflection failed (retry %d/%d): %s",
                             reflection_retries + 1,
                             config.reflection.max_retries,
                             result.critique[:200])
                    # Add the failed response to history
                    failed_msg = {"role": "assistant", "content": content}
                    history.append(failed_msg)
                    messages.append(failed_msg)
                    _archive(ctx, failed_msg)

                    # Add critique as user message for retry
                    critique_msg = {
                        "role": "user",
                        "content": (
                            "[reflection] Your previous response may not fully "
                            "address the user's request.\n"
                            f"Feedback: {result.critique}\n"
                            "Please try again, addressing the feedback above."
                        ),
                    }
                    history.append(critique_msg)
                    messages.append(critique_msg)
                    _archive(ctx, critique_msg)

                    reflection_retries += 1
                    continue  # back to LLM call

            # Suggest model escalation if reflection retries exhausted
            if reflection_exhausted and ctx.effort != "strong":
                content += (
                    "\n\n---\n*I'm not confident in this answer. "
                    "Try `!think-harder` to retry with a more capable model.*"
                )

            final_msg = {"role": "assistant", "content": content}
            history.append(final_msg)
            _archive(ctx, final_msg)

            # Archive reflection result after the final response (correct ordering)
            # Only archive if reflection ran for this specific response
            # (last_reflection is cleared when reflection is skipped)
            if last_reflection is not None:
                visibility = config.reflection.visibility
                r = last_reflection
                # Match visibility filtering: hidden=none, visible=failures, debug=all
                should_archive = (
                    visibility == "debug"
                    or (visibility == "visible" and not r.passed)
                )
                if should_archive:
                    detail = r.raw_response or r.critique or (
                        "Response passed evaluation" if r.passed else "No details")
                    label = ("reflection: PASS" if r.passed
                             else f"reflection: retry {reflection_retries}")
                    _archive(ctx, {"role": "reflection", "tool": label,
                                   "content": detail})

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
                      f"({config.agent.max_tool_iterations}) without a final response]")
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
            activated = ctx.activated_skills
            if activated:
                write_skills_state(config, conv_id, activated)
            skill_data = ctx.skill_data
            if skill_data:
                write_skill_data(config, conv_id, skill_data)


# -- Interactive mode helpers --------------------------------------------------


def _setup_interactive_context(ctx) -> None:
    """Populate context defaults and media handler for interactive mode."""
    from .media import TerminalMediaHandler
    config = ctx.config

    ctx.user_id = ctx.user_id or config.agent_user_id
    ctx.channel_id = ctx.channel_id or "interactive"
    ctx.channel_name = ctx.channel_name or "interactive"
    ctx.thread_id = ctx.thread_id or ""
    ctx.conv_id = "interactive"
    ctx.media_handler = TerminalMediaHandler(config.workspace_path)

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

            if config.llm.streaming:
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
