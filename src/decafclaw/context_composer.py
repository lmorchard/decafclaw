"""Context composer — unified context assembly for agent turns.

Owns the entire pipeline for building what gets sent to the LLM each turn:
system prompt, conversation history, memory/wiki context, tool definitions.
Tracks per-turn diagnostics (what was included, token estimates, actuals).
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


# -- Enums --------------------------------------------------------------------


class ComposerMode(enum.Enum):
    """Agent turn mode — controls which context sources are included.

    Callers set the mode via ``ctx.task_mode`` (mapped in ``run_agent_turn``):
    - INTERACTIVE — default for Mattermost, web UI, terminal
    - HEARTBEAT — periodic heartbeat tasks (skips memory + wiki)
    - SCHEDULED — cron-style scheduled tasks (skips memory + wiki)
    - CHILD_AGENT — delegate_task sub-agents (skips memory + wiki)

    ``skip_memory_context`` on the context is an independent flag that
    skips memory retrieval without affecting wiki injection or mode.
    """
    INTERACTIVE = "interactive"
    HEARTBEAT = "heartbeat"
    SCHEDULED = "scheduled"
    CHILD_AGENT = "child_agent"


# -- Diagnostics --------------------------------------------------------------


@dataclass
class SourceEntry:
    """Diagnostic entry for a single context source."""
    source: str
    tokens_estimated: int
    items_included: int
    items_truncated: int = 0
    details: dict = field(default_factory=dict)


# -- Result -------------------------------------------------------------------


@dataclass
class ComposedContext:
    """The complete assembled context for an LLM call."""
    messages: list[dict]
    tools: list[dict]
    deferred_tools: list[dict]
    total_tokens_estimated: int
    sources: list[SourceEntry]
    messages_to_archive: list[dict] = field(default_factory=list)
    retrieved_context_text: str = ""


# -- Per-conversation state ---------------------------------------------------


@dataclass
class ComposerState:
    """State carried between turns for a single conversation.

    Shared across context forks (same conversation). Assumes single-writer:
    only one compose() call at a time per conversation. If delegate_task
    ever composes in parallel for the same conversation, this needs a lock.
    """
    last_sources: list[SourceEntry] = field(default_factory=list)
    last_total_tokens_estimated: int = 0
    last_prompt_tokens_actual: int = 0
    last_completion_tokens_actual: int = 0
    recent_memory_ids: list[str] = field(default_factory=list)


# -- Composer -----------------------------------------------------------------


class ContextComposer:
    """Assembles the complete context for each agent turn.

    Stateful per-conversation: tracks what was included and actual token usage
    across turns for diagnostics and future budget optimization.
    """

    def __init__(self, state: ComposerState | None = None):
        self.state = state or ComposerState()

    async def compose(
        self,
        ctx,
        user_message: str,
        history: list,
        *,
        mode: ComposerMode = ComposerMode.INTERACTIVE,
        attachments: list[dict] | None = None,
    ) -> ComposedContext:
        """Assemble the complete context for this turn.

        Orchestrates all context sources, mutates history in place (appending
        wiki/memory/user messages), publishes events, and returns the
        ready-to-send ComposedContext. Does NOT archive — the caller is
        responsible for persisting messages via the messages_to_archive list.
        """
        from .agent import _resolve_attachments
        from .archive import LLM_ROLES
        from .util import estimate_tokens

        config = ctx.config
        sources: list[SourceEntry] = []
        to_archive: list[dict] = []

        # -- Truncate oversized user messages --
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

        # -- System prompt --
        system_text, system_entry = self._compose_system_prompt(config)
        sources.append(system_entry)

        # -- Wiki context (injected before user message in history) --
        wiki_msgs, wiki_entry = self._compose_wiki_context(
            ctx, config, user_message, history, mode,
        )
        for wm in wiki_msgs:
            history.append(wm)
            to_archive.append(wm)
            await ctx.publish("wiki_context", text=wm["content"], page=wm.get("wiki_page"))
        if wiki_entry:
            sources.append(wiki_entry)

        # -- Memory context (injected before user message in history) --
        memory_msgs, retrieved_context_text, mc_results, memory_entry = await self._compose_memory_context(
            ctx, config, user_message, mode,
        )
        for mm in memory_msgs:
            history.append(mm)
            to_archive.append(mm)
        if memory_entry:
            sources.append(memory_entry)

        # -- User message (added to history; caller archives with archive_text if needed) --
        history.append(user_msg)
        to_archive.append(user_msg)

        # -- Build LLM messages (filter + remap roles) --
        role_remap = {"memory_context": "user", "wiki_context": "user"}
        llm_history = []
        for m in history:
            role = m.get("role")
            if role in LLM_ROLES:
                llm_history.append(m)
            elif role in role_remap:
                llm_history.append({**m, "role": role_remap[role]})
        llm_history = [_resolve_attachments(config, m) for m in llm_history]

        # -- History source entry --
        # Exclude wiki/memory messages — they have their own SourceEntry,
        # so counting them here would double-count tokens in the total.
        remapped_roles = set(role_remap.keys())
        history_only = [m for m in history if m.get("role") not in remapped_roles]
        history_tokens = sum(
            estimate_tokens(str(m.get("content", "")))
            for m in history_only
            if m.get("role") in LLM_ROLES
        )
        history_msg_count = sum(
            1 for m in history_only if m.get("role") in LLM_ROLES
        )
        history_entry = SourceEntry(
            source="history",
            tokens_estimated=history_tokens,
            items_included=history_msg_count,
            details={"total_llm_messages": len(llm_history)},
        )
        sources.append(history_entry)

        # -- Tools --
        active_tools, deferred_tools, deferred_text, tools_entry = self._compose_tools(ctx, config)
        sources.append(tools_entry)

        # -- Assemble final messages --
        messages = [{"role": "system", "content": system_text}]
        if deferred_text:
            messages.append({"role": "system", "content": deferred_text})
        messages.extend(llm_history)

        # -- Publish memory context event (after user message for UI ordering) --
        if mc_results and config.memory_context.show_in_ui:
            await ctx.publish("memory_context",
                              text=retrieved_context_text,
                              results=mc_results)

        # -- Total token estimate --
        total_tokens = sum(s.tokens_estimated for s in sources)

        # -- Update state --
        self.state.last_sources = sources
        self.state.last_total_tokens_estimated = total_tokens

        return ComposedContext(
            messages=messages,
            tools=active_tools,
            deferred_tools=deferred_tools,
            total_tokens_estimated=total_tokens,
            sources=sources,
            messages_to_archive=to_archive,
            retrieved_context_text=retrieved_context_text,
        )

    def _compose_system_prompt(self, config) -> tuple[str, SourceEntry]:
        """Build the system prompt message content and track it as a source.

        Uses the pre-assembled config.system_prompt (set at startup, includes
        always-loaded skill bodies). Per-conversation activated skill bodies
        are delivered as tool results, not injected into the system prompt.
        """
        from .util import estimate_tokens

        text = config.system_prompt
        tokens = estimate_tokens(text)
        entry = SourceEntry(
            source="system_prompt",
            tokens_estimated=tokens,
            items_included=1,
        )
        return text, entry

    async def _compose_memory_context(
        self, ctx, config, user_message: str, mode: ComposerMode,
    ) -> tuple[list[dict], str, list[dict], SourceEntry | None]:
        """Retrieve and format memory context for injection.

        Returns (messages_to_inject, formatted_text, raw_results, source_entry).
        Fail-open: exceptions log a warning and return empty results.
        """
        from .util import estimate_tokens

        skip_modes = {ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT}
        if ctx.skip_memory_context or mode in skip_modes:
            return [], "", [], None

        try:
            from .memory_context import format_memory_context, retrieve_memory_context

            results = await retrieve_memory_context(config, user_message)
            if not results:
                return [], "", [], None

            formatted = format_memory_context(results)
            msg = {"role": "memory_context", "content": formatted}

            # Track recently injected entries
            self.state.recent_memory_ids = [
                r.get("entry_text", "")[:80] for r in results
            ]

            tokens = estimate_tokens(formatted)
            entry = SourceEntry(
                source="memory",
                tokens_estimated=tokens,
                items_included=len(results),
            )
            return [msg], formatted, results, entry

        except Exception:
            log.warning("Memory context composition failed", exc_info=True)
            return [], "", [], None

    def _compose_wiki_context(
        self, ctx, config, user_message: str, history: list, mode: ComposerMode,
    ) -> tuple[list[dict], SourceEntry | None]:
        """Build wiki context messages for referenced/open pages.

        Returns (messages_to_inject, source_entry).
        """
        from .util import estimate_tokens

        skip_modes = {ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT}
        if mode in skip_modes:
            return [], None

        from .agent import _get_already_injected_pages, _parse_wiki_references, _read_wiki_page

        vault_dir = config.vault_root
        if not vault_dir.exists():
            return [], None

        wiki_refs = _parse_wiki_references(user_message, ctx.wiki_page)
        if not wiki_refs:
            return [], None

        already_injected = _get_already_injected_pages(history)
        messages = []
        skipped = 0

        for ref in wiki_refs:
            if ref["page"] in already_injected:
                skipped += 1
                continue
            content = _read_wiki_page(config, ref["page"])
            if content is None:
                text = f"[Wiki page '{ref['page']}' not found]"
            elif ref["source"] == "open_page":
                text = f"[Currently viewing wiki page: {ref['page']}]\n\n{content}"
            else:
                text = f"[Referenced wiki page: {ref['page']}]\n\n{content}"
            messages.append({
                "role": "wiki_context",
                "content": text,
                "wiki_page": ref["page"],
            })

        if not messages and skipped == 0:
            return [], None

        tokens = sum(estimate_tokens(m["content"]) for m in messages)
        entry = SourceEntry(
            source="wiki",
            tokens_estimated=tokens,
            items_included=len(messages),
            items_truncated=skipped,
        )
        return messages, entry

    def _compose_tools(self, ctx, config) -> tuple[list[dict], list[dict], str | None, SourceEntry]:
        """Classify tools into active and deferred sets.

        Returns (active_tools, deferred_tools, deferred_text, source_entry).
        Replicates the logic in agent._build_tool_list() with diagnostics.
        """
        from .agent import _collect_all_tool_defs
        from .tools import TOOL_DEFINITIONS
        from .tools.search_tools import SEARCH_TOOL_DEFINITIONS
        from .tools.tool_registry import (
            build_deferred_list_text,
            classify_tools,
            estimate_tool_tokens,
            get_fetched_tools,
        )
        from .util import estimate_tokens

        all_defs = _collect_all_tool_defs(ctx)
        fetched = get_fetched_tools(ctx)
        active, deferred = classify_tools(all_defs, config, fetched)

        # Apply allowed_tools filter
        allowed = ctx.tools.allowed
        if allowed is not None:
            active = [
                t for t in active
                if t.get("function", {}).get("name") in allowed
            ]

        deferred_text = None
        if deferred:
            ctx.tools.deferred_pool = deferred
            active = active + SEARCH_TOOL_DEFINITIONS
            core_names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}
            deferred_text = build_deferred_list_text(deferred, core_names=core_names)

        tool_tokens = estimate_tool_tokens(active)
        text_tokens = estimate_tokens(deferred_text) if deferred_text else 0
        entry = SourceEntry(
            source="tools",
            tokens_estimated=tool_tokens + text_tokens,
            items_included=len(active),
            items_truncated=len(deferred),
            details={"deferred_mode": bool(deferred)},
        )
        return active, deferred, deferred_text, entry

    def _get_context_window_size(self, config) -> int:
        """Return the effective context window size.

        Prefers the explicit context_window_size if set, otherwise falls back
        to compaction_max_tokens as a conservative proxy.

        Not yet called — will be used by budget allocation in a future phase.
        """
        window = config.llm.context_window_size
        if window and window > 0:
            return window
        return config.compaction.max_tokens

    def record_actuals(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Record actual token usage from the LLM response."""
        self.state.last_prompt_tokens_actual = prompt_tokens
        self.state.last_completion_tokens_actual = completion_tokens
