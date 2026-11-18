"""Pre-emptive tool and skill matching (#1257, #604, #1491).

Keyword-matches the raw user message against deferred tool schemas and the
discovered skill catalog, populating ``ctx.tools.preempt_matches`` /
``ctx.skills.preempt_matches`` and rendering the archived discovery hints.
Only the user's own input is evaluated (#1049) so retrieved context cannot
induce irrelevant promotions.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING

from .context_types import ComposerMode, SourceEntry
from .preempt_search import match_tools, tokenize
from .tool_definitions import collect_all_tool_defs
from .tools.tool_registry import get_critical_names, get_fetched_tools
from .util import estimate_tokens

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

# Visible-hint trimming for skill matches (#604): keep matches within this
# score gap of the top hit (and at least the minimum score).
_HINT_SCORE_GAP = 1
_HINT_MIN_SCORE = 2


def compose_preempt_matches(ctx: "Context", config, user_message: str, mode: ComposerMode) -> SourceEntry | None:
    """Run pre-emptive keyword match, populate ``ctx.tools.preempt_matches``.

    Matches the raw user message against tool names + descriptions.
    Deferred matches become archived tail hints after classification.
    Native schemas stay stable until an explicit fetch or capability
    change. See docs/preemptive-tool-search.md and #1257.

    Only the user's own input is evaluated (#1049): injected memory /
    wiki / journal context and prior assistant responses are excluded
    so background retrieval can't induce irrelevant promotions.

    Returns a ``SourceEntry`` for the diagnostics sidecar, or None if
    matching is disabled or produced no results.

    The ``mode`` parameter is accepted for API symmetry with other
    composer methods and to reserve space for future mode-specific
    gating. Currently all composer-driven modes apply matching
    (reflection and compaction bypass the composer entirely).
    """
    _ = mode  # reserved for future per-mode gating

    # Reset for this turn. The field lives on ctx.tools (a shared
    # sub-object on the per-turn context), so it's fresh per turn
    # but stable across agent iterations within the turn.
    ctx.tools.preempt_matches = set()

    cfg = config.agent.preemptive_search
    if not cfg.enabled:
        return None

    # Match input is the raw user message only — no history, no
    # injected context (#1049).
    input_tokens = tokenize(user_message)
    if not input_tokens:
        return None

    # Candidate pool: all tools not already force-critical.
    # Already-force-critical names come from the same sources
    # classify_tools uses: env override + always-loaded skills (via
    # get_critical_names), fetched (via get_fetched_tools), and
    # activated skill tools (from ctx.tools.extra_definitions).
    # Plus declared priority: "critical" on the tool def itself.
    always_loaded = getattr(config, "always_loaded_skill_tools", set()) or set()
    on_demand = {td.get("function", {}).get("name", "") for td in ctx.tools.extra_definitions} - always_loaded
    already_critical: set[str] = get_critical_names(config)
    already_critical |= get_fetched_tools(ctx)
    already_critical |= on_demand

    all_defs = collect_all_tool_defs(ctx)
    # If the context restricts the usable tool set (eval runner,
    # restricted child agents), don't surface disallowed tools —
    # they'd waste the match cap and error at execute_tool.
    allowed = ctx.tools.allowed
    candidates: list[dict] = []
    for td in all_defs:
        name = td.get("function", {}).get("name", "")
        if not name or name in already_critical:
            continue
        # Respect declared critical priority — those are already in.
        if td.get("priority") == "critical":
            continue
        if allowed is not None and name not in allowed:
            continue
        if name in (ctx.tools.disallowed or set()):
            continue
        candidates.append(td)

    matches = match_tools(input_tokens, candidates, cfg.max_matches)
    if not matches:
        return None

    matched_names = {m["name"] for m in matches}
    ctx.tools.preempt_matches = matched_names

    log.info(
        "preemptive match: suggested %d tool(s) for conv %s: %s",
        len(matches),
        (ctx.conv_id or ctx.context_id)[:12],
        ", ".join(f"{m['name']}({m['score']})" for m in matches),
    )

    # The rendered hint cost is filled after classification, which
    # determines which matches actually need a discovery hint.
    return SourceEntry(
        source="preempt_matches",
        tokens_estimated=0,
        items_included=len(matches),
        details={
            "input_tokens": sorted(input_tokens),
            "matches": matches,
            "max_matches": cfg.max_matches,
        },
    )


def compose_preempt_skill_matches(
    ctx: "Context", config, user_message: str, mode: ComposerMode
) -> tuple[SourceEntry | None, str | None]:
    """Pre-emptive keyword match against the discovered skill catalog.

    Parallel to ``_compose_preempt_matches`` but for skills instead of
    tools. Skills already in ``ctx.skills.activated`` (which includes
    always-loaded bundled skills auto-activated at turn start) are
    excluded from the candidate pool — surfacing them would just be
    noise. Matches are rendered as a short system-message hint so the
    agent considers ``activate_skill`` without first calling a tool
    that hasn't been loaded yet.

    Only the raw user message is evaluated (#1049) — injected memory /
    wiki / journal context and prior assistant responses are excluded
    so background retrieval can't induce irrelevant skill hints.

    Returns ``(source_entry, hint_text)``. Both ``None`` if matching
    is disabled, the message tokenizes to nothing, or no skills match.
    """
    _ = mode  # reserved for future per-mode gating

    ctx.skills.preempt_matches = set()

    cfg = config.agent.preemptive_search
    if not cfg.enabled or cfg.max_matches <= 0:
        return None, None

    # Match input is the raw user message only — no history, no
    # injected context (#1049).
    input_tokens = tokenize(user_message)
    if not input_tokens:
        return None, None

    discovered = config.discovered_skills or []
    candidates = [s for s in discovered if s.name and s.name not in ctx.skills.activated]
    if not candidates:
        return None, None

    scored: list[dict] = []
    for skill in candidates:
        skill_tokens = tokenize(f"{skill.name} {skill.description}")
        overlap = input_tokens & skill_tokens
        if overlap:
            scored.append(
                {
                    "name": skill.name,
                    "score": len(overlap),
                    "matched_tokens": sorted(overlap),
                }
            )

    if not scored:
        return None, None

    scored.sort(key=lambda e: (-e["score"], e["name"]))
    top = scored[: cfg.max_matches]
    matched_names = {e["name"] for e in top}
    # Promote ALL top matches for tool loading — the hint trim below only
    # affects the visible text, not which skills classify_tools can pull in.
    ctx.skills.preempt_matches = matched_names

    # Trim the *visible* hint to skills within a small gap of the top
    # scorer so a dense pile of weak matches doesn't dilute the routing
    # signal (#604). Always keep at least one so the hint is never empty.
    top_score = top[0]["score"]
    threshold = max(_HINT_MIN_SCORE, top_score - _HINT_SCORE_GAP)
    hinted = [e for e in top if e["score"] >= threshold] or top[:1]
    hinted_names = {e["name"] for e in hinted}

    # Sanitize skill names before interpolating — names come from
    # SKILL.md frontmatter, which workspace/admin skills control.
    # Collapse whitespace (incl. newlines) to a single space so a name
    # can't inject extra hint lines/directives, then html-escape so an
    # `</preempt_skill_hint>` (or other markup) can't break out of the
    # wrapper and inject prompt content.
    safe_names = sorted(html.escape(" ".join(name.split()), quote=False) for name in hinted_names)
    bullets = "\n".join(f"- {n}" for n in safe_names)
    hint_text = (
        "<preempt_skill_hint>\n"
        "These skills look relevant to the current message. "
        "Call activate_skill(name) to load their tools.\n\n"
        f"{bullets}\n"
        "</preempt_skill_hint>"
    )

    log.info(
        "preemptive skill match: promoted %d skill(s), hinted %d, for conv %s: %s",
        len(top),
        len(hinted),
        (ctx.conv_id or ctx.context_id)[:12],
        ", ".join(f"{e['name']}({e['score']}{'*' if e['name'] in hinted_names else ''})" for e in top),
    )

    entry = SourceEntry(
        source="preempt_skill_matches",
        tokens_estimated=estimate_tokens(hint_text),
        items_included=len(hinted),  # the entry represents the hint text
        details={
            "input_tokens": sorted(input_tokens),
            "matches": top,  # full promoted set (feeds tool promotion)
            "hinted": hinted,  # subset actually named in the hint
            "promoted_count": len(top),
            "hinted_count": len(hinted),
            "max_matches": cfg.max_matches,
        },
    )
    return entry, hint_text


def build_preempt_tool_hint(
    ctx: "Context", config, deferred_tools: list[dict], entry: SourceEntry | None
) -> str | None:
    """Render and cost the deferred-tool discovery hint for matched tools."""
    if entry is None:
        return None
    deferred_names = {t["function"]["name"] for t in deferred_tools}
    owners = config.skill_tool_owners
    suggested = sorted(ctx.tools.preempt_matches & deferred_names - owners.keys())
    tool_hint_text = None
    if suggested:
        safe_names = [html.escape(" ".join(name.split()), quote=False) for name in suggested]
        tool_hint_text = (
            "<preempt_tool_hint>\n"
            "These deferred tools may help. Use tool_search to load them before calling them:\n"
            + "\n".join(f"- {name}" for name in safe_names)
            + "\n</preempt_tool_hint>"
        )
    entry.tokens_estimated = estimate_tokens(tool_hint_text) if tool_hint_text else 0
    entry.items_included = len(suggested)
    entry.details["suggested"] = suggested
    return tool_hint_text
