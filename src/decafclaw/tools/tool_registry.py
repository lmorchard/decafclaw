"""Tool registry — priority classification, token estimation, deferred catalog."""

import json
import logging
from enum import Enum

from ..util import estimate_tokens

log = logging.getLogger(__name__)


class Priority(str, Enum):
    """Tool priority tiers. Used as str values in tool definition dicts
    via the top-level ``"priority"`` field."""
    CRITICAL = "critical"
    NORMAL = "normal"
    LOW = "low"


# Rank used for sorting. Higher rank = higher priority.
_PRIORITY_RANK = {
    Priority.CRITICAL.value: 2,
    Priority.NORMAL.value: 1,
    Priority.LOW.value: 0,
}


def estimate_tool_tokens(tool_defs: list[dict]) -> int:
    """Estimate token cost of tool definitions."""
    return sum(estimate_tokens(json.dumps(td)) for td in tool_defs)


def get_critical_names(config) -> set[str]:
    """Return the set of tool names forced to `critical` priority.

    Includes:
    - User env override (``config.agent.critical_tools``)
    - Tool names from always-loaded skills (cached on config after
      first activation)
    """
    extra = set(config.agent.critical_tools)
    for skill in getattr(config, "discovered_skills", []):
        if skill.always_loaded and skill.has_native_tools:
            extra |= config.always_loaded_skill_tools
    return extra


def get_priority(tool_def: dict, config, force_critical: set[str]) -> str:
    """Resolve the priority for a single tool definition.

    Precedence (highest to lowest):
    1. Name is in ``force_critical`` (env override, activated skills,
       fetched, always-loaded skill tools) → ``critical``
    2. Declared ``priority`` field on the tool def → use that
    3. Default → ``normal`` (e.g. MCP tools, which the MCP layer doesn't
       tag with priority; or any tool that forgot to declare — a test
       invariant fails fast for core tools missing this field)
    """
    name = tool_def.get("function", {}).get("name", "")
    if name in force_critical:
        return Priority.CRITICAL.value

    declared = tool_def.get("priority")
    if declared in _PRIORITY_RANK:
        return declared

    return Priority.NORMAL.value


def classify_tools(
    all_tool_defs: list[dict],
    config,
    fetched_names: set[str] | None = None,
    skill_tool_names: set[str] | None = None,
    *,
    preempt_matches: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split tool definitions into active and deferred sets by priority.

    Algorithm:
    1. Resolve every tool's priority.
    2. All ``critical`` tools enter the active set (hard floor, included
       even if over budget).
    3. ``normal`` tools added one at a time while under
       ``tool_context_budget`` and ``max_active_tools``.
    4. ``low`` tools added only if room remains after ``normal``.
    5. Everything else goes to deferred.

    Input order within a tier is preserved so callers can influence
    tie-breaks by ordering the input list.

    ``preempt_matches`` is a set of tool names promoted by pre-emptive
    keyword matching on the current user message; they're treated as
    critical for this turn (same hard-floor semantics as fetched and
    skill tools). See docs/preemptive-tool-search.md.
    """
    fetched_names = fetched_names or set()
    skill_tool_names = skill_tool_names or set()
    preempt_matches = preempt_matches or set()

    # Build the "force critical" set
    force_critical = get_critical_names(config)
    force_critical |= fetched_names
    force_critical |= skill_tool_names
    force_critical |= preempt_matches

    budget = config.tool_context_budget
    max_active = getattr(config.agent, "max_active_tools", 40)

    # Bucket by resolved priority, preserving input order. Compute
    # each tool's token cost once up front — classify_tools runs every
    # agent iteration, so avoid re-encoding JSON per tool per fill loop.
    critical: list[dict] = []
    normal: list[dict] = []
    low: list[dict] = []
    token_cost: dict[int, int] = {}
    for td in all_tool_defs:
        token_cost[id(td)] = estimate_tool_tokens([td])
        prio = get_priority(td, config, force_critical)
        if prio == Priority.CRITICAL.value:
            critical.append(td)
        elif prio == Priority.LOW.value:
            low.append(td)
        else:
            normal.append(td)

    # Start with critical as the hard floor
    active = list(critical)
    active_tokens = sum(token_cost[id(td)] for td in active)
    deferred: list[dict] = []

    if active_tokens > budget or len(active) > max_active:
        log.warning(
            "Critical tool set exceeds budget or count: "
            "%d tools / %d tokens (budget %d, max %d). "
            "Critical tools are included anyway.",
            len(active), active_tokens, budget, max_active,
        )

    def _fill(tier: list[dict]) -> None:
        nonlocal active_tokens
        for td in tier:
            tokens = token_cost[id(td)]
            if (active_tokens + tokens <= budget
                    and len(active) + 1 <= max_active):
                active.append(td)
                active_tokens += tokens
            else:
                deferred.append(td)

    _fill(normal)
    _fill(low)

    if deferred:
        log.info(
            "Tool classification: %d active (%d tokens), %d deferred",
            len(active), active_tokens, len(deferred),
        )
    return active, deferred


def get_description(tool_def: dict) -> str:
    """Extract a short description from a tool definition."""
    desc = tool_def.get("function", {}).get("description", "")
    # First sentence or first 80 chars
    for sep in (". ", ".\n"):
        idx = desc.find(sep)
        if idx > 0:
            return desc[: idx + 1]
    if len(desc) > 80:
        return desc[:77] + "..."
    return desc


def _get_declared_priority(tool_def: dict) -> str:
    """Return the priority declared on a tool def, defaulting to normal."""
    prio = tool_def.get("priority")
    if prio in _PRIORITY_RANK:
        return prio
    return Priority.NORMAL.value


def _deferred_sort_key(tool_def: dict) -> tuple:
    """Sort key for the deferred catalog: (priority desc, source asc, name asc).

    Priority is inverted so higher priorities come first. Source is taken
    from the `_source_skill` tag if present (skill tools), the server
    segment of an ``mcp__server__tool`` name (MCP tools), or empty string
    (core tools — section heading already encodes the source).
    """
    name = tool_def.get("function", {}).get("name", "")
    prio = _get_declared_priority(tool_def)
    priority_rank = _PRIORITY_RANK.get(prio, _PRIORITY_RANK[Priority.NORMAL.value])

    source = tool_def.get("_source_skill", "")
    if not source and name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) >= 3:
            source = parts[1]

    return (-priority_rank, source, name)


def build_deferred_list_text(
    deferred_defs: list[dict],
    core_names: set[str] | None = None,
) -> str:
    """Build the deferred tool list block for system prompt injection.

    Groups tools by source section (Core / Skills / MCP: server). Within
    each section, sorts by ``(priority desc, source asc, name asc)`` so
    high-priority tools appear first and tools from the same skill or
    MCP server cluster together.
    """
    if not deferred_defs:
        return ""

    if core_names is None:
        from . import TOOL_DEFINITIONS  # deferred: circular dep
        core_names = {
            td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS
        }

    core_tools: list[dict] = []
    mcp_tools: dict[str, list[dict]] = {}
    skill_tools: list[dict] = []

    for td in deferred_defs:
        name = td.get("function", {}).get("name", "")

        if name.startswith("mcp__"):
            parts = name.split("__", 2)
            server = parts[1] if len(parts) >= 3 else "unknown"
            mcp_tools.setdefault(server, []).append(td)
        elif name in core_names:
            core_tools.append(td)
        else:
            skill_tools.append(td)

    def _render(defs: list[dict]) -> list[str]:
        defs_sorted = sorted(defs, key=_deferred_sort_key)
        return [
            f"- {td['function']['name']} — {get_description(td)}"
            for td in defs_sorted
        ]

    lines = ["## Available tools (use tool_search to load)\n"]

    if core_tools:
        lines.append("### Core")
        lines.extend(_render(core_tools))
        lines.append("")

    if skill_tools:
        lines.append("### Skills")
        lines.extend(_render(skill_tools))
        lines.append("")

    for server in sorted(mcp_tools):
        lines.append(f"### Tools from MCP server `{server}`")
        lines.extend(_render(mcp_tools[server]))
        lines.append("")

    return "\n".join(lines)


# -- Fetched tools helpers (list↔set for JSON serialization) -----------------


def get_fetched_tools(ctx) -> set[str]:
    """Read the fetched tools set from ctx.skills.data."""
    skill_data = ctx.skills.data
    raw = skill_data.get("fetched_tools", [])
    if isinstance(raw, set):
        return raw
    return set(raw)


def add_fetched_tools(ctx, names: set[str]) -> None:
    """Add tool names to the fetched set in ctx.skills.data."""
    existing = get_fetched_tools(ctx)
    ctx.skills.data["fetched_tools"] = sorted(existing | names)
