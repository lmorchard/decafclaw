"""Tool registry — priority classification, token estimation, deferred catalog."""

import json
import logging
from enum import Enum
from typing import TYPE_CHECKING

from ..util import estimate_tokens

if TYPE_CHECKING:
    from decafclaw.context import Context

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
    """Estimate token cost of tool definitions.

    Top-level prompt_guidelines are excluded because they are injected
    into the prompt as <tool_guidance> and accounted for there, not in
    the tool schema declarations sent to the model.
    """
    total = 0
    for td in tool_defs:
        if "prompt_guidelines" in td:
            clean = {k: v for k, v in td.items() if k != "prompt_guidelines"}
            total += estimate_tokens(json.dumps(clean))
        else:
            total += estimate_tokens(json.dumps(td))
    return total


def get_critical_names(config) -> set[str]:
    """Return the set of tool names forced to `critical` priority.

    Includes:
    - User env override (``config.agent.critical_tools``)
    """
    return set(config.agent.critical_tools)


def get_always_loaded_tool_names(config) -> set[str]:
    """Return the set of tool names belonging to always-loaded skills."""
    cached = getattr(config, "always_loaded_skill_tools", set()) or set()
    if cached:
        return cached
    names: set[str] = set()
    for skill in config.discovered_skills:
        if skill.always_loaded and skill.has_native_tools:
            try:
                from .skill_tools import _load_native_tools

                _, tool_defs, _ = _load_native_tools(skill)
                for td in tool_defs:
                    tname = td.get("function", {}).get("name")
                    if tname:
                        names.add(tname)
            except Exception as exc:
                log.debug("Failed to inspect always-loaded skill %r: %s", getattr(skill, "name", skill), exc)
    if names and hasattr(config, "always_loaded_skill_tools"):
        config.always_loaded_skill_tools = names
    return names


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
    promoted_tools: list[str] | set[str] | None = None,
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
    skill_tool_owners = getattr(config, "skill_tool_owners", {}) or {}

    always_loaded_tools = get_always_loaded_tool_names(config)
    on_demand_skill_tools = skill_tool_names - always_loaded_tools
    # On-demand activated skill tools without explicit declared priority
    # default to critical (backward compatibility: activation is the priority
    # signal). Tools with an explicit priority declaration honor it.
    undeclared_on_demand = set()
    for td in all_tool_defs:
        tname = td.get("function", {}).get("name", "")
        if tname in on_demand_skill_tools and td.get("priority") not in _PRIORITY_RANK:
            undeclared_on_demand.add(tname)

    # Build the "force critical" set
    force_critical = get_critical_names(config)
    force_critical |= fetched_names
    force_critical |= undeclared_on_demand
    force_critical |= preempt_matches

    budget = config.tool_context_budget
    max_active = getattr(config.agent, "max_active_tools", 40)

    # Bucket by resolved priority, preserving input order. Compute
    # each tool's token cost once up front — classify_tools runs every
    # agent iteration, so avoid re-encoding JSON per tool per fill loop.
    # Skill tools from non-activated skills bypass the priority buckets
    # entirely and go straight to deferred. They stay in the deferred
    # pool so tool_search can match against them, but they MUST NOT
    # appear in the active list (which becomes the LLM's tools parameter)
    # because seeing the schema there teaches the agent to call the tool
    # directly, skipping the skill body that documents how to use it.
    hidden_skill_tools: list[dict] = []
    critical: list[dict] = []
    normal: list[dict] = []
    low: list[dict] = []
    token_cost: dict[int, int] = {}
    for td in all_tool_defs:
        name = td.get("function", {}).get("name", "")
        token_cost[id(td)] = estimate_tool_tokens([td])
        if name in skill_tool_owners and name not in skill_tool_names and name not in always_loaded_tools:
            hidden_skill_tools.append(td)
            continue
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
            len(active),
            active_tokens,
            budget,
            max_active,
        )

    # If promoted tools are specified (e.g. from the active SessionMode),
    # prioritize them at the front of the normal tier in their declared order.
    if promoted_tools:
        promoted_list = list(promoted_tools)
        promoted_set = set(promoted_list)
        promoted_order = {name: i for i, name in enumerate(promoted_list)}

        p_tools = [td for td in normal if td.get("function", {}).get("name") in promoted_set]
        p_tools += [td for td in low if td.get("function", {}).get("name") in promoted_set]
        low = [td for td in low if td.get("function", {}).get("name") not in promoted_set]
        remaining_normal = [td for td in normal if td.get("function", {}).get("name") not in promoted_set]

        p_tools.sort(key=lambda td: promoted_order.get(td.get("function", {}).get("name"), 999))
        normal = p_tools + remaining_normal

    def _fill(tier: list[dict]) -> None:
        nonlocal active_tokens
        for td in tier:
            tokens = token_cost[id(td)]
            if active_tokens + tokens <= budget and len(active) + 1 <= max_active:
                active.append(td)
                active_tokens += tokens
            else:
                deferred.append(td)

    _fill(normal)
    _fill(low)

    # Hidden skill tools (from non-activated skills) always land in
    # deferred regardless of budget — they're for tool_search lookup,
    # never for direct callability via the active set.
    deferred.extend(hidden_skill_tools)

    if deferred:
        log.info(
            "Tool classification: %d active (%d tokens), %d deferred (%d hidden skill tools)",
            len(active),
            active_tokens,
            len(deferred),
            len(hidden_skill_tools),
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

        core_names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}

    core_tools: list[dict] = []
    mcp_tools: dict[str, list[dict]] = {}

    for td in deferred_defs:
        name = td.get("function", {}).get("name", "")

        if name.startswith("mcp__"):
            parts = name.split("__", 2)
            server = parts[1] if len(parts) >= 3 else "unknown"
            mcp_tools.setdefault(server, []).append(td)
        elif name in core_names:
            core_tools.append(td)
        # Else: skill tools — kept in the deferred pool for tool_search
        # to match against, but not rendered into the visible catalog.

    def _render(defs: list[dict]) -> list[str]:
        defs_sorted = sorted(defs, key=_deferred_sort_key)
        return [f"- {td['function']['name']} — {get_description(td)}" for td in defs_sorted]

    lines = ["## Available tools (use tool_search to load)\n"]

    if core_tools:
        lines.append("### Core")
        lines.extend(_render(core_tools))
        lines.append("")

    # Skill tools are intentionally NOT rendered here. Skills are
    # disclosed via the skill catalog (name + description in the
    # system prompt) and their tools become visible only after
    # activate_skill. The agent shouldn't see hidden tool names it
    # cannot call — that produced consistent failed-tool errors with
    # small parent models. Skill-tool defs remain in the deferred
    # pool so tool_search can match against them and surface the
    # owning skill.

    for server in sorted(mcp_tools):
        lines.append(f"### Tools from MCP server `{server}`")
        lines.extend(_render(mcp_tools[server]))
        lines.append("")

    # Wrap the assembled markdown in a <deferred_tools> block so the
    # model can distinguish the deferred catalog system message from
    # the main system prompt. The empty-input early-return above still
    # yields "" (no dangling wrapper). See docs/context-composer.md.
    return "<deferred_tools>\n" + "\n".join(lines) + "\n</deferred_tools>"


# -- Fetched tools helpers (list↔set for JSON serialization) -----------------


def get_fetched_tools(ctx: "Context") -> set[str]:
    """Read the fetched tools set from ctx.skills.data."""
    skill_data = ctx.skills.data
    raw = skill_data.get("fetched_tools", [])
    if isinstance(raw, set):
        return raw
    return set(raw)


def add_fetched_tools(ctx: "Context", names: set[str]) -> None:
    """Add tool names to the fetched set in ctx.skills.data."""
    existing = get_fetched_tools(ctx)
    ctx.skills.data["fetched_tools"] = sorted(existing | names)


# -- Tool guidance rendering & trust gating -----------------------------------


def build_tool_guidance_text(
    active_defs: list[dict],
    trusted_guidelines: dict[str, list[str]],
) -> str | None:
    """Build the <tool_guidance> system prompt block for the active tools.

    ``trusted_guidelines`` maps tool name -> guidelines taken from the
    definitions that core and trusted-skill owners declared (see
    ``tool_definitions.collect_trusted_tool_guidelines``). The active
    definitions only decide *which* names are in play; their own
    ``prompt_guidelines`` are never read, so a shadowing definition from an
    untrusted source cannot contribute text. MCP and workspace-tier tools are
    simply absent from the map.

    Guideline strings are deduplicated while preserving encounter order
    across active definitions.

    Returns:
        Formatted XML block or None if no active tool contributes guidelines.
    """
    seen_lines: set[str] = set()
    guidelines: list[str] = []

    for td in active_defs:
        name = td.get("function", {}).get("name", "")
        for line in trusted_guidelines.get(name, []):
            line_clean = line.strip()
            if line_clean and line_clean not in seen_lines:
                seen_lines.add(line_clean)
                guidelines.append(line_clean)

    if not guidelines:
        return None

    formatted = "\n".join(f"- {g}" for g in guidelines)
    return f"<tool_guidance>\n{formatted}\n</tool_guidance>"
