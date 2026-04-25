"""Tool-list assembly for the tool-choice eval.

Builds a fully-loaded tool definitions list — every core tool plus
every discovered skill's native tools — without the production
deferral or activation gating logic. The eval intentionally measures
description overlap under fair conditions, so every tool the model
could pick is in scope simultaneously.

MCP tools are excluded by default (deployment-specific noise);
``include_mcp=True`` opts them in by reading from the live MCP
registry.
"""

from __future__ import annotations

import logging

from ...skills import discover_skills
from ...tools import TOOL_DEFINITIONS as CORE_TOOL_DEFINITIONS
from ...tools.skill_tools import _load_native_tools

log = logging.getLogger(__name__)


def build_full_tool_loadout(config, *, include_mcp: bool = False) -> list[dict]:
    """Return the full tool definitions list for one eval run.

    The list is the concatenation of:
      - the core ``TOOL_DEFINITIONS`` exported from ``decafclaw.tools``
      - every discovered skill's ``TOOL_DEFINITIONS`` (loaded via the
        same dynamic-import path the production skill loader uses)
      - if ``include_mcp`` is true and an MCP registry is initialized,
        every MCP server's tool definitions

    Skills that fail to load are warned and skipped — one bad skill
    shouldn't take down the whole eval.
    """
    defs: list[dict] = list(CORE_TOOL_DEFINITIONS)

    for skill in discover_skills(config):
        if not skill.has_native_tools:
            continue
        try:
            _, tool_defs, _ = _load_native_tools(skill)
            defs.extend(tool_defs)
        except Exception as exc:
            log.warning(
                "Failed to load tools for skill '%s': %s", skill.name, exc,
            )

    if include_mcp:
        from ...mcp_client import get_registry
        registry = get_registry()
        if registry is not None:
            defs.extend(registry.get_tool_definitions())
    else:
        # Defensive filter — current core/skill TOOL_DEFINITIONS shouldn't
        # contain mcp__ entries, but a future skill could re-export them.
        defs = [
            d for d in defs
            if not d.get("function", {}).get("name", "").startswith("mcp__")
        ]

    return defs
