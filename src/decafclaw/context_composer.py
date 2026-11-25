"""Context composer — unified context assembly for agent turns.

Owns the entire pipeline for building what gets sent to the LLM each turn:
system prompt, conversation history, memory/wiki context, tool definitions.
Tracks per-turn diagnostics (what was included, token estimates, actuals).
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import html
import inspect
import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from decafclaw.skills.background.tools import format_status_text

from .archive import LLM_ROLES
from .attachments import resolve_attachments
from .conversation_paths import sidecar_path
from .image_budget import apply_image_budget
from .preempt_search import match_tools, tokenize
from .telemetry import get_tracer
from .tool_definitions import (
    build_tool_list,
    collect_all_tool_defs,
    refresh_dynamic_tools,
)
from .tools.tool_registry import (
    estimate_tool_tokens,
    get_critical_names,
    get_fetched_tools,
)
from .util import estimate_tokens

if TYPE_CHECKING:
    from decafclaw.context import Context

from .context_diagnostics import (
    build_context_status,
    build_diagnostics,
    get_context_window_size,
    get_effective_compaction_threshold,
)
from .context_messages import _expand_background_event, _reorder_tool_results
from .context_pipeline import (
    account_history_tokens,
    assemble_provider_history,
    compose_pre_compose_hooks,
    compose_system_prompt,
    compute_fixed_tokens,
    merge_provenance,
    working_memory_envelope,
)
from .context_sidecars import read_context_sidecar, read_last_request_sidecar, write_context_sidecar
from .context_tools import (
    compose_tools,
    record_prefix,
    sync_tool_messages,
    update_iteration_tools,
)
from .context_types import (
    ROLE_REMAP,
    ComposedContext,
    ComposerMode,
    ComposerState,
    InjectionCursors,
    SourceEntry,
    effective_compaction_threshold,
    select_unseen_items,
)
from .events import TurnLifecycle
from .extensions import ContextContribution, ContextSlot

log = logging.getLogger(__name__)

# Roles that are remapped to "user" before sending to the LLM.
# These messages are auto-injected by the composer per-turn, then
# archived under their internal role for accounting and dedupe.


# Ceiling for the never-archived <activated_skills> block. Exceeding it means
# too many skills are active at once; warn loudly rather than truncate,
# because dropping a body while its tools stay callable leaves the model with
# tools but not the instructions governing them (#1823).
ACTIVATED_SKILLS_WARN_TOKENS = 20_000


def render_activated_skills(ctx: "Context") -> tuple[dict, int, int] | None:
    """Render activated skill bodies as one transient <activated_skills> block.

    Never archived and never replayed (#1823): the block re-renders from
    ``ctx.skills.activated`` each turn, so the conversation prefix through the
    previous assistant turn stays cacheable and compaction never has to
    preserve a skill-activation turn to keep the instructions alive.

    Returns ``(message, tokens, skill_count)``, or None when nothing is active.
    """
    activated = getattr(ctx.skills, "activated", None) or {}
    if not activated:
        return None

    from .skills import find_skill, index_skills, substitute_skill_placeholders

    index = index_skills(ctx.config.discovered_skills)
    blocks: list[str] = []
    costs: list[tuple[str, int]] = []
    for name in activated:
        info = find_skill(index, name)
        if info is None:
            # Activated then removed from disk: there is no body left to
            # render, though its tools may still be callable this turn.
            continue
        if info.always_loaded:
            # Always-loaded bodies already live in the system prompt's
            # <loaded_skills> section; rendering them again here would
            # duplicate the body on every turn.
            continue
        body = substitute_skill_placeholders(info.body, skill_dir=str(info.location.resolve()))
        if not body.strip():
            continue
        cost = estimate_tokens(body)
        costs.append((name, cost))
        blocks.append(f'<skill name="{html.escape(name, quote=True)}">\n{body}\n</skill>')

    if not blocks:
        return None

    body_total = sum(cost for _, cost in costs)
    if body_total > ACTIVATED_SKILLS_WARN_TOKENS:
        log.warning(
            "Activated skills block is ~%d tokens (ceiling %d): %s. Bodies are "
            "not truncated because their tools remain callable.",
            body_total,
            ACTIVATED_SKILLS_WARN_TOKENS,
            ", ".join(f"{name} (~{cost} tokens)" for name, cost in costs),
        )

    content = "<activated_skills>\n" + "\n".join(blocks) + "\n</activated_skills>"
    return {"role": "user", "content": content}, estimate_tokens(content), len(blocks)


def _now() -> datetime:
    """Current naive local time — a seam so tests can patch the clock.

    Naive/local matches how ``vault_journal_append`` timestamps entries
    (``datetime.now()``), so the recency window compares apples to apples.
    """
    return datetime.now()


# -- Enums --------------------------------------------------------------------


# -- Composer -----------------------------------------------------------------


class ContextComposer:
    _tracer = get_tracer(__name__)

    """Assembles the complete context for each agent turn.

    Stateful per-conversation: tracks what was included and actual token usage
    across turns for diagnostics and future budget optimization.
    """

    def __init__(self, state: ComposerState | None = None):
        self.state = state or ComposerState()

    async def compose(
        self,
        ctx: "Context",
        user_message: str,
        history: list,
        *,
        mode: ComposerMode = ComposerMode.INTERACTIVE,
        attachments: list[dict] | None = None,
    ) -> ComposedContext:
        with self._tracer.start_as_current_span("ContextComposer.compose") as span:
            span.set_attribute("conversation.id", ctx.conv_id)
            span.set_attribute("composer.mode", str(mode))
            # Lifecycle hook extensions read the mode off the shared composer
            # state; compose() is the single owner of the classification.
            ctx.composer.mode = mode
            # Injection helpers advance the cursors before the injected
            # messages reach history and the archive. If composition fails or
            # is cancelled after that, roll the cursors back so the next turn
            # re-surfaces what was never delivered. Proactive retrieval also
            # records suppression paths before compose() returns, so restore
            # that set too; otherwise a page that never reached the prompt is
            # suppressed on later turns.
            cursors_before = ctx.composer.injection_cursors
            injected_before = set(ctx.composer.injected_paths)
            try:
                return await self._compose_impl(ctx, user_message, history, mode=mode, attachments=attachments)
            except BaseException:
                ctx.composer.injection_cursors = cursors_before
                ctx.composer.injected_paths = injected_before
                raise

    async def _compose_impl(
        self,
        ctx: "Context",
        user_message: str,
        history: list,
        *,
        mode: ComposerMode = ComposerMode.INTERACTIVE,
        attachments: list[dict] | None = None,
    ) -> ComposedContext:
        """Assemble the complete context for this turn.

        Orchestrates all context sources. Token accounting treats ``history``
        as read-only (archived prior turns); this turn's wiki / notes / memory
        / user-message injections are appended to ``history`` once, at the end,
        before returning. The caller's continued reads of ``history`` after
        compose() returns see the post-turn state.
        Does NOT archive — the caller is responsible for persisting messages
        via the messages_to_archive list.
        """
        config = ctx.config
        from .llm import ensure_model_context_window

        await ensure_model_context_window(config, getattr(ctx, "active_model", ""))
        if config.agent.auto_refresh_skills:
            from .tools.skill_tools import rediscover_skills

            old_prompt = config.system_prompt
            await asyncio.to_thread(rediscover_skills, config)
            if config.system_prompt != old_prompt:
                log.debug("Auto-refreshed skills: catalog text changed")
            else:
                log.debug("Auto-refreshed skills: catalog text unchanged")
        sources: list[SourceEntry] = []
        to_archive: list[dict] = []

        # -- Truncate oversized user messages --
        max_len = config.agent.max_message_length
        if max_len and len(user_message) > max_len:
            original_len = len(user_message)
            user_message = (
                user_message[:max_len] + f"\n\n[truncated at {max_len:,} chars, original was {original_len:,}]"
            )
            log.warning(f"User message truncated: {original_len:,} -> {max_len:,} chars")

        user_msg: dict = {"role": "user", "content": user_message}
        if attachments:
            user_msg["attachments"] = attachments

        # -- System prompt --
        system_text, system_entry = compose_system_prompt(config)
        sources.append(system_entry)

        # -- Detect mid-conversation system updates (#776) --
        system_update_msg: dict | None = None
        if self.state.baseline_system_context is None:
            self.state.baseline_system_context = config.system_prompt
        elif self.state.baseline_system_context != config.system_prompt:
            self.state.baseline_system_context = config.system_prompt
            system_update_msg = {
                "role": "user",
                "source": "system_update",
                "content": "[System Update: The system context or available tools have changed.]",
            }

        # -- Tools (compute before budget so we have actual token cost) --
        # Pre-emptive discovery hints run in PRE_COMPOSE after classification,
        # so deferred matches are known when the hints render (#1507).
        active_tools, deferred_tools, deferred_text, guidance_text, tools_entry = self._compose_tools(ctx)
        sources.append(tools_entry)

        # -- Compute fixed costs for dynamic budget allocation --
        # Archived prior-turn content only: fresh injections are appended after
        # all token accounting. Counts match the LLM-visible message list.
        history_tokens, history_msg_count = account_history_tokens(history)
        # User message — has its own SourceEntry so the diagnostics
        # waffle chart and [Context: ...] status line account for the
        # current-turn user content alongside archived history.
        user_msg_tokens = estimate_tokens(user_message)
        user_msg_entry = SourceEntry(
            source="user_message",
            tokens_estimated=user_msg_tokens,
            items_included=1,
        )
        sources.append(user_msg_entry)

        # Contribution owners refill these each turn. Clear them first so a
        # disabled extension cannot leak the previous turn's retrieval into
        # reflection, the Context Inspector, or a fixed-cost hook.
        ctx.composer.dynamic_token_budget = None
        ctx.composer.last_retrieved_context_text = ""
        ctx.composer.last_memory_results = []
        ctx.composer.last_recalled_facts = []
        ctx.composer.last_provenance = {}
        ctx.composer.injected_paths.clear()

        # -- PRE_COMPOSE fixed-cost wave (#1454, #1486, #1507, #1574) --
        # HEAD messages mount outside the archived history (see below); DELTA
        # and TAIL messages are archived with the rest of this turn's
        # injections; EPILOGUE messages mount after the user message and are
        # archived there; WORKING_MEMORY messages are bundled into one
        # transient envelope that is never archived. Fixed-slot contributions
        # (mentions, references, guide, hints, notes, journal) run first so
        # their tokens are charged before the dynamic budget is published.
        (
            head_msgs,
            delta_msgs,
            tail_hook_msgs,
            epilogue_msgs,
            working_memory_msgs,
            hook_sources,
            recalled_facts,
            provenance,
        ) = await compose_pre_compose_hooks(
            ctx,
            config,
            user_message,
            history,
            budget_consumers=False,
        )
        sources.extend(hook_sources)

        # -- Fixed-token phase (#1365) --
        # Charges everything mounted so far — system prompt, tools, the user
        # message, and the fixed PRE_COMPOSE contributions — plus archived
        # history. Mentions in particular arrive as a TAIL contribution, so
        # charging them here keeps the dynamic pool from over-filling a tight
        # context window.
        fixed_tokens = self._compute_fixed_tokens(sources, history_tokens)

        # Response reserve (leave room for the model's response)
        response_reserve = 4096

        # Dynamic budget for scored candidates with an explicit ceiling (#1048)
        window_size = self._get_context_window_size(config, ctx)
        ceiling = config.vault_retrieval.max_tokens
        if window_size > 0:
            remaining_budget = max(0, window_size - fixed_tokens - response_reserve)
            if ceiling and ceiling > 0:
                memory_budget = min(remaining_budget, ceiling)
            else:
                memory_budget = remaining_budget
        else:
            # Fall back to fixed max_tokens if context_window_size is unconfigured
            memory_budget = ceiling

        # PRE_COMPOSE extensions that own a dynamic pool (vault retrieval,
        # #1489) trim to this budget; core owns fixed-cost accounting.
        ctx.composer.dynamic_token_budget = memory_budget

        # -- PRE_COMPOSE dynamic-pool wave (#1365) --
        # Budget consumers run only after the fixed-cost phase publishes the
        # budget, so mention/reference tokens are already reserved.
        (
            dynamic_head,
            dynamic_delta,
            dynamic_tail,
            dynamic_epilogue,
            dynamic_working_memory,
            dynamic_sources,
            dynamic_facts,
            dynamic_provenance,
        ) = await compose_pre_compose_hooks(
            ctx,
            config,
            user_message,
            history,
            budget_consumers=True,
        )
        head_msgs.extend(dynamic_head)
        delta_msgs.extend(dynamic_delta)
        tail_hook_msgs.extend(dynamic_tail)
        epilogue_msgs.extend(dynamic_epilogue)
        working_memory_msgs.extend(dynamic_working_memory)
        sources.extend(dynamic_sources)
        recalled_facts.extend(dynamic_facts)
        merge_provenance(dynamic_provenance, provenance)

        ctx.composer.last_recalled_facts = list(recalled_facts)
        ctx.composer.last_provenance = provenance
        for hm in (*delta_msgs, *tail_hook_msgs):
            to_archive.append(hm)

        # -- User message (archived; appended to history at the end) --
        # The archive row is a copy so provenance metadata can attach to it
        # without ever reaching the provider (#1574).
        if system_update_msg:
            to_archive.append(system_update_msg)
        archived_user_msg = dict(user_msg)
        to_archive.append(archived_user_msg)

        # -- Build LLM messages (filter + remap roles) --
        # Combine archived history with this turn's injections in the same
        # order they'll be archived: prior history → PRE_COMPOSE DELTA →
        # PRE_COMPOSE TAIL (references) → system update → user message. The
        # combined list is what the LLM sees. DELTA mounts after archived
        # history and before tail envelopes so append-only contributions land
        # chronologically before per-turn dynamic context. WORKING_MEMORY is
        # not part of this list because it is not archived; it mounts after
        # the user message below.
        combined = [
            *history,
            *delta_msgs,
            *tail_hook_msgs,
        ]
        if system_update_msg:
            combined.append(system_update_msg)
        combined.append(user_msg)
        # Filter/remap roles, reattach tool results, resolve attachments, and
        # clamp images to the provider budget (#1143).
        llm_history, image_report = assemble_provider_history(combined, config, ctx, active_tools)

        # -- History source entry --
        # history_tokens / history_msg_count were computed above (budget side);
        # reuse them so budget and diagnostics are a single source of truth.
        history_entry = SourceEntry(
            source="history",
            tokens_estimated=history_tokens,  # computed earlier, single source of truth
            items_included=history_msg_count,
            details={
                "total_llm_messages": len(llm_history),
                "image_budget": {
                    "images_included": image_report.images_included,
                    "images_omitted": image_report.images_omitted,
                    "inline_bytes": image_report.inline_bytes,
                    "other_bytes": image_report.other_bytes,
                    "max_images": image_report.max_images,
                    "max_request_bytes": image_report.max_request_bytes,
                    "provider_type": image_report.provider_type,
                    "vision_unsupported": image_report.vision_unsupported,
                },
            },
        )
        sources.append(history_entry)

        # -- Assemble final messages --
        messages = [{"role": "system", "content": system_text}]
        if deferred_text:
            messages.append({"role": "system", "content": deferred_text})
        if guidance_text:
            messages.append({"role": "system", "content": guidance_text})

        # -- PRE_COMPOSE HEAD contributions (#1486) --
        # Mount after the system-message block and before history. HEAD is
        # deliberately NOT archived: a static contribution replays at this
        # same offset every turn, so the provider's prefix cache sees an
        # unchanged system+HEAD prefix (#1117). Archiving it would inject a
        # fresh copy into growing history each turn and bust the prefix.
        for hm in head_msgs:
            role = hm.get("role")
            if role in LLM_ROLES:
                messages.append(hm)
            elif role in ROLE_REMAP:
                messages.append({**hm, "role": ROLE_REMAP[role]})
            else:
                log.warning("PRE_COMPOSE HEAD message has unsupported role %r; skipping", role)

        messages.extend(llm_history)

        # -- Activated skill bodies (#1823) --
        # Mounted at the generation boundary and never archived. The block
        # re-renders from ctx.skills.activated each turn, so the conversation
        # prefix through the previous assistant turn stays cacheable and
        # compaction need not preserve activation turns.
        activated_block = render_activated_skills(ctx)
        if activated_block is not None:
            activated_msg, activated_tokens, activated_count = activated_block
            messages.append(activated_msg)
            sources.append(
                SourceEntry(
                    source="activated_skills",
                    tokens_estimated=activated_tokens,
                    items_included=activated_count,
                )
            )

        # -- PRE_COMPOSE EPILOGUE contributions (#1507) --
        # Mount after the user message, at the immediate generation boundary.
        # They are archived at this position, so later turns replay them where
        # they were sent and the cached provider prefix holds (#1116, #1117).
        # The internal injection roles (such as skill_hint) remap to "user";
        # an unsupported role is archived but left out of the provider request.
        for em in epilogue_msgs:
            role = em.get("role")
            if role in LLM_ROLES:
                messages.append(em)
            elif role in ROLE_REMAP:
                messages.append({**em, "role": ROLE_REMAP[role]})
            else:
                log.warning("PRE_COMPOSE EPILOGUE message has unsupported role %r; skipping", role)
            to_archive.append(em)

        # -- Total token estimate --
        total_tokens = sum(s.tokens_estimated for s in sources)

        # -- Context status note (for agent self-regulation) --
        # It rides in the ephemeral working-memory envelope: the line changes
        # every turn and would otherwise replay as a stale number in history.
        if config.agent.show_context_status:
            effective_window = self._get_context_window_size(config, ctx)
            status_line = self._build_context_status(
                total_tokens,
                effective_window,
                history_msg_count,
            )
            if status_line:
                working_memory_msgs.append({"role": "context_status", "content": status_line})

        # -- PRE_COMPOSE WORKING_MEMORY bundle (#1574) --
        # One transient envelope after the EPILOGUE hints, at the true
        # generation boundary. It is never archived and never replayed: the
        # conversation prefix through the previous assistant turn stays
        # cacheable, and compaction never has to summarize per-turn retrieval
        # that was already ephemeral.
        working_memory_msg = working_memory_envelope(working_memory_msgs)
        if working_memory_msg is not None:
            messages.append({**working_memory_msg, "role": ROLE_REMAP["working_memory"]})

        # -- Provenance references on the archived user row (#1574) --
        # Stashes surfaced paths and source token estimates on the archived
        # user row for turn auditing and Context Inspector inspection. Under
        # prompt-residency deduplication (#1574), ephemeral blocks evicted on
        # prior turns do not suppress future turns. The live prompt keeps the
        # plain user message; assemble_provider_history strips the key.
        provenance_refs: dict = {}
        vault_paths = [r.get("file_path", "") for r in ctx.composer.last_memory_results if r.get("file_path")]
        if vault_paths:
            provenance_refs["vault_paths"] = vault_paths
        if working_memory_msgs:
            provenance_refs["sources"] = [
                {"role": m.get("role"), "tokens": estimate_tokens(str(m.get("content") or ""))}
                for m in working_memory_msgs
            ]
        if provenance_refs:
            archived_user_msg["working_memory"] = provenance_refs

        # -- Update state --
        self.state.last_sources = sources
        self.state.last_total_tokens_estimated = total_tokens

        # Preserve the load-bearing side effect: caller (agent.py) continues
        # to read self.history after compose() returns. Append this turn's
        # injections in the same order as `combined` above. Working memory is
        # deliberately excluded: it is this turn's prompt only, not history.
        history.extend(combined[len(history) :])
        history.extend(epilogue_msgs)

        return ComposedContext(
            messages=messages,
            tools=active_tools,
            deferred_tools=deferred_tools,
            total_tokens_estimated=total_tokens,
            sources=sources,
            messages_to_archive=to_archive,
            retrieved_context_text=ctx.composer.last_retrieved_context_text,
            memory_results=list(ctx.composer.last_memory_results),
            recalled_facts=list(ctx.composer.last_recalled_facts),
            provenance=dict(ctx.composer.last_provenance),
        )

    def _compose_tools(self, ctx) -> tuple[list[dict], list[dict], str | None, str | None, SourceEntry]:
        return compose_tools(ctx)

    def update_iteration_tools(self, ctx: "Context", messages: list[dict]) -> list[dict]:
        return update_iteration_tools(ctx, messages, self.state)

    @staticmethod
    def sync_tool_messages(messages: list[dict], deferred_text: str | None, guidance_text: str | None) -> None:
        sync_tool_messages(messages, deferred_text, guidance_text)

    def _record_prefix(self, ctx: "Context", messages: list[dict], tools: list[dict]) -> None:
        record_prefix(ctx, messages, tools, self.state)

    def get_effective_compaction_threshold(
        self, config, ctx: "Context | None" = None, *, lowered: int | None = None
    ) -> int:
        return get_effective_compaction_threshold(config, ctx, lowered=lowered)

    def _get_context_window_size(self, config, ctx: Any = None) -> int:
        return get_context_window_size(config, ctx)

    @staticmethod
    def _build_context_status(total_tokens: int, context_window: int, message_count: int) -> str | None:
        return build_context_status(total_tokens, context_window, message_count)

    def record_actuals(self, prompt_tokens: int, completion_tokens: int, cached_prompt_tokens: int = 0) -> None:
        self.state.last_prompt_tokens_actual = prompt_tokens
        self.state.last_completion_tokens_actual = completion_tokens
        self.state.last_cached_prompt_tokens_actual = cached_prompt_tokens

    def build_diagnostics(self, config, composed: ComposedContext, ctx: "Context | None" = None) -> dict:
        return build_diagnostics(config, self.state, composed, ctx)

    def _compose_system_prompt(self, config) -> tuple[str, SourceEntry]:
        return compose_system_prompt(config)

    @staticmethod
    def _compute_fixed_tokens(sources: list[SourceEntry], history_tokens: int) -> int:
        return compute_fixed_tokens(sources, history_tokens)

    async def _compose_pre_compose_hooks(
        self,
        ctx: "Context",
        config: Any,
        user_message: str,
        history: list,
        *,
        budget_consumers: bool | None = None,
    ) -> tuple[
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[dict],
        list[SourceEntry],
        list[str],
        dict[str, list[dict]],
    ]:
        return await compose_pre_compose_hooks(ctx, config, user_message, history, budget_consumers=budget_consumers)
