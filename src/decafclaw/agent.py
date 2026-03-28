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
import functools
import json
import logging
import re as _re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .reflection import ReflectionResult

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


def _resolve_attachments(config, message: dict) -> dict:
    """Transform a message with attachments into multimodal content for the LLM.

    Messages without attachments pass through unchanged. The archive stores
    plain text + attachment metadata; this builds the ephemeral content array.
    """
    atts = message.get("attachments")
    if not atts:
        return message

    from .attachments import read_attachment_base64

    content_parts: list[dict] = []
    text = message.get("content", "")
    if text:
        content_parts.append({"type": "text", "text": text})

    for att in atts:
        b64_data = read_attachment_base64(config, att)
        if b64_data is None:
            content_parts.append({
                "type": "text",
                "text": f"[attachment missing: {att.get('filename', '?')}]",
            })
            continue

        mime = att.get("mime_type", "application/octet-stream")
        # TODO(#137): MIME type is client-supplied — validate with magic bytes
        # server-side to prevent non-images from being base64-embedded
        if mime.startswith("image/"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64_data}"},
            })
        else:
            # Non-image: represent as a textual placeholder only
            # (binary data is not sent to the LLM)
            content_parts.append({
                "type": "text",
                "text": f"[file: {att.get('filename', '?')} ({mime})]",
            })

    # Return message with multimodal content, stripping attachments key
    result = {k: v for k, v in message.items() if k != "attachments"}
    result["content"] = content_parts
    return result


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
    if ctx.skip_reflection:
        return False
    if not content or not content.strip():
        return False
    if getattr(ctx, "cancelled", None) and ctx.cancelled.is_set():
        return False
    return True


async def _handle_reflection(
    ctx, config, messages, history, final_text,
    user_message, attachments, retrieved_context_text,
    turn_start_index, reflection_retries, last_reflection,
) -> tuple[str | None, bool, int, "ReflectionResult | None"]:
    """Run the reflection phase on a candidate final response.

    Returns (text, should_retry, reflection_retries, last_reflection):
    - Reflection skipped or passed: (final_text, False, retries, result)
    - Reflection failed with retries left: (None, True, retries+1, result)
      — critique has been injected into messages/history before returning
    - Reflection failed, no retries left: (text_with_escalation, False, retries, result)
    """
    if not _should_reflect(ctx, config, final_text, reflection_retries):
        reflection_exhausted = (
            reflection_retries >= config.reflection.max_retries
            and last_reflection is not None
            and not last_reflection.passed
        )
        last_reflection = None  # clear stale result from prior retry

        # Suggest model escalation if reflection retries exhausted
        if reflection_exhausted and ctx.effort != "strong":
            final_text += (
                "\n\n---\n*I'm not confident in this answer. "
                "Try `!think-harder` to retry with a more capable model.*"
            )
        return final_text, False, reflection_retries, last_reflection

    from .reflection import (
        build_prior_turn_summary,
        build_tool_summary,
        evaluate_response,
    )

    tool_summary = build_tool_summary(
        history, turn_start_index,
        max_result_len=config.reflection.max_tool_result_len,
    )
    # turn_start_index points past the current user message;
    # use turn_start_index - 1 to exclude it from prior turns
    prior_turn_summary = build_prior_turn_summary(
        history, turn_start_index - 1,
        max_turns=3,
        max_result_len=200,
    )
    # Annotate user message with attachment info for the judge —
    # it can't see the actual files but needs to know they exist
    judge_user_message = user_message
    if attachments:
        att_desc = ", ".join(
            f"{a.get('filename', '?')} ({a.get('mime_type', '?')})"
            for a in attachments
        )
        judge_user_message += f"\n\n[User attached files: {att_desc}]"
    result = await evaluate_response(
        config, judge_user_message, final_text, tool_summary,
        prior_turn_summary=prior_turn_summary,
        retrieved_context=retrieved_context_text,
    )

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
        failed_msg = {"role": "assistant", "content": final_text}
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
        return None, True, reflection_retries, last_reflection

    # Reflection passed (or errored out — fail-open)
    return final_text, False, reflection_retries, last_reflection


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


@functools.lru_cache(maxsize=128)
def _media_placeholder_pattern(filename: str) -> _re.Pattern:
    """Build a regex to find the placeholder for a given filename."""
    return _re.compile(
        r"\[file attached: " + _re.escape(filename) + r"[^\]]*\]"
    )


async def _process_tool_media(ctx, result: ToolResult) -> list[str]:
    """Process media items on a tool result — save/upload and replace placeholders.

    For handlers returning workspace_ref: replaces placeholder text with markdown refs.
    For handlers returning file_id: collects file_ids for caller to attach.

    Returns list of file_ids (for Mattermost attachment), empty for other channels.
    Clears result.media after processing.
    """
    if not result.media:
        return []

    handler = ctx.media_handler
    if handler is None:
        log.warning(f"No media handler — {len(result.media)} media item(s) not delivered")
        result.media.clear()
        return []

    conv_id = ctx.conv_id or ctx.channel_id or "unknown"
    file_ids = []

    for item in result.media:
        filename = item.get("filename", "unknown")
        content_type = item.get("content_type", "application/octet-stream")
        data = item.get("data", b"")

        try:
            save_result = await handler.save_media(conv_id, filename, data, content_type)
        except Exception as e:
            log.warning(f"Failed to save media {filename}: {e}")
            continue

        if save_result.workspace_ref:
            pattern = _media_placeholder_pattern(filename)
            if content_type.startswith("image/"):
                replacement = f"![{filename}]({save_result.workspace_ref})"
            else:
                replacement = f"[{filename}]({save_result.workspace_ref})"
            new_text, count = pattern.subn(replacement, result.text, count=1)
            if count > 0:
                result.text = new_text
            else:
                # No placeholder — append ref so the media is discoverable
                result.text = result.text.rstrip() + "\n" + replacement
        if save_result.file_id:
            file_ids.append(save_result.file_id)

    result.media.clear()
    return file_ids


async def _execute_single_tool(call_ctx, tc, semaphore):
    """Execute one tool call. Returns tool_msg dict.

    Designed to run concurrently — uses its own forked ctx so
    current_tool_call_id doesn't race with other calls.
    Media is processed per-tool-call via _process_tool_media().
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

            # Process media per-tool-call (save/upload, replace placeholders)
            file_ids = await _process_tool_media(call_ctx, result)
            if file_ids:
                await call_ctx.publish("tool_media_uploaded",
                                       tool=fn_name,
                                       file_ids=file_ids,
                                       tool_call_id=tool_call_id)
        except asyncio.CancelledError:
            result = ToolResult(text=f"[cancelled: {fn_name}]")
        except Exception as e:
            log.error(f"Tool call {fn_name} failed: {e}", exc_info=True)
            result = ToolResult(text=f"[error executing {fn_name}: {e}]")
        finally:
            await call_ctx.publish("tool_end", tool=fn_name,
                                   result_text=result.text,
                                   display_text=getattr(result, "display_text", None),
                                   display_short_text=getattr(result, "display_short_text", None),
                                   media=result.media or [],
                                   tool_call_id=tool_call_id)

    tool_msg = {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": result.text,
    }
    if result.display_short_text:
        tool_msg["display_short_text"] = result.display_short_text
    _archive(call_ctx, tool_msg)
    return tool_msg


async def _execute_tool_calls(ctx, tool_calls, history, messages):
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
            tool_msg = result
            history.append(tool_msg)
            messages.append(tool_msg)

    return None


# -- Turn setup helpers ---------------------------------------------------------


async def _setup_turn_state(ctx, config, history) -> dict[str, str]:
    """Restore persisted skill/effort state and resolve effort overrides.

    Handles:
    - Skill restoration from sidecar (persisted activated skills + skill_data)
    - Auto-activation of always-loaded bundled skills
    - Effort level restoration from archive
    - Effort resolution to LLM config overrides

    Returns effort_overrides dict (may be empty if no override needed).
    """
    from .tools.skill_tools import restore_skills  # deferred: circular dep

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

    # Auto-activate always-loaded skills (bundled only — trust boundary)
    from .skills import _BUNDLED_SKILLS_DIR
    bundled_dir = _BUNDLED_SKILLS_DIR.resolve()
    discovered = getattr(config, "discovered_skills", [])
    for skill_info in discovered:
        if not skill_info.always_loaded or skill_info.name in ctx.activated_skills:
            continue
        if not Path(skill_info.location).resolve().is_relative_to(bundled_dir):
            continue  # only bundled skills can be always-loaded
        from .tools.skill_tools import activate_skill_internal
        try:
            await activate_skill_internal(ctx, skill_info)
            log.debug(f"Auto-activated always-loaded skill '{skill_info.name}'")
        except Exception as e:
            log.error(f"Failed to auto-activate skill '{skill_info.name}': {e}")

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

    return effort_overrides


async def _prepare_messages(
    ctx, config, user_message: str, history: list,
    archive_text: str = "",
    attachments: list[dict] | None = None,
) -> tuple[list, str]:
    """Build the LLM messages array from history and user input.

    Handles:
    - Truncating oversized user messages
    - Injecting proactive memory context before the user message
    - Archiving the user message (using archive_text for inline commands)
    - Building the messages array (system prompt + filtered/remapped history)
    - Resolving attachments into multipart content

    Returns (messages, retrieved_context_text).
    """
    # Truncate oversized user messages
    max_len = config.agent.max_message_length
    if max_len and len(user_message) > max_len:
        original_len = len(user_message)
        user_message = (
            user_message[:max_len]
            + f"\n\n[truncated at {max_len:,} chars, original was {original_len:,}]"
        )
        log.warning(f"User message truncated: {original_len:,} -> {max_len:,} chars")

    user_msg: dict = {"role": "user", "content": user_message}
    if attachments:
        user_msg["attachments"] = attachments

    # Proactive memory context — inject before user message
    retrieved_context_text = ""
    if not ctx.skip_memory_context:
        from .memory_context import format_memory_context, retrieve_memory_context
        mc_results = await retrieve_memory_context(config, user_message)
        if mc_results:
            retrieved_context_text = format_memory_context(mc_results)
            mc_msg = {"role": "memory_context", "content": retrieved_context_text}
            history.append(mc_msg)
            _archive(ctx, mc_msg)
            if config.memory_context.show_in_ui:
                await ctx.publish("memory_context",
                                  text=retrieved_context_text,
                                  results=mc_results)

    history.append(user_msg)
    # Archive the display version for inline commands (short), full text for normal messages
    if archive_text:
        archive_msg: dict = {"role": "user", "content": archive_text}
        if attachments:
            archive_msg["attachments"] = attachments
    else:
        archive_msg = user_msg
    _archive(ctx, archive_msg)

    # Build the messages array: system prompt + history
    # Filter out metadata roles (effort, reflection) that aren't valid LLM messages
    # Remap non-standard roles that should appear in LLM context
    ROLE_REMAP = {"memory_context": "user"}
    from .archive import LLM_ROLES
    llm_history = []
    for m in history:
        role = m.get("role")
        if role in LLM_ROLES:
            llm_history.append(m)
        elif role in ROLE_REMAP:
            llm_history.append({**m, "role": ROLE_REMAP[role]})
    # Resolve attachments into multimodal content arrays for the LLM
    llm_history = [_resolve_attachments(config, m) for m in llm_history]
    messages = [{"role": "system", "content": config.system_prompt}] + llm_history

    return messages, retrieved_context_text


# -- Main agent turn -----------------------------------------------------------


async def run_agent_turn(ctx, user_message: str, history: list,
                         archive_text: str = "",
                         attachments: list[dict] | None = None) -> "ToolResult":
    """Process a single user message through the agent loop.

    Args:
        ctx: Runtime context (carries config, event bus, etc.)
        user_message: The user's message text
        archive_text: If set, archive this instead of user_message (for inline
                      commands where the full body is the LLM prompt but the
                      archive should show the short command)
        history: Conversation history (list of message dicts, mutated in place)

    Returns:
        ToolResult with the agent's text response and any accumulated media.
    """
    config = ctx.config
    ctx.history = history

    effort_overrides = await _setup_turn_state(ctx, config, history)

    conv_id = ctx.conv_id or ctx.channel_id

    try:
        messages, retrieved_context_text = await _prepare_messages(
            ctx, config, user_message, history,
            archive_text=archive_text, attachments=attachments,
        )
        ctx.messages = messages

        # Slot for deferred tools system message (replaced each iteration)
        deferred_msg: dict | None = None

        prompt_tokens = 0
        empty_retries = 0
        reflection_retries = 0
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
                    ctx, tool_calls, history, messages
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
            log.debug("Reflection check: enabled=%s, retries=%d/%d, skip=%s, has_content=%s",
                       config.reflection.enabled, reflection_retries,
                       config.reflection.max_retries, ctx.skip_reflection, bool(content))
            content, should_retry, reflection_retries, last_reflection = (
                await _handle_reflection(
                    ctx, config, messages, history, content,
                    user_message, attachments, retrieved_context_text,
                    turn_start_index, reflection_retries, last_reflection,
                )
            )
            if should_retry:
                continue

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

            # Extract workspace:// refs only for channels that need it
            # (Mattermost strips refs and uploads files; web/terminal render them in-place)
            handler = ctx.media_handler
            should_extract = (handler is None or handler.strips_workspace_refs)
            if should_extract:
                cleaned_text, workspace_media = extract_workspace_media(
                    content or "", config.workspace_path
                )
                if workspace_media:
                    return ToolResult(text=cleaned_text, media=workspace_media)
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

