"""Context composer — unified context assembly for agent turns.

Owns the entire pipeline for building what gets sent to the LLM each turn:
system prompt, conversation history, memory/wiki context, tool definitions.
Tracks per-turn diagnostics (what was included, token estimates, actuals).
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


# -- Enums --------------------------------------------------------------------


class ComposerMode(enum.Enum):
    """Agent turn mode — controls which context sources are included.

    Callers set the mode via ``ctx.task_mode`` (mapped in ``run_agent_turn``):
    - INTERACTIVE — default for Mattermost, web UI, terminal
    - HEARTBEAT — periodic heartbeat tasks (skips memory + wiki)
    - SCHEDULED — cron-style scheduled tasks (skips memory + wiki)
    - CHILD_AGENT — delegate_task sub-agents (skips memory + wiki)

    ``skip_vault_retrieval`` on the context is an independent flag that
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
    memory_results: list[dict] = field(default_factory=list)


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
    injected_paths: set[str] = field(default_factory=set)  # file_paths already in context; cleared on compaction


# -- Sidecar persistence ------------------------------------------------------


def _context_sidecar_path(config, conv_id: str) -> Path:
    """Path to the context diagnostics sidecar file."""
    base_dir = (config.workspace_path / "conversations").resolve()
    safe_name = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    if not safe_name:
        return base_dir / "_invalid.context.json"
    path = (base_dir / f"{safe_name}.context.json").resolve()
    if not path.is_relative_to(base_dir):
        return base_dir / "_invalid.context.json"
    return path


def write_context_sidecar(config, conv_id: str, diagnostics: dict) -> None:
    """Write context diagnostics to the sidecar file. Fail-open."""
    try:
        path = _context_sidecar_path(config, conv_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(diagnostics, indent=2, default=str))
    except Exception:
        log.warning("Failed to write context sidecar for %s", conv_id, exc_info=True)


def read_context_sidecar(config, conv_id: str) -> dict | None:
    """Read context diagnostics from the sidecar file. Returns None if missing."""
    try:
        path = _context_sidecar_path(config, conv_id)
        if not path.exists():
            return None
        return json.loads(path.read_text())
    except Exception:
        log.warning("Failed to read context sidecar for %s", conv_id, exc_info=True)
        return None


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
        # Explicit @[[Page]] references are fixed costs — always included.
        wiki_msgs, wiki_entry = self._compose_vault_references(
            ctx, config, user_message, history, mode,
        )
        for wm in wiki_msgs:
            history.append(wm)
            to_archive.append(wm)
            await ctx.publish("vault_references", text=wm["content"], page=wm.get("wiki_page"))
        if wiki_entry:
            sources.append(wiki_entry)

        # -- Tools (compute before budget so we have actual token cost) --
        active_tools, deferred_tools, deferred_text, tools_entry = self._compose_tools(ctx, config)
        sources.append(tools_entry)

        # -- Compute fixed costs for dynamic budget allocation --
        # Fixed costs: system prompt + wiki refs + tools + existing history
        fixed_tokens = system_entry.tokens_estimated
        if wiki_entry:
            fixed_tokens += wiki_entry.tokens_estimated
        # Estimate existing history (before this turn's additions)
        # Only exclude wiki messages injected THIS turn (wiki_msgs);
        # prior turns' memory/wiki are already in history and sent to the LLM.
        injected_wiki_ids = {id(wm) for wm in wiki_msgs}
        existing_history_tokens = sum(
            estimate_tokens(str(m.get("content", "")))
            for m in history
            if id(m) not in injected_wiki_ids
        )
        fixed_tokens += existing_history_tokens
        # User message
        fixed_tokens += estimate_tokens(user_message)
        # Tools (actual token cost, not estimate)
        fixed_tokens += tools_entry.tokens_estimated

        # Response reserve (leave room for the model's response)
        response_reserve = 4096

        # Dynamic budget for scored candidates
        window_size = self._get_context_window_size(config)
        remaining_budget = max(0, window_size - fixed_tokens - response_reserve)

        # Fall back to fixed max_tokens if remaining budget is unreasonable
        # (e.g. context_window_size not configured)
        memory_budget: int | None = None
        if remaining_budget > 0:
            memory_budget = remaining_budget
        # else: None → _compose_vault_retrieval falls back to max_tokens

        # -- Memory context (injected before user message in history) --
        memory_msgs, retrieved_context_text, mc_results, memory_entry = await self._compose_vault_retrieval(
            ctx, config, user_message, mode,
            token_budget=memory_budget,
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
        role_remap = {"vault_retrieval": "user", "vault_references": "user"}
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

        # -- Assemble final messages --
        messages = [{"role": "system", "content": system_text}]
        if deferred_text:
            messages.append({"role": "system", "content": deferred_text})
        messages.extend(llm_history)

        # -- Publish memory context event (after user message for UI ordering) --
        if mc_results and config.vault_retrieval.show_in_ui:
            await ctx.publish("vault_retrieval",
                              text=retrieved_context_text,
                              results=mc_results)

        # -- Total token estimate --
        total_tokens = sum(s.tokens_estimated for s in sources)

        # -- Context status note (for agent self-regulation) --
        if config.agent.show_context_status:
            effective_window = self._get_context_window_size(config)
            status_line = self._build_context_status(
                total_tokens, effective_window, history_msg_count,
            )
            if status_line:
                messages.append({"role": "system", "content": status_line})

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
            memory_results=mc_results,
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

    def _score_candidates(self, candidates: list[dict], config) -> list[dict]:
        """Score retrieval candidates using recency + importance + similarity.

        Each candidate gets a composite_score field. Returns sorted descending.
        """
        from datetime import datetime

        relevance = config.relevance
        now = datetime.now()

        for c in candidates:
            similarity = min(1.0, max(0.0, c.get("similarity", 0.0)))

            # Recency: exponential decay based on hours since modification
            modified_at = c.get("modified_at", "")
            if modified_at:
                try:
                    mod_time = datetime.fromisoformat(modified_at)
                    hours = max(0.0, (now - mod_time).total_seconds() / 3600)
                    recency = relevance.recency_decay_rate ** hours
                    recency = min(1.0, max(0.0, recency))
                except (ValueError, TypeError):
                    recency = 0.5
            else:
                recency = 0.5

            importance = min(1.0, max(0.0, c.get("importance", 0.5)))

            c["recency"] = recency
            c["composite_score"] = (
                relevance.w_similarity * similarity
                + relevance.w_recency * recency
                + relevance.w_importance * importance
            )

        candidates.sort(key=lambda c: c.get("composite_score", 0), reverse=True)
        return candidates

    async def _compose_vault_retrieval(
        self, ctx, config, user_message: str, mode: ComposerMode,
        token_budget: int | None = None,
    ) -> tuple[list[dict], str, list[dict], SourceEntry | None]:
        """Retrieve and format memory context for injection.

        Args:
            token_budget: Dynamic budget from composer. If None, falls back
                to config.vault_retrieval.max_tokens for backward compatibility.

        Returns (messages_to_inject, formatted_text, raw_results, source_entry).
        Retrieval candidates are scored by composite relevance and selected
        by score order within the token budget.
        Fail-open: exceptions log a warning and return empty results.
        """
        from .util import estimate_tokens

        skip_modes = {ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT}
        if ctx.skip_vault_retrieval or mode in skip_modes:
            return [], "", [], None

        try:
            from .memory_context import format_memory_context, retrieve_memory_context

            results = await retrieve_memory_context(config, user_message)
            if not results:
                return [], "", [], None

            # Filter out candidates already injected in this conversation
            # (they're already in history — re-injecting wastes tokens)
            if self.state.injected_paths:
                before = len(results)
                results = [r for r in results if r.get("file_path", "") not in self.state.injected_paths]
                suppressed = before - len(results)
                if suppressed:
                    log.debug("Memory context: suppressed %d already-injected candidates", suppressed)

            if not results:
                return [], "", [], None

            # Score and rank candidates
            total_candidates = len(results)
            results = self._score_candidates(results, config)

            # Drop candidates below minimum score threshold
            score_threshold = config.relevance.min_composite_score
            results = [r for r in results if r.get("composite_score", 0) >= score_threshold]

            # Select by token budget (score order replaces similarity order)
            budget = token_budget if token_budget is not None else config.vault_retrieval.max_tokens
            log.debug("Memory context: %d candidates, budget=%d tokens (%s)",
                      total_candidates, budget,
                      "dynamic" if token_budget is not None else "fixed")
            from .memory_context import _trim_to_token_budget
            results = _trim_to_token_budget(results, budget)
            if results:
                log.debug("Memory context: selected %d/%d candidates (scores %.3f–%.3f)",
                          len(results), total_candidates,
                          results[0].get("composite_score", 0),
                          results[-1].get("composite_score", 0))

            # Compute per-candidate token estimates for diagnostics
            for r in results:
                r["tokens_estimated"] = estimate_tokens(r.get("entry_text", ""))

            formatted = format_memory_context(results)
            msg = {"role": "vault_retrieval", "content": formatted}

            # Track injected paths so they're suppressed in future turns
            # (cleared on compaction when the content is summarized away)
            for r in results:
                path = r.get("file_path", "")
                if path:
                    self.state.injected_paths.add(path)

            tokens = estimate_tokens(formatted)
            top_score = results[0].get("composite_score", 0) if results else 0
            min_score = results[-1].get("composite_score", 0) if results else 0
            entry = SourceEntry(
                source="memory",
                tokens_estimated=tokens,
                items_included=len(results),
                items_truncated=total_candidates - len(results),
                details={
                    "top_score": round(top_score, 3),
                    "min_score": round(min_score, 3),
                    "candidates_considered": total_candidates,
                    "token_budget": budget,
                    "budget_source": "dynamic" if token_budget is not None else "fixed",
                },
            )
            return [msg], formatted, results, entry

        except Exception:
            log.warning("Memory context composition failed", exc_info=True)
            return [], "", [], None

    def _compose_vault_references(
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
                "role": "vault_references",
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

        Used by compose() for dynamic budget allocation.
        """
        window = config.llm.context_window_size
        if window and window > 0:
            return window
        return config.compaction.max_tokens

    @staticmethod
    def _build_context_status(
        total_tokens: int, context_window: int, message_count: int,
    ) -> str | None:
        """Build a one-line context usage note for the agent.

        Returns None if context_window is zero (unconfigured).
        """
        if context_window <= 0:
            return None
        pct = total_tokens / context_window * 100
        hint = ""
        if pct > 70:
            hint = " — consider being concise"
        return (
            f"[Context: ~{total_tokens:,} / {context_window:,} tokens"
            f" ({pct:.0f}%), {message_count} messages{hint}]"
        )

    def record_actuals(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Record actual token usage from the LLM response."""
        self.state.last_prompt_tokens_actual = prompt_tokens
        self.state.last_completion_tokens_actual = completion_tokens

    def build_diagnostics(self, config, composed: ComposedContext) -> dict:
        """Build the full diagnostics dict for the context sidecar file."""
        from datetime import datetime, timezone

        candidates = []
        for r in composed.memory_results:
            entry = {
                "file_path": r.get("file_path", ""),
                "source_type": r.get("source_type", ""),
                "composite_score": round(r.get("composite_score", 0), 3),
                "similarity": round(r.get("similarity", 0), 3),
                "recency": round(r.get("recency", 0.5), 3),
                "importance": round(r.get("importance", 0.5), 3),
                "modified_at": r.get("modified_at", ""),
                "tokens_estimated": r.get("tokens_estimated", 0),
            }
            if r.get("linked_from"):
                entry["linked_from"] = r["linked_from"]
            candidates.append(entry)

        sources = []
        for s in composed.sources:
            sources.append({
                "source": s.source,
                "tokens_estimated": s.tokens_estimated,
                "items_included": s.items_included,
                "items_truncated": s.items_truncated,
                "details": s.details,
            })

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_tokens_estimated": composed.total_tokens_estimated,
            "total_tokens_actual": self.state.last_prompt_tokens_actual,
            "context_window_size": self._get_context_window_size(config),
            "compaction_threshold": config.compaction.max_tokens,
            "sources": sources,
            "memory_candidates": candidates,
        }
