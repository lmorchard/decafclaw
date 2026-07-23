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

    # Score is a (name_tier, description_hit) tuple, compared
    # lexicographically: the name tier dominates so an exact-name match
    # always outranks a partial-name match, which always outranks a
    # description-only match — the description hit only breaks ties *within*
    # a name tier. A weighted sum wouldn't hold this invariant (a
    # partial-name + description match could tie an exact-name-only match).
    NO_MATCH = (0, 0)

    def _score(name: str, desc: str) -> tuple[int, int]:
        """Relevance of a name+description pair to the query.

        `select:` is an exact-name lookup: (1, 0) named, (0, 0) not. Keyword
        search ranks name-token matches above description-only matches so a
        query naming a tool doesn't get outranked by an unrelated tool that
        happens to mention the word in its description (#526)."""
        if is_select:
            return (1, 0) if name in requested_names else NO_MATCH
        n = name.lower()
        if keyword == n:
            name_tier = 2  # exact name match — strongest signal
        elif keyword in n:
            name_tier = 1  # partial name match
        else:
            name_tier = 0
        desc_hit = 1 if keyword in desc.lower() else 0
        return (name_tier, desc_hit)

    # Skill descriptions keyed by name — built once so the deferred-pool
    # walk resolves an owning skill's description in O(1) rather than
    # re-scanning discovered_skills per matching tool.
    skill_desc_by_name = {s.name: s.description for s in discovered_skills}

    # 1. Match the skill catalog (name + description per skill). Track the
    #    best score seen per skill so a hidden-tool-name hit (step 2) can
    #    raise a skill already matched by its catalog entry.
    # name → (description, score)
    matched_skills: dict[str, tuple[str, tuple[int, int]]] = {}

    def _record_skill(
        skill_name: str, description: str, score: tuple[int, int]
    ) -> None:
        existing = matched_skills.get(skill_name)
        if existing is None or score > existing[1]:
            matched_skills[skill_name] = (description, score)

    for skill in discovered_skills:
        if skill.name in activated:
            continue
        score = _score(skill.name, skill.description)
        if score != NO_MATCH:
            _record_skill(skill.name, skill.description, score)

    # 2. Walk the deferred pool. Skill tools redirect to their owning
    #    skill; non-skill deferred tools (core demoted + MCP) get
    #    fetched into the active set as today.
    scored_tool_defs: list[tuple[tuple[int, int], dict]] = []
    for td in pool:
        name = td.get("function", {}).get("name", "")
        if not name:
            continue
        desc = get_description(td)
        owning_skill = skill_tool_owners.get(name)
        score = _score(name, desc)
        if score == NO_MATCH:
            continue
        if owning_skill:
            if owning_skill in activated:
                continue
            # Surface the skill, not the tool.
            _record_skill(owning_skill, skill_desc_by_name.get(owning_skill, ""), score)
        else:
            scored_tool_defs.append((score, td))

    # Order both result sets by score (descending), highest-signal first.
    # sorted() is stable, so ties keep skill-discovery / pool order.
    ranked_skills = sorted(
        matched_skills.items(), key=lambda kv: kv[1][1], reverse=True
    )
    ranked_tools = sorted(scored_tool_defs, key=lambda st: st[0], reverse=True)

    # Bound keyword-mode results across the combined output. Exact
    # `select:` queries return everything the user named. The budget is
    # score-aware across skills AND tools together, so a low-scoring
    # (e.g. description-only) skill can't consume the budget and evict a
    # higher-scoring tool. Skills win ties (kind_rank 0) — they're lighter
    # pointers the agent chooses to activate. Rendering still groups skills
    # then tools; only which matches survive the budget is combined.
    if not is_select and len(ranked_skills) + len(ranked_tools) > max_results:
        candidates = [
            (score, 0, ("skill", name)) for name, (_desc, score) in ranked_skills
        ] + [(score, 1, ("tool", i)) for i, (score, _td) in enumerate(ranked_tools)]
        candidates.sort(key=lambda c: c[1])  # tiebreak: skills before tools
        candidates.sort(key=lambda c: c[0], reverse=True)  # primary: score desc
        kept = {ref for _score, _kind, ref in candidates[:max_results]}
        ranked_skills = [rs for rs in ranked_skills if ("skill", rs[0]) in kept]
        ranked_tools = [rt for i, rt in enumerate(ranked_tools) if ("tool", i) in kept]

    fetched_tool_defs = [td for _score, td in ranked_tools]
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
