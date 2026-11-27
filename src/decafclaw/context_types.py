"""Shared context types, composer state, and budget resolution (#1491).

Split out of ``context_composer.py`` so the orchestrator module holds the turn
pipeline rather than every dataclass and threshold helper.

It also owns the canonical turn-mode classification (#1355): ``ComposerMode``,
``TaskMode``, the per-mode traits table, and the derived skip/unattended/
unwatched partitions.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from decafclaw.context import Context


class ComposerMode(enum.Enum):
    """Agent turn mode — controls which context sources are included.

    Callers set the mode via ``ctx.task_mode`` (mapped in ``run_agent_turn``):
    - INTERACTIVE — default for Mattermost and the web UI
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

    @property
    def is_skip(self) -> bool:
        """True when this mode skips interactive-only context sources.

        Use ``SKIP_MODES`` (or this property) instead of re-declaring
        ``{HEARTBEAT, SCHEDULED, CHILD_AGENT}`` at a call site (#1355).
        """
        return self in SKIP_MODES


class TaskMode(enum.Enum):
    """Runtime classification of a turn for unattended/interactive behavior.

    Distinct from ``TurnKind`` (the *origin* of a turn): USER and WORKFLOW
    turns are ``INTERACTIVE``; each task kind maps to its mode via
    ``TurnKind.task_mode_for()`` (conversation_manager.py). The values are the
    wire strings used at transport boundaries (inbox JSONL) — internally,
    always use members. Free-form strings are only parsed at the boundary (see
    ``conversation_manager.parse_task_mode``). See #1381.
    """

    INTERACTIVE = ""  # attended user / workflow turns
    HEARTBEAT = "heartbeat"
    SCHEDULED = "scheduled"
    CHILD_AGENT = "child_agent"
    BACKGROUND_WAKE = "background_wake"


@dataclass(frozen=True)
class TaskModeTraits:
    """What one ``TaskMode`` means for context selection and authorization.

    This table is the single source of the mode/skip classification (#1355).
    Two earlier duplicates carried subtly different membership because they
    answer different questions:

    - ``composer_mode`` drives context injection. Any mode whose composer
      mode is not ``INTERACTIVE`` skips interactive-only sources: memory
      retrieval, vault wiki injection, notes/journal deltas, mentions, preempt
      hints, and resolved-error pruning. See ``SKIP_MODES``.
    - ``unattended`` means no human can answer a confirmation prompt, so
      ``Context.is_unattended`` treats the turn as unsupervised. This is NOT
      the complement of the skip set, and the difference is deliberate:
      ``CHILD_AGENT`` skips context but inherits the parent's
      ``request_confirmation``, so a child of an interactive turn still has
      someone who can answer. ``BACKGROUND_WAKE`` keeps interactive context but
      fires autonomously on the user's conversation, where nobody is watching.
    - ``watched`` means a transport subscriber renders the turn's live
      progress (``text_before_tools``). Heartbeat, scheduled, and child turns
      run on synthetic conversation ids no transport ever subscribed to, so
      only their final tool text reaches anyone (``UNWATCHED_TASK_MODES``).
      A wake turn runs on the user's real conversation, which does have a
      live subscriber.
    """

    composer_mode: ComposerMode
    unattended: bool = False
    watched: bool = True


TASK_MODE_TRAITS: dict[TaskMode, TaskModeTraits] = {
    TaskMode.INTERACTIVE: TaskModeTraits(ComposerMode.INTERACTIVE),
    TaskMode.HEARTBEAT: TaskModeTraits(ComposerMode.HEARTBEAT, unattended=True, watched=False),
    TaskMode.SCHEDULED: TaskModeTraits(ComposerMode.SCHEDULED, unattended=True, watched=False),
    TaskMode.CHILD_AGENT: TaskModeTraits(ComposerMode.CHILD_AGENT, watched=False),
    TaskMode.BACKGROUND_WAKE: TaskModeTraits(ComposerMode.INTERACTIVE, unattended=True),
}

#: Composer modes that skip interactive-only context sources (#1355).
SKIP_MODES: frozenset[ComposerMode] = frozenset(mode for mode in ComposerMode if mode is not ComposerMode.INTERACTIVE)

#: TaskMode forms of the same partitions, derived from ``TASK_MODE_TRAITS``
#: so a new mode cannot land in only some of them.
SKIP_TASK_MODES: frozenset[TaskMode] = frozenset(
    mode for mode, traits in TASK_MODE_TRAITS.items() if traits.composer_mode in SKIP_MODES
)
UNATTENDED_TASK_MODES: frozenset[TaskMode] = frozenset(
    mode for mode, traits in TASK_MODE_TRAITS.items() if traits.unattended
)
UNWATCHED_TASK_MODES: frozenset[TaskMode] = frozenset(
    mode for mode, traits in TASK_MODE_TRAITS.items() if not traits.watched
)


# -- Diagnostics --------------------------------------------------------------


@dataclass
class SourceEntry:
    """Diagnostic entry for a single context source.

    ``slot`` and ``extension`` attribute hook-driven contributions for the
    Context Inspector (#1509): the :class:`decafclaw.extensions.ContextSlot`
    value the messages mounted into and the extension that produced them.
    Sources without a lifecycle slot (system prompt, tools, history) leave
    both empty.
    """

    source: str
    tokens_estimated: int
    items_included: int
    items_truncated: int = 0
    details: dict = field(default_factory=dict)
    slot: str = ""
    extension: str = ""


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
    # Atomic facts from legacy PRE_COMPOSE string hook results, surfaced to
    # the web UI as the turn's recalled-memory disclosure (#1514).
    recalled_facts: list[str] = field(default_factory=list)
    # Passive context provenance contributed by PRE_COMPOSE extensions (#1563).
    # Keyed by source (facts, journals, vault, scratchpad); each value is a
    # list of JSON-ready item dicts the web UI renders in the turn's capsule
    # row.
    provenance: dict[str, list[dict]] = field(default_factory=dict)


# -- Per-conversation state ---------------------------------------------------


@dataclass(frozen=True)
class InjectionCursors:
    """High-water marks for delta injections of notes and journal (#1111).

    Each source keeps the timestamp of the newest entry already surfaced
    plus the texts of the surfaced entries carrying exactly that timestamp.
    Notes have second resolution and journal headers minute resolution, so
    a later entry can share the boundary timestamp; the texts let it
    through instead of being skipped by a strict ``>`` comparison.

    Frozen so ``ContextComposer.compose`` can snapshot and restore the whole
    value when composition fails or is cancelled.
    """

    notes_ts: str = ""
    notes_boundary: frozenset[str] = frozenset()
    journal_ts: datetime | None = None
    journal_boundary: frozenset[str] = frozenset()


def select_unseen_items(items: list, last_ts: Any, boundary: frozenset[str]) -> tuple[list, Any, frozenset[str]]:
    """Return ``(unseen, new_ts, new_boundary)`` for chronological ``items``.

    ``items`` carry ``.timestamp`` and ``.text``. An item is unseen when it
    is newer than ``last_ts``, or shares ``last_ts`` but its text is not in
    ``boundary``. The returned cursor is unchanged when nothing is unseen.
    """
    if not last_ts:
        unseen = list(items)
    else:
        unseen = [i for i in items if i.timestamp > last_ts or (i.timestamp == last_ts and i.text not in boundary)]
    if not unseen:
        return [], last_ts, boundary
    new_ts = unseen[-1].timestamp
    new_boundary = frozenset(i.text for i in unseen if i.timestamp == new_ts)
    if new_ts == last_ts:
        new_boundary |= boundary
    return unseen, new_ts, new_boundary


@dataclass
class ComposerState:
    """State carried between turns for a single conversation.

    Shared across context forks (same conversation). Assumes single-writer:
    only one compose() call at a time per conversation. If delegate_task
    ever composes in parallel for the same conversation, this needs a lock.
    """

    last_sources: list[SourceEntry] = field(default_factory=list)
    last_total_tokens_estimated: int = 0
    # Turn mode for the current compose() call. Set by the composer so
    # lifecycle hook extensions can classify interactive vs unattended turns
    # without re-deriving the mapping from ctx.task_mode.
    mode: ComposerMode = ComposerMode.INTERACTIVE
    # Remaining token budget for dynamic PRE_COMPOSE contributions, computed
    # by compose() from the model window minus fixed costs (#1048, #1489).
    # ``None`` means the composer could not determine a budget.
    dynamic_token_budget: int | None = None
    # Latest vault retrieval output, carried to ComposedContext for
    # reflection and the Context Inspector. Owned by VaultRetrievalExtension.
    last_retrieved_context_text: str = ""
    last_memory_results: list[dict] = field(default_factory=list)
    # Legacy PRE_COMPOSE string results from the latest compose() call, kept
    # for the turn's message_complete payload (#1514). Refilled per compose;
    # a disabled extension cannot leak the previous turn's facts.
    last_recalled_facts: list[str] = field(default_factory=list)
    # Passive context provenance from the latest compose() call (#1563),
    # refilled per compose so a disabled extension cannot leak the previous
    # turn's capsules into the UI.
    last_provenance: dict[str, list[dict]] = field(default_factory=dict)
    last_prompt_tokens_actual: int = 0
    last_completion_tokens_actual: int = 0
    last_cached_prompt_tokens_actual: int = 0  # prompt tokens served from provider cache (#480)
    injected_paths: set[str] = field(default_factory=set)  # file_paths already in context; cleared on compaction
    # High-water marks for delta-injected notes and journal entries
    # (#306, #1111). Persisted across turns; reset on compaction.
    injection_cursors: InjectionCursors = field(default_factory=InjectionCursors)
    # Track the baseline system context to detect mid-conversation changes (#776)
    baseline_system_context: str | None = None
    # Digests only: describe deliberate prefix changes without logging content.
    prefix_fingerprints: dict[str, str] = field(default_factory=dict)
    # Cumulative tool-result clearing stats for this conversation
    # (#298). Reset on compaction since the surviving summary
    # represents a fresh in-memory view.
    cleanup_cleared_count: int = 0
    cleanup_cleared_bytes: int = 0

    def reset_for_compaction(self) -> None:
        """Reset conversation-scoped injection state on compaction (#1111, #1116).

        After compaction, summarized content replaces original history.
        Reset high-water marks and injected paths so the next turn starts
        with a fresh baseline.
        """
        self.injected_paths.clear()
        self.prefix_fingerprints = {}
        self.injection_cursors = InjectionCursors()
        self.cleanup_cleared_count = 0
        self.cleanup_cleared_bytes = 0


def _model_context_window(config, ctx: "Context | None" = None) -> int:
    """Resolve the model context window size (#1099).

    Prefers the explicit ``context_window_size`` on the active model's
    ``model_configs`` entry, then the global ``config.llm.context_window_size``,
    then falls back to the legacy fixed ``compaction.max_tokens`` proxy.
    """
    window = int(getattr(getattr(config, "llm", None), "context_window_size", 0) or 0)
    model_name = (getattr(ctx, "active_model", "") or "") if ctx is not None else ""
    if model_name:
        model_cfgs = getattr(config, "model_configs", None) or {}
        mc = model_cfgs.get(model_name) if hasattr(model_cfgs, "get") else None
        mc_window = int(getattr(mc, "context_window_size", 0) or 0) if mc is not None else 0
        if mc_window > 0:
            window = mc_window
    return window


def effective_compaction_threshold(config, ctx: "Context | None" = None, *, lowered: int | None = None) -> int:
    """Resolve the effective auto-compaction token threshold (#1099).

    Precedence (highest first):

      1. ``lowered`` — turn-local reactive lowering applied by the agent
         loop after a context-length-exceeded error. A safety valve for the
         turn that just overflowed; it beats user- and config-set thresholds
         so the runaway turn can compact its way out.
      2. Per-conversation absolute override — ``ctx.compaction_threshold_override``
         (persisted as ``PersistedTurnState.compaction_threshold``).
      3. Per-conversation ratio override — ``ctx.compaction_threshold_ratio_override``
         (persisted as ``PersistedTurnState.compaction_threshold_ratio``) applied
         to the resolved model context window, else the global
         ``config.compaction.threshold_ratio``.
      4. Fixed ``config.compaction.max_tokens`` fallback.

    ``threshold_ratio`` of ``None`` keeps the historical fixed-cap behaviour.
    When set and the resolved model context window is known (``context_window_size
    > 0``), the effective threshold becomes ``int(context_window * ratio)``; an
    unknown window falls back to the fixed cap.
    """
    threshold = config.compaction.max_tokens
    if lowered is not None and lowered > 0:
        return int(lowered)
    override = getattr(ctx, "compaction_threshold_override", None) if ctx is not None else None
    if override:
        return int(override)
    ratio = None
    if ctx is not None:
        per_conv_ratio = getattr(ctx, "compaction_threshold_ratio_override", None)
        if per_conv_ratio:
            ratio = float(per_conv_ratio)
    if ratio is None:
        ratio = config.compaction.threshold_ratio
    if ratio is None:
        return threshold
    window = _model_context_window(config, ctx)
    if window > 0:
        return int(window * float(ratio))
    return threshold


ROLE_REMAP: dict[str, str] = {
    "vault_retrieval": "user",
    "vault_references": "user",
    "workspace_references": "user",
    "mcp_references": "user",
    "conversation_notes": "user",
    "recent_journal": "user",
    "working_memory": "user",
    "cancel_marker": "user",
    "turn_aborted": "user",
    "skill_hint": "user",
    "context_status": "user",
    "recalled_facts": "user",
}
