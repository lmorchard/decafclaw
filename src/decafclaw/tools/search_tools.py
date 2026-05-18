"""Tool search — deferred-tool loading + skill discovery via keyword or exact name."""

import json
import logging

from ..media import ToolResult
from .tool_registry import add_fetched_tools, get_description

log = logging.getLogger(__name__)


def tool_search(ctx, query: str, max_results: int = 10) -> ToolResult:
    """Search for tools / skills and load matched tool definitions.

    Matches against:
    - Non-skill deferred tools (core demoted + MCP) — fetches them as today.
    - Skill catalog entries (name + description) — returns skill names; the
      agent must call ``activate_skill`` to load the skill's body and tools.
    - Hidden skill-tool names and descriptions — surfaces the OWNING skill,
      not the individual tool, so the agent learns where the capability
      lives even if it recalled a specific tool name from elsewhere.
    """
    pool = ctx.tools.deferred_pool or []
    skill_tool_owners = ctx.config.skill_tool_owners or {}
    discovered_skills = ctx.config.discovered_skills or []
    activated = ctx.skills.activated

    is_select = query.startswith("select:")
    requested_names: set[str] = set()
    keyword = ""
    if is_select:
        requested_names = {n.strip() for n in query[7:].split(",") if n.strip()}
    else:
        keyword = query.lower()

    def _matches(name: str, desc: str) -> bool:
        if is_select:
            return name in requested_names
        return keyword in name.lower() or keyword in desc.lower()

    # 1. Match the skill catalog (name + description per skill).
    matched_skills: dict[str, str] = {}  # skill name → description (deduped)
    for skill in discovered_skills:
        if skill.name in activated:
            continue
        if _matches(skill.name, skill.description):
            matched_skills.setdefault(skill.name, skill.description)

    # 2. Walk the deferred pool. Skill tools redirect to their owning
    #    skill; non-skill deferred tools (core demoted + MCP) get
    #    fetched into the active set as today.
    fetched_tool_defs: list[dict] = []
    for td in pool:
        name = td.get("function", {}).get("name", "")
        if not name:
            continue
        desc = get_description(td)
        owning_skill = skill_tool_owners.get(name)
        if owning_skill:
            if owning_skill in activated:
                continue
            if _matches(name, desc):
                # Surface the skill, not the tool. Look up the skill's
                # description for the response.
                if owning_skill not in matched_skills:
                    for s in discovered_skills:
                        if s.name == owning_skill:
                            matched_skills[owning_skill] = s.description
                            break
        else:
            if _matches(name, desc):
                fetched_tool_defs.append(td)

    # Bound keyword-mode results across the combined output. Exact
    # `select:` queries return everything the user named.
    if not is_select and (matched_skills or fetched_tool_defs):
        total = len(matched_skills) + len(fetched_tool_defs)
        if total > max_results:
            # Prefer skills first (lighter — agent decides whether to
            # activate), then fill remaining budget with tools.
            keep_skills = list(matched_skills.items())[:max_results]
            matched_skills = dict(keep_skills)
            remaining = max_results - len(matched_skills)
            fetched_tool_defs = fetched_tool_defs[: max(remaining, 0)]

    missing: set[str] = set()
    if is_select:
        found = set(matched_skills.keys()) | {
            td["function"]["name"] for td in fetched_tool_defs
        }
        missing = requested_names - found

    if not matched_skills and not fetched_tool_defs:
        return ToolResult(
            text=(
                f"No matches for '{query}'. "
                "Check the Available Skills catalog in your instructions, "
                "or pass a different keyword."
            )
        )

    # Register non-skill matches as fetched so they become callable.
    if fetched_tool_defs:
        add_fetched_tools(ctx, {td["function"]["name"] for td in fetched_tool_defs})

    parts: list[str] = []

    if matched_skills:
        parts.append(
            f"{len(matched_skills)} skill(s) matched. Call "
            "activate_skill(name) to load a skill's body and tools."
        )
        for skill_name in sorted(matched_skills):
            parts.append(f"- **{skill_name}**: {matched_skills[skill_name]}")

    if fetched_tool_defs:
        parts.append(
            f"{len(fetched_tool_defs)} tool(s) loaded. "
            "These tools are now available to call."
        )
        for td in fetched_tool_defs:
            parts.append(json.dumps(td, indent=2))

    if missing:
        parts.append(f"Not found: {', '.join(sorted(missing))}")

    return ToolResult(text="\n\n".join(parts))


SEARCH_TOOLS = {
    "tool_search": tool_search,
}

SEARCH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "tool_search",
            "description": (
                "Find skills and deferred tools by keyword or exact name. "
                "For SKILLS, returns the skill name and description — call "
                "activate_skill(name) to load the skill's body and tools. "
                "For non-skill deferred tools, returns the tool schema and "
                "makes the tool immediately callable. Use 'select:name1,name2' "
                "for exact lookup by name; otherwise the query is a "
                "case-insensitive keyword matched against skill names, skill "
                "descriptions, and hidden skill-tool names + descriptions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Keyword search term, or 'select:name1,name2' "
                            "for exact selection by skill or tool name"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            "Max combined skill+tool matches for keyword "
                            "search (default 10)"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]
