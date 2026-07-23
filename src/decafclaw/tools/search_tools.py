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

    def _score(name: str, desc: str) -> int:
        """Relevance of a name+description pair to the query.

        `select:` is an exact-name lookup (1 = named, 0 = not). Keyword
        search ranks name-token matches above description-only matches so a
        query naming a tool doesn't get outranked by an unrelated tool that
        happens to mention the word in its description (#526)."""
        if is_select:
            return 1 if name in requested_names else 0
        n = name.lower()
        score = 0
        if keyword == n:
            score += 3  # exact name match — strongest signal
        elif keyword in n:
            score += 2  # partial name match
        if keyword in desc.lower():
            score += 1  # description-only match ranks last
        return score

    # 1. Match the skill catalog (name + description per skill). Track the
    #    best score seen per skill so a hidden-tool-name hit (step 2) can
    #    raise a skill already matched by its catalog entry.
    matched_skills: dict[str, tuple[str, int]] = {}  # name → (description, score)

    def _record_skill(skill_name: str, description: str, score: int) -> None:
        existing = matched_skills.get(skill_name)
        if existing is None or score > existing[1]:
            matched_skills[skill_name] = (description, score)

    for skill in discovered_skills:
        if skill.name in activated:
            continue
        score = _score(skill.name, skill.description)
        if score:
            _record_skill(skill.name, skill.description, score)

    # 2. Walk the deferred pool. Skill tools redirect to their owning
    #    skill; non-skill deferred tools (core demoted + MCP) get
    #    fetched into the active set as today.
    scored_tool_defs: list[tuple[int, dict]] = []
    for td in pool:
        name = td.get("function", {}).get("name", "")
        if not name:
            continue
        desc = get_description(td)
        owning_skill = skill_tool_owners.get(name)
        score = _score(name, desc)
        if not score:
            continue
        if owning_skill:
            if owning_skill in activated:
                continue
            # Surface the skill, not the tool. Look up the skill's
            # description for the response.
            skill_desc = ""
            for s in discovered_skills:
                if s.name == owning_skill:
                    skill_desc = s.description
                    break
            _record_skill(owning_skill, skill_desc, score)
        else:
            scored_tool_defs.append((score, td))

    # Order both result sets by score (descending), highest-signal first.
    # sorted() is stable, so ties keep skill-discovery / pool order.
    ranked_skills = sorted(
        matched_skills.items(), key=lambda kv: kv[1][1], reverse=True
    )
    fetched_tool_defs = [
        td for _, td in sorted(scored_tool_defs, key=lambda st: st[0], reverse=True)
    ]

    # Bound keyword-mode results across the combined output. Exact
    # `select:` queries return everything the user named.
    if not is_select and (ranked_skills or fetched_tool_defs):
        total = len(ranked_skills) + len(fetched_tool_defs)
        if total > max_results:
            # Prefer skills first (lighter — agent decides whether to
            # activate), then fill remaining budget with tools. Both lists
            # are already ranked, so truncation keeps the best matches.
            ranked_skills = ranked_skills[:max_results]
            remaining = max_results - len(ranked_skills)
            fetched_tool_defs = fetched_tool_defs[: max(remaining, 0)]
    matched_skills = dict(ranked_skills)

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
        for skill_name, (description, _score_) in ranked_skills:
            parts.append(f"- **{skill_name}**: {description}")

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
