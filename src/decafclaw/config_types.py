"""Configuration sub-dataclasses grouped by concern.

Each dataclass represents a config group (llm, mattermost, etc.).
Field metadata supports:
  - secret: True — masked in `config show` unless --reveal
  - env_alias: "NAME" — alternative env var (checked after systematic name)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields
from enum import StrEnum
from typing import Any

# Provider type constants (llm.types imports only stdlib, so this is cycle-free:
# config -> config_types -> llm.types never loops back to config). Used as keys
# for the verified image-budget limits below.
from .llm.types import PROVIDER_OPENAI, PROVIDER_OPENAI_COMPAT, PROVIDER_VERTEX

log = logging.getLogger(__name__)


@dataclass
class LlmConfig:
    # No built-in endpoint: the legacy llm group is opt-in. Use providers +
    # model_configs for new setups; set these explicitly for the legacy path.
    url: str = ""
    model: str = ""
    api_key: str = field(default="", metadata={"secret": True})
    streaming: bool = True
    timeout: int = 300  # seconds for LLM HTTP requests
    context_window_size: int = 0  # 0 = not specified, fall back to compaction_max_tokens


@dataclass
class MattermostConfig:
    # Master switch. When false, the Mattermost client is not started even if
    # url + token are configured — lets the web gateway run standalone (e.g.
    # for the decafclaw client) without stealing a deployed bot's websocket.
    enabled: bool = True
    url: str = ""
    token: str = field(default="", metadata={"secret": True})
    bot_username: str = ""
    ignore_bots: bool = True
    ignore_webhooks: bool = False
    debounce_ms: int = 1000
    cooldown_ms: int = 1000
    require_mention: bool = True
    user_rate_limit_ms: int = 500
    channel_blocklist: list[str] = field(default_factory=list)
    circuit_breaker_max: int = 10
    circuit_breaker_window_sec: int = 30
    circuit_breaker_pause_sec: int = 60
    enable_emoji_confirms: bool = True
    stream_throttle_ms: int = 200


@dataclass
class NotesConfig:
    """Per-conversation scratchpad (#299) — always-loaded
    ``notes_append`` / ``notes_read`` tools backed by an append-only
    markdown file at ``workspace/conversations/{conv_id}/notes.md``,
    colocated with the conversation archive and other sidecars. Recent
    entries auto-inject into context at turn start.
    """

    enabled: bool = True
    max_entry_chars: int = 1024  # silent truncation at this cap
    context_max_entries: int = 20  # most recent N injected per turn
    context_max_chars: int = 4096  # body cap on the inject (drops oldest)
    # File-level cap. ``read_notes`` reads the full file, so we trim
    # the oldest entries on append once the file exceeds this many
    # lines — keeps long-running conversations from accumulating
    # unbounded IO/CPU per turn. 0 = no cap.
    max_total_entries: int = 1000


@dataclass
class RemindersConfig:
    """Scoped reminders (#1127): admin-authored rules checked against a
    tool call's finalized arguments before dispatch.

    Rules live at ``data/{agent_id}/reminders.json`` — the admin tier, so the
    agent cannot author or edit them. A match either rejects the call with
    correction guidance or lets it run and injects one ephemeral reminder
    message (never archived). Disabled by default: this is an experiment.
    """

    enabled: bool = False
    # Matches allowed per rule per conversation. A reject rule keeps rejecting
    # after this budget, with terse text instead of the full message; a remind
    # rule stops injecting (advisory context becomes tax once repeated).
    max_matches_per_rule: int = 3
    # Per-turn cap on injected reminders, bounding worst-case context growth.
    max_per_turn: int = 2


@dataclass
class RecentJournalConfig:
    """Auto-surface recent journal entries (#306).

    At interactive turn start, inject a bounded summary of recently
    written journal entries on a small fixed budget, separate from the
    dynamic semantic-retrieval pool so it doesn't compete with it.
    Recency is a strong signal as a retrieval *mode* — surfacing "what
    was I just thinking about?" — where it's weak as a factor folded
    into a composite similarity score.

    Only entries newer than the last one already surfaced this
    conversation are injected (high-water mark on ``ComposerState``), so
    each entry appears at most once. Skipped for heartbeat / scheduled /
    child-agent modes.
    """

    enabled: bool = True
    max_hours: int = 24  # only entries written within this window
    max_entries: int = 5  # most recent K within the window, whichever is tighter
    max_tokens: int = 1024  # soft cap on the injected body (trims oldest)


@dataclass
class CompactionConfig:
    url: str = ""  # empty = resolve from llm via resolved()
    model: str = ""  # empty = resolve from llm via resolved()
    api_key: str = field(default="", metadata={"secret": True})
    max_tokens: int = 100000
    llm_max_tokens: int = 0  # 0 = use max_tokens
    # User-configurable compaction threshold ratio (#1099). None = use
    # ``max_tokens``; when set and the model context window is known
    # (``context_window_size > 0``), the effective threshold is
    # ``int(context_window_size * ratio)`` instead of the fixed cap.
    threshold_ratio: float | None = None
    preserve_turns: int = 5
    memory_sweep_enabled: bool = True
    # Threaded structured slice (#302). When enabled, compaction emits
    # a JSON block alongside its prose summary listing decisions, open
    # questions, and artifacts; the slice persists across compactions
    # so high-signal facts don't decay across iterated summarization.
    decisions_enabled: bool = True
    decisions_max_per_category: int = 30  # FIFO cap per category; 0 = no cap

    def resolved(self, config) -> CompactionConfig:
        """Return copy with empty fields filled from config.llm."""
        return replace(
            self,
            url=self.url or config.llm.url,
            model=self.model or config.llm.model,
            api_key=self.api_key or config.llm.api_key,
        )


@dataclass
class CleanupConfig:
    """Tool-result clearing — a lightweight tier that runs before
    full compaction. Replaces large old tool-message bodies with a
    short stub so the agent loop doesn't keep paying attention budget
    on raw tool output it has already synthesized. A second, more
    aggressive pass stubs tool errors that a later success of the same
    tool has already resolved. See docs/context-composer.md, #298,
    and #540.
    """

    enabled: bool = True
    preserve_prompt_cache: bool = True  # no interactive history rewrites between compactions
    min_turn_age: int = 2  # tool messages from the last N user turns are protected
    min_size_bytes: int = 1024  # smaller messages aren't worth clearing
    preserve_tools: list[str] = field(
        default_factory=lambda: [
            "activate_skill",
            "checklist_create",
            "checklist_step_done",
            "checklist_abort",
            "checklist_status",
        ]
    )
    # Resolved-error pruning (#540): stub a tool error whose same-tool
    # failure was later recovered. More aggressive than the size/age clear —
    # applies regardless of size and age. Skipped in non-interactive modes.
    prune_resolved_errors: bool = True
    # Conservative matching: only prune when the later success carries the
    # same arguments as the failed call. Default False = same-tool-only.
    prune_resolved_same_args_only: bool = False
    # Superseded-read pruning (#1146): stub a successful full-file read whose
    # path was later re-read successfully (full coverage) — the older copy can
    # conflict with current content. Applies regardless of size/age; skipped in
    # non-interactive modes. Disable via ``cleanup.prune_superseded_reads = false``.
    prune_superseded_reads: bool = True


@dataclass
class EmbeddingConfig:
    model: str = "text-embedding-004"
    provider: str = ""  # named provider from config.providers; empty = legacy resolved()
    url: str = ""  # empty = resolve from llm via resolved()
    api_key: str = field(default="", metadata={"secret": True})
    search_strategy: str = "substring"
    dimensions: int = 768

    def resolved(self, config) -> EmbeddingConfig:
        """Return copy with empty fields filled from config.llm."""
        return replace(
            self,
            url=self.url or config.llm.url.replace("/chat/completions", "/embeddings"),
            api_key=self.api_key or config.llm.api_key,
        )


@dataclass
class HeartbeatConfig:
    interval: str = "30m"
    user: str = ""
    channel: str = ""
    suppress_ok: bool = True


@dataclass
class SchedulesConfig:
    # Global switch for the schedule timer (#957). Per-task `enabled` in
    # SCHEDULE.md frontmatter still applies when this is true.
    enabled: bool = True


@dataclass
class HttpConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 18880
    secret: str = field(default="", metadata={"secret": True})
    base_url: str = ""
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MB


@dataclass
class ShellConfig:
    aux_approval_enabled: bool = False
    # Foreground waiting budget for `shell` (#1136). Opt-in: when set (> 0), a
    # foreground command is started through the shared background-job runner and
    # awaited up to this budget; on expiry the SAME process continues as a managed
    # background job and the tool returns its job handle instead of output.
    # None (default) keeps today's blocking behavior with a hard timeout.
    foreground_wait_budget_sec: float | None = None
    # Total runtime limit for shell commands (seconds). Replaces the former
    # hardcoded 30s foreground timeout; for a handed-off job it becomes the job's
    # max_lifetime, enforced by the existing background-job expiry policy. Kept
    # separate from foreground_wait_budget_sec (how long to WAIT) and from the
    # explicit-background default lifetime (BackgroundConfig / 600s).
    max_runtime_sec: float = 30.0
    # Dedicated model for the shell auto-approval judge (#1213). Empty = ride
    # on the legacy aux chain (auxiliary_model → active conversation model →
    # default). When set, must name a model_configs key — discovered models
    # materialize as keys, but a hand-written raw name is not routable (the
    # shell group has no legacy endpoint of its own) and is treated as unset
    # with a warning.
    aux_approval_model: str = ""
    # Global, scope-keyed rulebook (#1641 phase 4). Rules keyed by
    # (activity scope, command shape) are merged into the judge prompt for
    # commands in that scope. Reads are in the hot path; writes only through
    # the admin_* tools with human confirmation. Off disables merging without
    # touching the files (the ledger keeps accumulating evidence).
    scope_rules_enabled: bool = True
    # Promotion thresholds: a ledger shape becomes a proposed rule at N human
    # approvals with no more than M denials. Defaults are the design's N=2, M=0.
    scope_rule_min_approvals: int = 2
    scope_rule_max_denials: int = 0
    # Kill switch for the deterministic read-only tier (#1641). When False, a
    # provably read-only command falls through to the normal pipeline (security
    # monitor → judge → human) instead of being allowed silently.
    #
    # This tier is the one place a command can run with NO human and NO model
    # in the loop, so a false positive is an unreviewed execution. Having an
    # off switch means a suspect classifier can be neutralized by editing
    # config.json (which the watcher applies in place, no restart) rather than
    # reverting and redeploying. Default True: the tier is the point of the
    # feature, and off-by-default would ship it dead.
    read_only_enabled: bool = True
    # How many recent shell calls define a conversation's established activity
    # (#1641). A command is "in direction" only when every domain it touches
    # appeared in this window. Lower it to make drift prompt sooner.
    #
    # 0 or a negative value DISABLES direction entirely: every command counts
    # as established, so the new-activity tier never fires. This is the safe
    # off switch — an unbounded window (all history counts) would re-create the
    # stale-direction failure the window exists to avoid, and having "0" mean
    # "unbounded" made the setting that looks like "off" the most dangerous
    # value in the file.
    activity_window: int = 50
    # Shadow mode for direction inference (#1661). When True, every command
    # still derives its activity and the pipeline still decides exactly as
    # before; a `direction_shadow` audit event records what the direction tier
    # *would* have done (prompt / defer / pass). This is the low-risk way to
    # compare direction decisions with real outcomes before trusting the tier.
    direction_shadow_mode: bool = False

    # --- Resource caps for spawned shell commands ---------------------------
    # Agent-authored shell commands are the least predictable child processes
    # in the system. Without caps, one runaway command can take the whole host
    # down: memory exhaustion makes the kernel thrash and stop answering SSH,
    # and CPU saturation starves the daemon that would otherwise report it.
    # Each capped command runs in a transient systemd scope, so the kernel
    # kills the command instead of the daemon.
    #
    # Values are systemd memory specs: a percentage of physical memory or an
    # absolute size (e.g. "2G", "512M"). Percentages survive a VM resize.
    # MemoryMax is the hard limit where the cgroup OOM killer fires; empty
    # disables that limit.
    #
    # 40% of this 3.8 GB host caps one command at ~1.5 GB, which still leaves
    # the daemon, Marimo, Television and Syncthing resident. Raise it on a host
    # with more RAM rather than removing it.
    resource_limits_enabled: bool = True
    memory_max: str = "40%"
    # Empty by default, and that is deliberate. MemoryHigh is a soft threshold
    # at which the kernel starts reclaiming rather than killing. With swap
    # allowed that means "page out and survive". With memory_swap_max="0" there
    # is nowhere to reclaim to, so the kernel instead THROTTLES the allocating
    # process: it slows to a crawl, holds its memory, and never trips
    # MemoryMax. The command then burns its entire timeout before being
    # SIGTERM'd, and the agent sees a timeout rather than "you hit your memory
    # cap". A hard cap plus swap disabled gives a fast, unambiguous kill, which
    # is what a runaway should get. Set a value here only if you also allow
    # swapping (memory_swap_max > 0) and want graceful reclaim.
    memory_high: str = ""
    # A cgroup's memory.swap.max defaults to "max", so a memory cap alone would
    # let a runaway spill into swap instead of dying — moving the thrash rather
    # than preventing it. Keep this at "0" unless you deliberately want capped
    # commands to be able to swap.
    memory_swap_max: str = "0"
    # Relative CPU share (systemd's own default is 100). Lowering it means a
    # command that pins every core still leaves the daemon and SSH scheduled.
    # None = systemd default, i.e. no explicit CPUWeight property.
    cpu_weight: int | None = 50
    # Hard task (thread/process) count for the scope. A runaway recursive
    # script or fork bomb hits this instead of consuming the host's pid space.
    # None = systemd default.
    tasks_max: int | None = 512


@dataclass
class TerminalConfig:
    enabled: bool = True
    buffer_bytes: int = 10 * 1024 * 1024  # ring buffer cap per session
    default_cwd: str | None = None  # falls back to workspace_path
    allowed_cwd_roots: list[str] = field(default_factory=list)  # empty → [workspace, $HOME]
    shell_override: str | None = None  # falls back to $SHELL or /bin/sh
    max_sessions_per_conv: int = 8


@dataclass
class PreemptiveSearchConfig:
    """Pre-emptive keyword-match against the current user message to
    promote relevant deferred tools into the active set for the turn.
    See docs/preemptive-tool-search.md.
    """

    enabled: bool = True
    max_matches: int = 10


@dataclass
class MapWidgetConfig:
    """Tile source for the bundled `map` canvas widget. Server-controlled —
    injected into widget data by the registry normalizer so the agent can't
    author it. Default is OpenStreetMap's public tile server (fine for
    low-volume personal use; point at your own server for anything heavier)."""

    tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tile_attribution: str = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    max_zoom: int = 19


@dataclass
class WebFrameWidgetConfig:
    """Security allowlist for the bundled `web_frame` canvas widget."""

    allowed_origins: list[str] = field(default_factory=list)
    allow_lan_and_tailnet: bool = True


@dataclass
class WidgetsConfig:
    """Per-widget server-side configuration."""

    map: MapWidgetConfig = field(default_factory=MapWidgetConfig)
    web_frame: WebFrameWidgetConfig = field(default_factory=WebFrameWidgetConfig)


@dataclass
class AgentConfig:
    data_home: str = "./data"
    id: str = "decafclaw"
    user_id: str = "user"
    max_tool_iterations: int = 200
    max_concurrent_tools: int = 5
    max_message_length: int = 50000
    tool_context_budget_pct: float = 0.10
    critical_tools: list[str] = field(default_factory=list)
    child_max_tool_iterations: int = 10
    child_timeout_sec: int = 300
    # Parallel sub-agent dispatch via `delegate_tasks` (#397). The
    # plural tool runs up to `max_parallel_delegates` children
    # concurrently; the total batch size is capped at
    # `max_tasks_per_delegate_call` to prevent fan-out blowup.
    max_parallel_delegates: int = 3
    max_tasks_per_delegate_call: int = 10
    # Wall-clock timeout applied to each non-MCP tool call in execute_tool.
    # `<= 0` disables the wrapper globally. Per-tool overrides live on
    # TOOL_DEFINITIONS entries via the `timeout` key.
    tool_timeout_sec: int = 180
    # Wall-clock timeout for user confirmation prompts. `<= 0` or `None`
    # disables the timeout so the agent waits indefinitely.
    confirmation_timeout_sec: int | None = None
    turn_on_new_message: str = "queue"  # "queue" or "cancel"
    show_context_status: bool = True
    auto_refresh_skills: bool = True
    preemptive_search: PreemptiveSearchConfig = field(default_factory=PreemptiveSearchConfig)


@dataclass
class ReflectionConfig:
    enabled: bool = True
    url: str = ""  # empty = resolve from llm
    model: str = ""  # empty = resolve from llm
    # A model_configs key. When set AND present in config.model_configs, every
    # reflection judge routes through it, so the author does not grade its own
    # homework (#591). Otherwise resolution continues down the chain: `model`
    # if it too is a model_configs key -> `model` as a legacy raw name via
    # resolved() -> default_model -> the legacy resolved() llm-group fallback.
    # Any explicitly set `model` outranks default_model, whether or not it
    # names a model_configs key (#752).
    verifier_model: str = ""
    api_key: str = field(default="", metadata={"secret": True})
    max_retries: int = 2
    visibility: str = "hidden"  # hidden | visible | debug
    max_tool_result_len: int = 2000  # max chars per tool result shown to judge

    def resolved(self, config) -> ReflectionConfig:
        """Return copy with empty url/model/api_key filled from config.llm."""
        return replace(
            self,
            url=self.url or config.llm.url,
            model=self.model or config.llm.model,
            api_key=self.api_key or config.llm.api_key,
        )


@dataclass
class VaultConfig:
    vault_path: str = "workspace/vault/"
    agent_folder: str = "agent/"
    recent_changes_limit: int = 50
    # Vault-relative folder paths that bypass the user-page write
    # confirmation. Prefix match (no globs). Empty by default — opt-in.
    user_writable_paths: list[str] = field(default_factory=list)


class RetrievalMode(StrEnum):
    """How auto-retrieved vault memory is injected (``vault_retrieval.mode``, #301).

    String-valued so config JSON round-trips and existing string
    comparisons keep working; the enum makes a typo'd mode impossible to
    construct. Unknown values fall back to ``ALWAYS`` at compose time
    (``extensions.vault_retrieval.resolve_retrieval_mode``).
    """

    ALWAYS = "always"  # inject scored full-body candidates (default, back-compat)
    HEADLINES = "headlines"  # title + summary + score only; agent pulls bodies via vault_read
    ON_DEMAND = "on_demand"  # no auto-injection; agent drives via vault_search / vault_read


@dataclass
class ExtensionConfig:
    """Generic runtime-extension configuration entry (``extensions.<name>``).

    ``options`` is extension-owned data: each extension type validates and
    interprets its own keys. Core does not apply env-var overrides to options.
    """

    enabled: bool = True
    # Extension type registered via ``extensions.register_extension_type``.
    # Empty means "use the config key as the type name".
    type: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryConfig:
    """Deprecated: legacy ``memory`` block, migrated to ``extensions.mem0``.

    Kept only so ``load_config`` can parse the old block (including its
    ``MEMORY_*`` env precedence) and normalize it. Not a ``Config`` field.
    """

    provider: str | None = None  # None | "mem0"
    qdrant_path: str = "data/mem0_qdrant"
    embedder_model: str = "BAAI/bge-small-en-v1.5"
    llm_base_url: str = "http://synobian:4100/v1"
    llm_model: str = "mandelbrot3-flint"
    api_key: str = field(default="", metadata={"secret": True})
    top_k: int = 5


@dataclass
class LifecycleConfig:
    """Turn lifecycle hooks configuration."""

    pre_compose_timeout: float = 0.3
    pre_compose_token_ceiling: int = 300
    # Bound for PRE_COMPACTION collectors before transcript summarization.
    pre_compaction_timeout: float = 2.0


@dataclass
class VaultRetrievalConfig:
    enabled: bool = True
    similarity_threshold: float = 0.3
    max_results: int = 5
    max_tokens: int = 4000  # Ceiling on auto-retrieved memory tokens (default 4000, see #1048)
    show_in_ui: bool = True
    # Retrieval injection mode (#301):
    #   "always"    — current default; inject scored full-body candidates
    #   "headlines" — inject title + summary + score only; agent calls
    #                 vault_read for full bodies on demand
    #   "on_demand" — inject nothing; agent drives retrieval via
    #                 vault_search / vault_read. @[[Page]] mentions
    #                 still inject (those are user-driven).
    # An unknown value logs a warning at compose time and falls back to "always".
    mode: RetrievalMode = RetrievalMode.ALWAYS
    headline_summary_max_chars: int = 120


@dataclass
class VaultGuideConfig:
    """Always-loaded vault guide (e.g. AGENTS.md).

    When a guide file is present at ``<vault_root>/<path>``, its contents
    are injected as an authoritative ``system`` section on every
    interactive turn (#592). Independent of vault_retrieval — plain file
    read, no embeddings.
    """

    enabled: bool = True
    path: str = "AGENTS.md"  # relative to vault root
    max_tokens: int = 2000  # AGENTS.md is ~500 tok; generous cap


@dataclass
class RelevanceConfig:
    # Relevance weights are renormalized over their own sum in
    # `score_candidates`, so the 0.65 threshold keeps comparable meaning even
    # when a config overrides only one of them. Importance is not part of this
    # sum (#1713): it is an ordering tie-break after the threshold.
    w_similarity: float = 0.625
    w_recency: float = 0.375
    w_importance: float = 0.0  # retained for config compatibility; not scored
    recency_decay_rate: float = 0.99  # per-hour exponential decay
    min_composite_score: float = 0.65  # relevance-only; candidates below drop
    graph_expansion_enabled: bool = True
    graph_expansion_similarity_discount: float = 0.7


@dataclass
class ImportanceConfig:
    """Weights for garden's deterministic weekly importance recompute (#197
    Phase 5, redesigned in #1713).

    Structural importance is PageRank over the full backlink graph:
    ``importance = clamp01(w_inbound * pagerank)``. Retrieval frequency was
    self-referential (retrieved pages score higher, which gets them retrieved
    more) and no reference signal was ever implemented, so both knobs are kept
    for config compatibility but default to zero and no longer feed the score.
    See ``skills/garden/tools.py::compute_importance_scores``.
    """

    w_retrieval: float = 0.0
    w_inbound: float = 1.0
    w_reference: float = 0.0


@dataclass
class ProviderConfig:
    """Connection config for an LLM provider."""

    type: str = ""  # "vertex", "openai-responses", "openai", "openai-compat" (alias "litellm")
    api_key: str = field(default="", metadata={"secret": True})
    url: str = ""  # litellm/openai base URL
    project: str = ""  # vertex GCP project
    region: str = ""  # vertex region (e.g. "us-central1")
    service_account_file: str = ""  # vertex: path to service account JSON key


@dataclass
class ModelConfig:
    """Named model configuration referencing a provider."""

    provider: str = ""  # key into providers dict
    model: str = ""  # model name for the provider
    context_window_size: int = 0
    timeout: int = 300
    streaming: bool = True
    reasoning_effort: str | None = None
    # Tri-state vision capability (#1143): True/False when known, None = unknown.
    # ``False`` makes the image budget turn every inbound image into an omission
    # notice (the model cannot see images, so sending them is wasted bytes).
    # ``None``/True budget normally. Discovered models may set this from the
    # gateway's capability report; hand-written configs leave it None.
    supports_vision: bool | None = None

    def __post_init__(self):
        if self.reasoning_effort is not None and self.reasoning_effort not in (
            "none",
            "minimal",
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ):
            raise ValueError(f"Invalid reasoning_effort: {self.reasoning_effort!r}")


# Verified per-provider-type image/request limits, from the providers' own docs.
# ``max_images`` caps the number of inline images per request; ``max_request_bytes``
# caps the whole serialized request body. A provider type absent here
# (openai-responses / litellm / scripted) has no verified limit and is
# deliberately unenforced (None) — coverage grows as limits are verified.
# ``openai`` subclasses the openai-compat contract, so it shares its limits.
#
# Sources (re-verified 2026-11-15):
# - OpenAI, "Images and vision": "Request size | Up to 512 MB total payload per
#   request", "Image count | Up to 1,500 images per request".
#   https://developers.openai.com/api/docs/guides/images-vision
# - Vertex AI, "Image understanding": Gemini 3+ / 2.5 "Maximum images per prompt:
#   3,000 (images and PDFs combined)"; "Maximum inline part size: 256 MB"
#   (30 MiB on non-migrated Gemini 2.5 endpoints); total generateContent payload
#   ≤ 500 MiB compressed. The byte cap here is the per-inline-part ceiling: the
#   budget is omit-only, so it also keeps the whole body under the payload limit.
#   https://cloud.google.com/vertex-ai/docs/generative-ai/docs/multimodal/image-understanding
_VERIFIED_IMAGE_LIMITS: dict[str, dict] = {
    PROVIDER_OPENAI_COMPAT: {"max_images": 1500, "max_request_bytes": 512 * 1024 * 1024},
    PROVIDER_OPENAI: {"max_images": 1500, "max_request_bytes": 512 * 1024 * 1024},
    PROVIDER_VERTEX: {"max_images": 3000, "max_request_bytes": 256 * 1024 * 1024},
}


@dataclass(frozen=True)
class ResolvedImageLimits:
    """The effective image/request limits for one provider type.

    ``None`` on a field means "no enforcement" for that dimension (the provider
    type has no verified limit). Built by :meth:`ImageBudgetConfig.resolved`.
    """

    provider_type: str | None = None
    max_images: int | None = None
    max_request_bytes: int | None = None


@dataclass
class ImageBudgetConfig:
    """Whole-request image + serialized-payload budget (#1143).

    Applied to the outbound request view after attachment resolution, before a
    provider call. See :mod:`decafclaw.image_budget` and docs/context-composer.md.

    ``providers`` holds per-provider-type overrides as plain dicts (the generic
    config loader does not auto-build a dict-of-dataclass). Each value may set
    ``max_images`` and/or ``max_request_bytes``; unset keys fall back to the
    verified defaults in :data:`_VERIFIED_IMAGE_LIMITS`. A provider type with no
    entry and no verified default is unenforced.
    """

    providers: dict[str, dict] = field(default_factory=dict)

    def resolved(self, provider_type: str | None) -> ResolvedImageLimits:
        """Merge the verified defaults for ``provider_type`` with any override.

        For each dimension (``max_images``, ``max_request_bytes``): an explicit
        override value wins — including an explicit null, which *disables* that
        dimension (e.g. keep a provider's byte cap but drop its count cap). With
        no override, the verified default applies; with neither, ``None`` (no
        enforcement). An unknown/empty provider type yields all-None.

        Malformed values fail open: a non-numeric dimension value or a
        non-mapping per-provider override (e.g. a hand-written string in
        config.json) logs a warning and disables that dimension (or the whole
        override) rather than raising on every turn.
        """
        if not provider_type:
            return ResolvedImageLimits(provider_type=provider_type or None)
        defaults = _VERIFIED_IMAGE_LIMITS.get(provider_type, {})
        override = self.providers.get(provider_type) or {}
        if not isinstance(override, dict):
            # A per-provider override must be a mapping. The env path always
            # yields one (``_parse_dict`` keeps JSON types, #1306); any other
            # type is a hand-written config.json value. Fail open: warn and
            # ignore rather than raising on every turn (the ``ast.literal_eval``
            # recovery for stringified mappings was removed in #1307).
            log.warning("image_budget: providers[%r] override %r is not a mapping; ignoring", provider_type, override)
            override = {}

        def pick(key: str):
            # An explicit override (key present) wins, null included (= disable).
            if key in override:
                val = override[key]
            elif key in defaults:
                val = defaults[key]
            else:
                return None
            if val is None:
                return None
            if isinstance(val, str):  # config.json (or a JSON string in env) may hand-write strings
                v = val.strip().lower()
                if v in ("", "none", "null"):
                    return None
                val = v
            try:
                return int(val)
            except (TypeError, ValueError):
                log.warning(
                    "image_budget: non-numeric %s=%r for provider %r; disabling that dimension", key, val, provider_type
                )
                return None

        return ResolvedImageLimits(
            provider_type=provider_type,
            max_images=pick("max_images"),
            max_request_bytes=pick("max_request_bytes"),
        )


@dataclass
class EmailConfig:
    """SMTP settings for the send_email tool and the email notification channel.

    Both surfaces share this core config (host / port / auth / sender).
    ``allowed_recipients`` only affects the tool — the notification
    channel has its own trust-boundary list in
    ``NotificationsChannelsConfig.email.recipient_addresses``.
    """

    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = field(default="", metadata={"secret": True})
    smtp_password: str = field(default="", metadata={"secret": True})
    use_tls: bool = True  # STARTTLS on port 587
    sender_address: str = ""  # From:
    # Tool-only allowlist. Entries match exact addresses or `@domain.com`
    # suffix patterns (case-insensitive). Empty list = every send
    # requires interactive confirmation.
    allowed_recipients: list[str] = field(default_factory=list)
    max_attachment_bytes: int = 10 * 1024 * 1024  # 10 MB — sum across attachments


@dataclass
class EmailChannelConfig:
    """Email notification channel.

    Each enabled notification at or above ``min_priority`` is emailed to
    all addresses in ``recipient_addresses``. The channel does NOT
    consult ``EmailConfig.allowed_recipients`` — these recipients are
    the trust boundary.
    """

    enabled: bool = False
    recipient_addresses: list[str] = field(default_factory=list)
    min_priority: str = "high"  # "low" | "normal" | "high"
    # Deferred delivery (#1664): "immediate" sends as soon as the record is
    # published. "deferred" waits escalate_after_sec and drops delivery when
    # the record is acknowledged first. Only attended, non-high records can
    # defer; email stays immediate by default because it is often the
    # primary interface rather than an escalation channel.
    delivery: str = "immediate"  # "immediate" | "deferred"
    escalate_after_sec: float = 60.0


@dataclass
class MattermostDMChannelConfig:
    """Mattermost direct-message channel for notifications.

    Delivery is skipped when ``recipient_username`` is empty or when the
    Mattermost client isn't running. See docs/notifications.md.
    """

    enabled: bool = False
    recipient_username: str = ""
    min_priority: str = "high"  # "low" | "normal" | "high"
    # Deferred delivery (#1664): see EmailChannelConfig. A Mattermost DM may
    # be the primary interface, so it stays immediate by default.
    delivery: str = "immediate"  # "immediate" | "deferred"
    escalate_after_sec: float = 60.0


@dataclass
class NtfyChannelConfig:
    """NTFY push channel for notifications and tool confirmations (#1494).

    Publishes inbox notifications to an ntfy topic, and (when
    ``notify_on_confirmation`` is true) pushes pending tool
    confirmations with Approve/Deny action buttons. The buttons POST
    single-use tokens to ``{http callback base}/actions/confirm``, so
    they are only included when ``config.http.enabled`` is true.
    ``server_url`` must be an ``http(s)://`` URL; an empty ``topic``
    keeps the adapter unsubscribed at startup.

    ``access_token`` is sent as ``Authorization: Bearer``. Use a
    private/self-hosted topic: the push carries approve links.
    """

    enabled: bool = False
    server_url: str = "https://ntfy.sh"
    # Topic name, appended to server_url for the publish endpoint.
    topic: str = ""
    access_token: str = field(default="", metadata={"secret": True})
    min_priority: str = "low"  # "low" | "normal" | "high"
    # Push pending tool confirmations with Approve/Deny action buttons when
    # the operator has not answered them in the transcript (#1664).
    notify_on_confirmation: bool = True
    default_priority: int = 1  # ntfy priority scale 1-5 (1=min/lowest urgency)
    confirmation_priority: int = 3  # ntfy priority scale 1-5 (3=default/medium urgency)
    # Deferred delivery (#1664): ntfy is the interrupting escalation
    # channel, so it defers by default. "immediate" restores the pre-#1664
    # push-at-publish behaviour.
    delivery: str = "deferred"  # "immediate" | "deferred"
    escalate_after_sec: float = 60.0
    # Wait this long after a confirmation request before pushing it. A
    # confirmation answered in the transcript first never reaches the
    # phone. 0 disables the deferral (push immediately). Must stay below
    # agent.confirmation_timeout_sec when that timeout is set: the timeout
    # synthesizes a denial, so a push at the same moment would buzz about an
    # already-refused command. Validated at config load.
    confirmation_escalate_after_sec: float = 60.0


@dataclass
class VaultPageChannelConfig:
    """Vault page notification channel — appends each matching
    notification to a daily rollup file under the configured folder.

    Complements the push channels (Mattermost DM, email) with a
    persistent, local audit trail. The channel does NOT embed the
    resulting pages for semantic search — notifications are a rolling
    log, not reference material. Vault folder browsing + ``vault_list``
    provide discoverability.

    The ``folder`` path is vault-root-relative and sandboxed at use
    time: absolute paths and ``..`` escapes are rejected.

    Enabled by default — the channel is purely local (no external
    delivery, no credentials, no cost) and an always-on audit trail is
    useful out of the box. Disable by setting ``enabled: false`` in
    ``config.json`` if you don't want the vault pages.
    """

    enabled: bool = True
    min_priority: str = "low"  # "low" | "normal" | "high"
    folder: str = "agent/pages/notifications"


@dataclass
class NotificationsChannelsConfig:
    """Per-channel adapter configuration for notifications."""

    mattermost_dm: MattermostDMChannelConfig = field(default_factory=MattermostDMChannelConfig)
    email: EmailChannelConfig = field(default_factory=EmailChannelConfig)
    ntfy: NtfyChannelConfig = field(default_factory=NtfyChannelConfig)
    vault_page: VaultPageChannelConfig = field(default_factory=VaultPageChannelConfig)


@dataclass
class NotificationsConfig:
    """Notification inbox settings. See docs/notifications.md."""

    retention_days: int = 30
    channels: NotificationsChannelsConfig = field(default_factory=NotificationsChannelsConfig)


@dataclass
class FileWatchConfig:
    """Configuration for one-shot file watches (#1560).

    ``poll_interval_sec`` bounds wake latency; the watch uses a stat call, so
    the default 1s poll stays cheap. ``notify_on_timeout`` turns a silent
    expiry into a wake turn that tells the agent the wait ended without an
    event.
    """

    enabled: bool = True
    poll_interval_sec: float = 1.0
    max_timeout_sec: int = 86400
    max_watches_per_conversation: int = 16
    notify_on_timeout: bool = False


@dataclass
class BackgroundConfig:
    """Configuration for background-process tool behavior."""

    wake_max_per_window: int = 20
    wake_window_sec: int = 60
    default_completion_tail_lines: int = 50
    # Explicit-background job lifetime (shell_background_start). Raised from
    # the former 600s hardcode (#1746): a full test suite outlives it. A
    # per-call max_lifetime_sec overrides this; 0 disables expiry.
    default_max_lifetime_sec: float = 3600.0


@dataclass
class WorkflowConfig:
    """Configuration for the durable workflow engine (#581).

    ``max_resume_attempts`` caps how many times the startup scan will
    auto-resume a workflow left at ``status="running"`` after a server
    crash. Each resume increments ``Journal.attempts``; once the counter
    reaches ``max_resume_attempts``, the scan flips ``status="error"``
    instead of retrying (bounds replay-storm risk).
    """

    max_resume_attempts: int = 3


@dataclass
class PreScriptConfig:
    """Pre-agent scripts for scheduled tasks (#450).

    A scheduled task may declare `pre_script:` in its frontmatter; the runner
    executes it with the project interpreter before the turn and injects stdout
    into the prompt, so mechanical work (fetch, query, diff) costs no LLM
    round-trips.

    The timeout is deliberately NOT charged against `agent.max_tool_iterations`.
    Nothing has been inferred yet when the script runs, so a slow fetch must not
    shrink the reasoning budget it was gathering data for.
    """

    enabled: bool = True
    # Float so tests can use sub-second values; ints work fine in config.json.
    timeout_sec: float = 60.0


@dataclass
class LoopBreakerConfig:
    """Diagnostic guardrails for infinite loop detection (#598).

    The agent loop monitors tool-call patterns to detect infinite loops
    (same tool with same args N times) and error surges (E errors within
    W tool results). Tuning these thresholds tunes the sensitivity of
    the trip signal.
    """

    enabled: bool = True
    repeat_threshold: int = 3  # same (tool, args) N times → trip
    error_threshold: int = 4  # this many errors...
    error_window: int = 6  # ...within the last N tool results → trip
    exempt_tools: tuple[str, ...] = ("wait", "shell_background_status")


@dataclass
class AuditLogConfig:
    """Persistent audit log event bus subscriber configuration.

    Records tool calls, LLM requests, skill activations,
    and MCP connections to a structured JSONL log. Supports file rotation
    or truncation.
    """

    enabled: bool = True
    path: str = "audit.jsonl"
    max_size_bytes: int = 10 * 1024 * 1024  # 10 MB default
    max_backups: int = 5


@dataclass
class TelemetryConfig:
    """Instrumentation sidecars (#310 tool usage, #409 reflection metrics,
    #197 retrieval telemetry, #10 Prometheus metrics).

    Append-only JSONL under ``workspace/``, metadata only — never tool
    args/returns, reflection response bodies, or prompt contents; only
    names, sizes, counts, token totals, and fingerprints. Producers are
    fail-open EventBus subscribers: a telemetry write that raises must
    never break a turn. Paths are workspace-relative. Enabled by default
    so a deployed agent starts collecting without a config edit — the
    point is a week of real data. Records older than ``retention_days`` are
    rotated into monthly archive files. ``metrics_enabled`` is the odd one
    out — it gates the Prometheus subscriber, which keeps no sidecar at all.
    See docs/tools.md, docs/reflection.md, docs/vault.md, and docs/metrics.md.
    """

    retention_days: int = 30
    tool_usage_enabled: bool = True
    tool_usage_path: str = "tool_usage.jsonl"
    reflection_metrics_enabled: bool = True
    reflection_metrics_path: str = "reflection/metrics.jsonl"
    retrieval_enabled: bool = True
    retrieval_path: str = "telemetry/retrieval.jsonl"
    loop_breaker_enabled: bool = True
    loop_breaker_path: str = "telemetry/loop_breaker.jsonl"
    metrics_enabled: bool = True
    otlp_endpoint: str | None = None
    otlp_service_name: str = "decafclaw"


@dataclass
class SecretPolicyConfig:
    """Admin-tool secret-file protection for ``data/{agent_id}/`` (issue #1013).

    The six admin tools are the agent's only door into this directory, and it
    holds secrets (tokens, API keys, MCP credentials). This dataclass carries
    the admin's *extensions* to the built-in protection in
    :mod:`decafclaw.secret_policy`:

    - ``refuse_paths`` — glob patterns (relative to ``config.agent_path``;
      ``*``/``?`` cross ``/``) naming files/directories to REFUSE on every admin
      tool. Add a new secret file here instead of in code.
    - ``redact_paths`` — dotted JSON paths (relative to the JSON root of a
      redactable file: ``config.json`` or ``mcp_servers.json``) to REDACT in
      ``admin_read`` output. A single ``*`` segment matches any one key, e.g.
      ``providers.*.api_key``. Applied on top of the annotation-derived set.

    This is harm reduction, not watertightness: best-effort, admin-extensible.
    See docs/data-layout.md ("Secret files") and docs/tools.md (Admin Files).
    """

    refuse_paths: list[str] = field(default_factory=list)
    redact_paths: list[str] = field(default_factory=list)


def is_secret(dc_class: type, field_name: str) -> bool:
    """Check if a dataclass field is marked as secret."""
    for f in dc_fields(dc_class):
        if f.name == field_name:
            return f.metadata.get("secret", False)
    return False


def get_env_alias(dc_class: type, field_name: str) -> str | None:
    """Get the env var alias for a dataclass field, if any."""
    for f in dc_fields(dc_class):
        if f.name == field_name:
            return f.metadata.get("env_alias")
    return None
