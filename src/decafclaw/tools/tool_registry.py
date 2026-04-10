"""Tool registry — token estimation, classification, and deferred list building."""

import json
import logging

from ..util import estimate_tokens

log = logging.getLogger(__name__)

# Tools that are always sent to the LLM, even in deferred mode.
DEFAULT_ALWAYS_LOADED = {
    "activate_skill", "shell", "workspace_read", "workspace_write",
    "web_fetch", "current_time", "delegate_task",
}


def estimate_tool_tokens(tool_defs: list[dict]) -> int:
    """Estimate token cost of tool definitions."""
    return sum(estimate_tokens(json.dumps(td)) for td in tool_defs)


def get_always_loaded_names(config) -> set[str]:
    """Return the set of tool names that should always be loaded.

    Includes tools from always-loaded skills (e.g. vault).
    """
    extra = set(config.agent.always_loaded_tools)
    # Include tool names from always-loaded skills
    for skill in getattr(config, "discovered_skills", []):
        if skill.always_loaded and skill.has_native_tools:
            # Tool names aren't known until loaded, but we store them on config
            # after first activation. Check the cached set.
            extra |= config.always_loaded_skill_tools
    return DEFAULT_ALWAYS_LOADED | extra


def classify_tools(
    all_tool_defs: list[dict],
    config,
    fetched_names: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Split tool definitions into active and deferred sets.

    If total token cost is under the budget, returns (all, []) — no deferral.
    Otherwise: active = always-loaded + fetched; deferred = everything else.
    """
    if fetched_names is None:
        fetched_names = set()

    budget = config.tool_context_budget
    total_tokens = estimate_tool_tokens(all_tool_defs)

    if total_tokens <= budget:
        return all_tool_defs, []

    always_loaded = get_always_loaded_names(config)
    include_names = always_loaded | fetched_names

    active = []
    deferred = []
    for td in all_tool_defs:
        name = td.get("function", {}).get("name", "")
        if name in include_names:
            active.append(td)
        else:
            deferred.append(td)

    log.info(
        f"Tool deferral active: {total_tokens} tokens > {budget} budget, "
        f"{len(active)} active, {len(deferred)} deferred"
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


def build_deferred_list_text(
    deferred_defs: list[dict],
    core_names: set[str] | None = None,
) -> str:
    """Build the deferred tool list block for system prompt injection.

    Groups tools by source: Core, Skill (by name), MCP (by server).
    """
    if not deferred_defs:
        return ""

    if core_names is None:
        from . import TOOL_DEFINITIONS  # deferred: circular dep
        core_names = {
            td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS
        }

    core_tools = []
    mcp_tools: dict[str, list[tuple[str, str]]] = {}
    skill_tools: list[tuple[str, str]] = []

    for td in deferred_defs:
        name = td.get("function", {}).get("name", "")
        desc = get_description(td)

        if name.startswith("mcp__"):
            # mcp__server__tool → group by server
            parts = name.split("__", 2)
            server = parts[1] if len(parts) >= 3 else "unknown"
            mcp_tools.setdefault(server, []).append((name, desc))
        elif name in core_names:
            core_tools.append((name, desc))
        else:
            skill_tools.append((name, desc))

    lines = ["## Available tools (use tool_search to load)\n"]

    if core_tools:
        lines.append("### Core")
        for name, desc in sorted(core_tools):
            lines.append(f"- {name} — {desc}")
        lines.append("")

    if skill_tools:
        lines.append("### Skills")
        for name, desc in sorted(skill_tools):
            lines.append(f"- {name} — {desc}")
        lines.append("")

    for server in sorted(mcp_tools):
        lines.append(f"### MCP: {server}")
        for name, desc in sorted(mcp_tools[server]):
            lines.append(f"- {name} — {desc}")
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
