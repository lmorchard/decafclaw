"""Composer lifecycle pipeline: system prompt, PRE_COMPOSE dispatch, mounting (#1491).

Owns how collector hooks become HEAD/DELTA/TAIL/EPILOGUE messages and source
entries. The orchestrator in ``context_composer.py`` calls these helpers;
extensions only see the lifecycle contract.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any

from .archive import LLM_ROLES
from .attachments import resolve_attachments
from .context_messages import _expand_background_event, _reorder_tool_results
from .context_types import ROLE_REMAP, SourceEntry
from .events import TurnLifecycle
from .extensions import ContextContribution, ContextSlot
from .image_budget import apply_image_budget
from .util import estimate_tokens

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def compose_system_prompt(config) -> tuple[str, SourceEntry]:
    """Build the system prompt message content and track it as a source.

    Uses the pre-assembled config.system_prompt (set at startup, includes
    always-loaded skill bodies). Per-conversation activated skill bodies
    are delivered as tool results, not injected into the system prompt.
    """
    text = config.system_prompt
    tokens = estimate_tokens(text)
    entry = SourceEntry(
        source="system_prompt",
        tokens_estimated=tokens,
        items_included=1,
    )
    return text, entry


async def compose_pre_compose_hooks(
    ctx: "Context", config: Any, user_message: str, history: list
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[SourceEntry]]:
    """Run PRE_COMPOSE lifecycle collector hooks concurrently.

    Hooks may return structured ``ContextContribution`` objects (or lists
    of them) for the HEAD, DELTA, TAIL, and EPILOGUE slots, or legacy ``str``
    / ``list[str]`` fact text. Legacy text is coerced to one TAIL
    ``<recalled_facts>`` envelope under the configured timeout and token
    ceiling, exactly as before (#1454, #1486). Structured contributions
    carry their own messages and are not subject to that legacy ceiling.

    Returns ``(head, delta, tail, epilogue, sources)``. Fails open: a raising
    or slow hook is logged and the turn still composes.
    """
    hooks = ctx.get_interceptors(TurnLifecycle.PRE_COMPOSE)
    if not hooks:
        return [], [], [], [], []

    lifecycle_cfg = getattr(config, "lifecycle", None)
    default_timeout = getattr(lifecycle_cfg, "pre_compose_timeout", 0.3)
    token_ceiling = getattr(lifecycle_cfg, "pre_compose_token_ceiling", 300)

    def _hook_timeout(hook) -> float:
        """Per-hook bound; extensions may declare a larger one (#1489)."""
        owner = getattr(hook, "__self__", None)
        declared = getattr(owner, "pre_compose_timeout", None)
        if declared is None:
            declared = getattr(hook, "pre_compose_timeout", None)
        if isinstance(declared, (int, float)) and declared > 0:
            return float(declared)
        return float(default_timeout)

    async def _call_hook(hook):
        sig = inspect.signature(hook)
        params = list(sig.parameters.values())
        has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
        if has_varargs or len(params) >= 3:
            res = hook(ctx, user_message, history)
        elif len(params) == 2:
            res = hook(ctx, user_message)
        elif len(params) == 1:
            p_name = params[0].name
            if p_name in ("user_message", "message", "msg"):
                res = hook(user_message)
            else:
                res = hook(ctx)
        else:
            res = hook()
        if inspect.iscoroutine(res):
            res = await res
        return res

    async def _safe_call_hook(hook):
        try:
            return await asyncio.wait_for(_call_hook(hook), timeout=_hook_timeout(hook))
        except asyncio.TimeoutError:
            log.warning("PRE_COMPOSE hook %s timed out after %.3fs", hook, _hook_timeout(hook))
            return None
        except Exception as e:
            log.warning("PRE_COMPOSE hook %s failed: %s", hook, e)
            return None

    tasks = [asyncio.ensure_future(_safe_call_hook(h)) for h in hooks]
    outer_timeout = max(_hook_timeout(h) for h in hooks) + 0.05
    try:
        done, pending = await asyncio.wait(tasks, timeout=outer_timeout)
    except Exception as e:
        log.warning("PRE_COMPOSE gathering failed: %s", e)
        return [], [], [], [], []
    if pending:
        # Keep what finished inside the bound and drop only the slow hooks.
        # Discarding completed contributions would silently lose context
        # that a hook already advanced cursors for (scratchpad notes,
        # #1487) whenever a sibling hook is slow.
        log.warning("PRE_COMPOSE hook exceeded its bound and ignored cancellation")
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    results = []
    for task in tasks:
        if task not in done:
            continue
        if task.cancelled():
            # A hook that received cancellation (user cancel, shutdown)
            # must not have that swallowed by the fail-open collector.
            raise asyncio.CancelledError()
        try:
            results.append(task.result())
        except Exception as e:
            log.warning("PRE_COMPOSE hook failed: %s", e)

    head: list[dict] = []
    delta: list[dict] = []
    tail: list[dict] = []
    epilogue: list[dict] = []
    sources: list[SourceEntry] = []
    fact_texts: list[str] = []

    for r in results:
        if r is None:
            continue
        if isinstance(r, ContextContribution):
            mount_context_contribution(r, head, delta, tail, epilogue, sources)
        elif isinstance(r, str):
            if r.strip():
                fact_texts.append(r.strip())
        elif isinstance(r, (list, tuple)):
            for item in r:
                if isinstance(item, ContextContribution):
                    mount_context_contribution(item, head, delta, tail, epilogue, sources)
                elif isinstance(item, str):
                    if item.strip():
                        fact_texts.append(item.strip())
                else:
                    log.warning(
                        "PRE_COMPOSE hook returned unsupported item %s; ignoring",
                        type(item).__name__,
                    )
        else:
            log.warning(
                "PRE_COMPOSE hook returned unsupported result %s; ignoring",
                type(r).__name__,
            )

    if fact_texts:
        tail.append(legacy_recalled_facts_message(fact_texts, token_ceiling))
        envelope_text = tail[-1]["content"]
        sources.append(
            SourceEntry(
                source="recalled_facts",
                tokens_estimated=estimate_tokens(envelope_text),
                items_included=len(fact_texts),
            )
        )

    return head, delta, tail, epilogue, sources


def mount_context_contribution(
    contribution: ContextContribution,
    head: list[dict],
    delta: list[dict],
    tail: list[dict],
    epilogue: list[dict],
    sources: list[SourceEntry],
) -> None:
    """Validate and mount one structured contribution; fail open."""
    if not isinstance(contribution.slot, ContextSlot):
        log.warning("PRE_COMPOSE contribution has unknown slot %r; ignoring", contribution.slot)
        return
    messages = contribution.messages
    if not isinstance(messages, (list, tuple)) or any(not isinstance(m, dict) for m in messages):
        log.warning(
            "PRE_COMPOSE contribution for slot %s has malformed messages; ignoring",
            contribution.slot.value,
        )
        return

    target = {
        ContextSlot.HEAD: head,
        ContextSlot.DELTA: delta,
        ContextSlot.TAIL: tail,
        ContextSlot.EPILOGUE: epilogue,
    }[contribution.slot]
    target.extend(messages)

    if contribution.source_entry is None:
        return
    if isinstance(contribution.source_entry, SourceEntry):
        sources.append(contribution.source_entry)
    else:
        log.warning(
            "PRE_COMPOSE contribution for slot %s has malformed source_entry; ignoring",
            contribution.slot.value,
        )


def legacy_recalled_facts_message(fact_texts: list[str], token_ceiling: int) -> dict:
    """Coerce legacy ``str``/``list[str]`` hook results into one TAIL envelope."""
    combined_text = "\n".join(fact_texts)
    if "<recalled_facts>" in combined_text:
        envelope_text = combined_text
    else:
        envelope_text = f"<recalled_facts>\n{combined_text}\n</recalled_facts>"

    if estimate_tokens(envelope_text) > token_ceiling:
        lines = combined_text.splitlines()
        trimmed_lines = []
        current_tokens = estimate_tokens("<recalled_facts>\n\n</recalled_facts>")
        for line in lines:
            line_tokens = estimate_tokens(line + "\n")
            if current_tokens + line_tokens > token_ceiling:
                break
            trimmed_lines.append(line)
            current_tokens += line_tokens
        trimmed_body = "\n".join(trimmed_lines)
        if not trimmed_body:
            trimmed_body = combined_text[: token_ceiling * 4]
        envelope_text = f"<recalled_facts>\n{trimmed_body}\n</recalled_facts>"

    return {"role": "recalled_facts", "content": envelope_text}


def account_history_tokens(history: list) -> tuple[int, int]:
    """Count LLM-visible archived history tokens and messages.

    Counts roles sent directly (``LLM_ROLES``), after remap (``ROLE_REMAP``), or
    after expansion (``background_event`` -> synthetic tool-call pair). Other
    archive-only roles are skipped.
    """
    countable_roles = LLM_ROLES | ROLE_REMAP.keys()
    history_tokens = 0
    history_msg_count = 0
    for message in history:
        role = message.get("role")
        if role == "background_event":
            expanded = _expand_background_event(message)
            history_tokens += sum(estimate_tokens(str(m.get("content", ""))) for m in expanded)
            history_msg_count += len(expanded)
        elif role in countable_roles:
            history_tokens += estimate_tokens(str(message.get("content", "")))
            history_msg_count += 1
    return history_tokens, history_msg_count


def assemble_provider_history(combined: list, config: Any, ctx: "Context", active_tools: list[dict]):
    """Filter, remap, reorder, resolve attachments, and apply the image budget."""
    llm_history = []
    for message in combined:
        role = message.get("role")
        if role == "background_event":
            llm_history.extend(_expand_background_event(message))
        elif role in LLM_ROLES:
            llm_history.append(message)
        elif role in ROLE_REMAP:
            llm_history.append({**message, "role": ROLE_REMAP[role]})
    llm_history = _reorder_tool_results(llm_history)
    llm_history_originals = llm_history
    llm_history = [resolve_attachments(config, m) for m in llm_history]
    llm_history, image_report = apply_image_budget(
        config,
        ctx.active_model,
        llm_history,
        originals=llm_history_originals,
        tools=active_tools,
    )
    return llm_history, image_report
