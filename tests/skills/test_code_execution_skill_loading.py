"""Skill-loading tests for the `code_execution` bundled skill.

Verifies the integration with the skill loader:
  - discovery from `src/decafclaw/skills/`
  - always-loaded classification (auto-activated, exempt from deferral)
  - `init()` applies `config.skills["code_execution"]` overrides
  - the registered tool is force-promoted to `critical` in `build_tool_list`
"""

import pytest

from decafclaw.skills import discover_skills
from decafclaw.tool_definitions import build_tool_list, invalidate_skill_cache
from decafclaw.tools.skill_tools import activate_skill_internal


def test_code_execution_skill_is_discovered(config):
    """Bundled discovery surfaces the `code_execution` skill."""
    skills = discover_skills(config)
    names = {s.name for s in skills}
    assert "code_execution" in names, (
        f"`code_execution` not in discovered bundled skills: {sorted(names)}"
    )


def test_code_execution_skill_is_always_loaded(config):
    """The bundled `code_execution` skill is classified as always-loaded."""
    skills = discover_skills(config)
    always_loaded = {s.name for s in skills if s.always_loaded}
    assert "code_execution" in always_loaded, (
        f"`code_execution` missing from always-loaded set: {sorted(always_loaded)}"
    )


@pytest.mark.asyncio
async def test_code_execution_init_applies_config_skills_override(ctx):
    """Activating the skill runs init() and overlays config.skills[name] via
    load_sub_config. This proves init() actually executed — a default-only
    assertion would also pass if init() were never called, since the module
    declares `_settings = SkillConfig()` at import time."""
    skills = discover_skills(ctx.config)
    info = next((s for s in skills if s.name == "code_execution"), None)
    assert info is not None
    ctx.config.discovered_skills = skills

    # Overlay a non-default value via config.skills before activation; this is
    # the same path load_sub_config uses in production.
    ctx.config.skills["code_execution"] = {"max_tool_calls": 7}

    await activate_skill_internal(ctx, info)

    # The skill loader imports tools.py via importlib.spec_from_file_location,
    # which is a separate module instance from `decafclaw.skills.code_execution
    # .tools` and is not registered in sys.modules. init() mutates the loader's
    # instance, so we reach it via the registered tool function's __globals__
    # (which IS that module's namespace).
    tool_fn = ctx.tools.extra["code_execution"]
    settings = tool_fn.__globals__["_settings"]
    assert settings.max_tool_calls == 7, (
        f"init() did not apply config.skills override: "
        f"got max_tool_calls={settings.max_tool_calls!r}, expected 7"
    )


@pytest.mark.asyncio
async def test_code_execution_tool_is_critical_in_build_tool_list(ctx):
    """After activation, `code_execution` is in the active tool list
    and is force-promoted to critical via the always-loaded path."""
    skills = discover_skills(ctx.config)
    info = next((s for s in skills if s.name == "code_execution"), None)
    assert info is not None
    ctx.config.discovered_skills = skills

    await activate_skill_internal(ctx, info)
    invalidate_skill_cache(ctx.config)

    active, _deferred = build_tool_list(ctx)
    names = {td.get("function", {}).get("name") for td in active}
    assert "code_execution" in names, (
        f"`code_execution` missing from active tool list: {sorted(names)}"
    )
    assert "code_execution" in ctx.config.always_loaded_skill_tools, (
        "activation did not register `code_execution` in "
        "always_loaded_skill_tools (force-critical set)"
    )
