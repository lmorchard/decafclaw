"""Tool-registry assembly for the agent loop.

Given a ctx, gather all available tool definitions (core + activated
skills + bundled-skill native tools + MCP + extra), classify them into
active vs deferred per the tool-priority budget, and produce the
per-iteration tool list the LLM call sees.

Consumed by `agent.py` (every iteration of the agent loop) and
`context_composer.py` (turn-start budget accounting). Kept separate
from `tool_execution.py` so composer can import definitions without
pulling in execution internals.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .skills import grants_capability
from .tools import TOOL_DEFINITIONS
from .tools.search_tools import SEARCH_TOOL_DEFINITIONS
from .tools.tool_registry import (
    build_deferred_list_text,
    build_tool_guidance_text,
    classify_tools,
    get_fetched_tools,
)

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Cache preloaded skill definitions by config id, avoiding Config mutation.
# Cleared by `invalidate_skill_cache` after `refresh_skills`.
_skill_def_cache: dict[int, list] = {}


def invalidate_skill_cache(config) -> None:
    """Clear the cached skill definitions for a config. Call after refresh_skills."""
    _skill_def_cache.pop(id(config), None)


def refresh_dynamic_tools(ctx: "Context") -> None:
    """Call dynamic tool providers to refresh skill tools for this turn.

    Skills that export get_tools(ctx) have their tools and definitions
    replaced each turn based on current state (e.g., project phase).
    Collects all possible tool names from providers, removes stale entries,
    then re-adds the current set.
    """
    providers = ctx.tools.dynamic_providers
    if not providers:
        return

    # Collect names from previous turn + this turn so we can remove stale entries
    names_to_remove: set[str] = set()
    for skill_name in providers:
        names_to_remove.update(ctx.tools.dynamic_provider_names.get(skill_name, set()))

    # Call each provider for this turn's tools
    provider_results: list[tuple[str, dict, list]] = []
    for skill_name, get_tools_fn in providers.items():
        try:
            tools, tool_defs = get_tools_fn(ctx)
            names_to_remove.update(tools.keys())
            ctx.tools.dynamic_provider_names[skill_name] = set(tools.keys())
            ctx.tools.dynamic_provider_definitions[skill_name] = list(tool_defs)
            provider_results.append((skill_name, tools, tool_defs))
        except Exception as e:
            # Fail-open: remove this provider's stale tools. If the model
            # tries to call a removed tool, it gets a "tool not found" error.
            log.warning(f"Dynamic tool provider for '{skill_name}' failed: {e}")
            ctx.tools.dynamic_provider_names[skill_name] = set()
            ctx.tools.dynamic_provider_definitions[skill_name] = []

    # Remove all dynamic-provider tools (old + new names) from extra
    ctx.tools.extra = {name: fn for name, fn in ctx.tools.extra.items() if name not in names_to_remove}
    ctx.tools.extra_definitions = [
        td for td in ctx.tools.extra_definitions if td.get("function", {}).get("name") not in names_to_remove
    ]

    # Re-add the current turn's tools from each provider
    for skill_name, tools, tool_defs in provider_results:
        ctx.tools.extra.update(tools)
        ctx.tools.extra_definitions.extend(tool_defs)


def _preloaded_skill_defs(ctx: "Context") -> list:
    """Tool definitions from capability-tier skills, pre-loaded for a stable tool list.

    Cached by config id to avoid re-executing tools.py every iteration.
    """
    config_id = id(ctx.config)
    _cached = _skill_def_cache.get(config_id)
    if _cached is None:
        _cached = []
        for skill_info in ctx.config.discovered_skills:
            # Capability tier only: `_load_native_tools` imports tools.py,
            # which execs its module-level code, and `workspace/skills/` is
            # agent-writable. Activation gates that behind a confirmation
            # (#649); pre-loading here would front-run it (#744).
            if skill_info.has_native_tools and grants_capability(skill_info):
                try:
                    from .tools.skill_tools import _load_native_tools

                    _, tool_defs, _ = _load_native_tools(skill_info)
                    _cached.extend(tool_defs)
                except Exception as e:
                    log.warning(f"Failed to pre-load skill '{skill_info.name}' tools: {e}")
        _skill_def_cache[config_id] = _cached
    return _cached


def collect_all_tool_defs(ctx: "Context") -> list:
    """Gather all available tool definitions (core + skill + MCP + extra).

    Does NOT apply allowed_tools filter — returns the full unfiltered set
    so classification can see everything before deciding what to defer.
    """
    # Skill tools first — activated skill tools get priority positioning
    # so the model sees them before the long tail of core tools
    all_tools = list(ctx.tools.extra_definitions) + list(TOOL_DEFINITIONS)

    preloaded_names = {t.get("function", {}).get("name") for t in all_tools}
    for td in _preloaded_skill_defs(ctx):
        name = td.get("function", {}).get("name")
        if name and name not in preloaded_names:
            all_tools.append(td)
            preloaded_names.add(name)

    from .mcp_client import get_registry

    mcp_registry = get_registry()
    if mcp_registry:
        all_tools = all_tools + mcp_registry.get_tool_definitions()

    return _dedupe_by_name(all_tools)


def _dedupe_by_name(tool_defs: list) -> list:
    """Drop later declarations of an already-declared function name.

    Providers reject a request carrying the same function name twice outright
    — Vertex answers 400 "Duplicate function declaration found: X" — and that
    lands at the provider call, before any tool runs. The agent never sees a
    tool result, so it cannot diagnose or recover; from inside the
    conversation the model simply appears to break (#684).

    The sources concatenated above are not disjoint. An activated skill can
    export a tool named for a core one (the agent may author workspace skills,
    and the names it reaches for first are the ones already taken), and
    nothing upstream prevents it.

    Keep-FIRST is the required tiebreak, not an arbitrary one. Skill
    definitions are positioned first, and ``execute_tool`` checks
    ``ctx.tools.extra`` before the global registry — so keeping the first
    declaration means the schema the model is shown belongs to the same
    implementation that will run. Keep-last would leave the two silently
    disagreeing, which is worse than the crash it replaces.
    """
    seen: set[str] = set()
    deduped = []
    for td in tool_defs:
        name = td.get("function", {}).get("name")
        if not name:
            # Malformed entries aren't this function's business — pass them
            # through so whatever validates shape still gets to complain.
            deduped.append(td)
            continue
        if name in seen:
            log.warning(
                "Dropping duplicate declaration of tool %r — an earlier "
                "definition of that name wins (skill tools shadow core "
                "tools, matching execute_tool's dispatch order).",
                name,
            )
            continue
        seen.add(name)
        deduped.append(td)
    return deduped


def collect_trusted_tool_guidelines(ctx: "Context") -> dict[str, list[str]]:
    """Map tool name -> ``prompt_guidelines`` from the definitions trusted owners declared.

    Guidance is attributed by provenance, never by reading the active
    definition for a name: an activated workspace skill may declare a tool
    named like a core or trusted-skill tool, and its definition then wins
    ``_dedupe_by_name``. Reading guidance from the owner's own definition
    keeps agent-authored text out of the ``<tool_guidance>`` system block.

    Precedence: core tools, then trusted skills' dynamic providers (this
    turn's ``get_tools`` output), then what trusted skills registered on
    activation (``skill_contributions`` — current after a reload, and carries
    names that activation renamed to dodge a collision), then the pre-loaded
    static definitions for trusted skills that aren't activated.
    """
    guidelines: dict[str, list[str]] = {}

    def _add(defs: list) -> None:
        for td in defs:
            name = td.get("function", {}).get("name")
            if name and name not in guidelines:
                guidelines[name] = td.get("prompt_guidelines") or []

    _add(TOOL_DEFINITIONS)
    skills_by_name = {s.name: s for s in ctx.config.discovered_skills}

    def _trusted(skill_name: str) -> bool:
        info = skills_by_name.get(skill_name)
        return info is not None and grants_capability(info)

    for skill_name in ctx.tools.dynamic_providers:
        if _trusted(skill_name):
            _add(ctx.tools.dynamic_provider_definitions.get(skill_name, []))
    for skill_name, (_, tool_defs) in ctx.tools.skill_contributions.items():
        if _trusted(skill_name):
            _add(tool_defs)
    _add(_preloaded_skill_defs(ctx))
    return guidelines


def build_tool_list(ctx: "Context") -> tuple[list, str | None, str | None]:
    """Build the tool list, with optional deferred mode and tool-owned guidance.

    Returns (tool_definitions, deferred_text, guidance_text) where:
    - `deferred_text` is None if all tools fit in budget, or a system prompt block
      listing deferred tools when budget is exceeded.
    - `guidance_text` is None if no active tool defines guidelines, or a
      <tool_guidance> system prompt block.
    """
    all_defs = collect_all_tool_defs(ctx)
    fetched = get_fetched_tools(ctx)
    # Skill tools (from activated skills) should never be deferred
    skill_tool_names = {td.get("function", {}).get("name", "") for td in ctx.tools.extra_definitions}
    # Pre-emptive matches populated by ContextComposer at turn start;
    # reused across iterations so mid-turn reclassification stays consistent.
    from .modes import resolve_active_mode

    active_mode_name = getattr(ctx, "active_mode", "default") or "default"
    active_mode = resolve_active_mode(ctx.config, active_mode_name)
    active, deferred = classify_tools(
        all_defs,
        ctx.config,
        fetched,
        skill_tool_names,
        preempt_matches=ctx.tools.preempt_matches,
        promoted_tools=active_mode.promoted_tools,
    )

    # Apply allowed_tools / disallowed_tools filters
    allowed = ctx.tools.allowed
    disallowed = ctx.tools.disallowed or set()

    if allowed is not None:
        active = [t for t in active if t.get("function", {}).get("name") in allowed]
        deferred = [t for t in deferred if t.get("function", {}).get("name") in allowed]

    if disallowed:
        active = [t for t in active if t.get("function", {}).get("name") not in disallowed]
        deferred = [t for t in deferred if t.get("function", {}).get("name") not in disallowed]

    trusted_guidelines = collect_trusted_tool_guidelines(ctx)

    if not deferred:
        return active, None, build_tool_guidance_text(active, trusted_guidelines)

    # Deferred mode: set the pool on ctx and add tool_search
    ctx.tools.deferred_pool = deferred
    active = active + SEARCH_TOOL_DEFINITIONS

    # Build deferred list text for system prompt
    core_names = {td.get("function", {}).get("name", "") for td in TOOL_DEFINITIONS}
    deferred_text = build_deferred_list_text(deferred, core_names=core_names)
    guidance_text = build_tool_guidance_text(active, trusted_guidelines)

    return active, deferred_text, guidance_text
