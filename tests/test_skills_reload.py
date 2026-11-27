"""Tests for re-activating skills and reloading their tool modules (split from #1419)."""

import pytest

from decafclaw.skills import SkillInfo
from decafclaw.tools.skill_tools import (
    SkillActivationResult,
    _compute_skill_hash,
    _save_permission,
    activate_skill_internal,
    tool_activate_skill,
    tool_skill_validate,
)
from tests.skill_helpers import _native_skill, _text, _two_tool_skill, _write_ws_skill

# -- re-activation reloads edited tools --
#
# Editing an active skill's tools.py used to be a guaranteed no-op:
# activate_skill early-returned "already active" without re-importing, and
# refresh_skills only rebuilt the catalog on `config` — never the live
# functions in ctx.tools.extra. So the author's edit-validate-reload loop
# could not converge, at all, and the only escape was restarting the process.


@pytest.mark.asyncio
async def test_reactivate_reloads_edited_tools(ctx, tmp_path):
    """After editing tools.py, re-activating picks up the new definition."""
    skill_dir = tmp_path / "editable"
    skill = _native_skill(
        skill_dir,
        tools_py=(
            "TOOLS = {'v1': lambda ctx: 'one'}\nTOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'v1'}}]\n"
        ),
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "editable", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="editable")
    assert "v1" in ctx.tools.extra

    # The author fixes their tool and renames it.
    (skill_dir / "tools.py").write_text(
        "TOOLS = {'v2': lambda ctx: 'two'}\nTOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'v2'}}]\n"
    )

    result = await tool_activate_skill(ctx, name="editable")
    assert "v2" in ctx.tools.extra, "edited tools.py was not reloaded"
    # The replaced tool must not linger — a stale name in ctx.tools.extra is
    # advertised to the LLM and calls dead code.
    assert "v1" not in ctx.tools.extra, "stale tool survived the reload"
    names = [td.get("function", {}).get("name") for td in ctx.tools.extra_definitions]
    assert names == ["v2"]
    assert "reload" in _text(result).lower()


@pytest.mark.asyncio
async def test_reactivate_reloads_changed_tool_body(ctx, tmp_path):
    """Same tool name, changed implementation — the new body must run."""
    skill_dir = tmp_path / "samename"
    skill = _native_skill(
        skill_dir,
        name="samename",
        tools_py=(
            "def go(ctx):\n    return 'before'\n\n"
            "TOOLS = {'go': go}\n"
            "TOOL_DEFINITIONS = [{'type': 'function', "
            "'function': {'name': 'go'}}]\n"
        ),
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "samename", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="samename")
    assert ctx.tools.extra["go"](ctx) == "before"

    (skill_dir / "tools.py").write_text(
        "def go(ctx):\n    return 'after'\n\n"
        "TOOLS = {'go': go}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'go'}}]\n"
    )
    await tool_activate_skill(ctx, name="samename")
    assert ctx.tools.extra["go"](ctx) == "after"


@pytest.mark.asyncio
async def test_reactivate_updates_dynamic_provider(ctx, tmp_path):
    """A skill exporting get_tools re-registers its provider on reload."""
    skill_dir = tmp_path / "dyn"
    skill = _native_skill(
        skill_dir,
        name="dyn",
        tools_py=(
            "TOOLS = {'d1': lambda ctx: 'x'}\n"
            "TOOL_DEFINITIONS = [{'type': 'function', "
            "'function': {'name': 'd1'}}]\n"
            "def get_tools(ctx):\n    return TOOLS, TOOL_DEFINITIONS\n"
        ),
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "dyn", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="dyn")
    first = ctx.tools.dynamic_providers["dyn"]

    (skill_dir / "tools.py").write_text(
        "TOOLS = {'d2': lambda ctx: 'y'}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'd2'}}]\n"
        "def get_tools(ctx):\n    return TOOLS, TOOL_DEFINITIONS\n"
    )
    await tool_activate_skill(ctx, name="dyn")
    assert ctx.tools.dynamic_providers["dyn"] is not first
    assert ctx.tools.dynamic_provider_names["dyn"] == {"d2"}


@pytest.mark.asyncio
async def test_reactivate_broken_edit_keeps_working_tools(ctx, tmp_path):
    """A reload that fails to import must report the error and leave the
    previously-working tools in place rather than half-unloading them."""
    skill_dir = tmp_path / "breakable"
    skill = _native_skill(
        skill_dir,
        name="breakable",
        tools_py=(
            "TOOLS = {'ok': lambda ctx: 'fine'}\n"
            "TOOL_DEFINITIONS = [{'type': 'function', "
            "'function': {'name': 'ok'}}]\n"
        ),
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "breakable", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="breakable")
    (skill_dir / "tools.py").write_text("def broken(\n")  # SyntaxError

    result = await tool_activate_skill(ctx, name="breakable")
    text = _text(result)
    assert "SyntaxError" in text
    assert "ok" in ctx.tools.extra, "working tool was dropped by a failed reload"


@pytest.mark.asyncio
async def test_activate_skill_internal_tool_load_failure_returns_error_result(ctx, tmp_path):
    """A skill whose tools.py fails to import surfaces the failure as a
    SkillActivationResult — not an exception, not a bare string — so callers
    branch on the type instead of inspecting ctx.skills.activated."""
    skill_dir = tmp_path / "broken"
    skill = _native_skill(skill_dir, name="broken", tools_py="def broken(\n")
    ctx.config.discovered_skills = [skill]

    result = await activate_skill_internal(ctx, skill)

    assert isinstance(result, SkillActivationResult), (
        f"expected a SkillActivationResult on tool-load failure, got {type(result).__name__}"
    )
    assert "SyntaxError" in result.text
    # The skill must NOT be marked active, and no tool registered.
    assert "broken" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_reactivate_same_size_edit_is_not_served_from_bytecode(ctx, tmp_path):
    """A same-size edit inside one second must still take effect.

    CPython validates a cached .pyc against source size and mtime truncated
    to whole seconds. Both writes here are byte-for-byte the same length and
    land in the same second, which is the precise shape that made
    exec_module replay the pre-edit bytecode — the edit looked like a no-op.
    """
    skill_dir = tmp_path / "samesize"
    before = "TOOLS = {'t': lambda ctx: 'aaa'}\nTOOL_DEFINITIONS = []\n"
    after = "TOOLS = {'t': lambda ctx: 'bbb'}\nTOOL_DEFINITIONS = []\n"
    assert len(before) == len(after), "test premise: writes must be the same size"

    skill = _native_skill(skill_dir, name="samesize", tools_py=before)
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "samesize", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="samesize")
    assert ctx.tools.extra["t"](ctx) == "aaa"

    (skill_dir / "tools.py").write_text(after)
    await tool_activate_skill(ctx, name="samesize")
    assert ctx.tools.extra["t"](ctx) == "bbb", "stale bytecode was executed"


@pytest.mark.asyncio
async def test_activation_writes_no_pycache_into_the_skill_dir(ctx, tmp_path):
    """Skill dirs live in the agent-writable workspace, and the agent cannot
    remove a __pycache__ directory with workspace_delete."""
    skill_dir = tmp_path / "clean"
    skill = _native_skill(
        skill_dir,
        name="clean",
        tools_py="TOOLS = {'c': lambda ctx: 'x'}\nTOOL_DEFINITIONS = []\n",
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "clean", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="clean")
    assert not (skill_dir / "__pycache__").exists()


def test_skill_validate_sees_same_size_edit(ctx):
    """The validator must not report a SyntaxError the author already fixed."""
    d = _write_ws_skill(
        ctx,
        "fixme",
        "name: fixme\ndescription: Being fixed.",
        tools_py="def get_tools(ctx)\n    return {}, []\n",  # missing colon
    )
    first = tool_skill_validate(ctx, path="skills/fixme")
    assert first.data["ok"] is False

    # Add the colon, drop a space: same length, same second.
    (d / "tools.py").write_text("def get_tools(ctx):\n    return {},[]\n")
    second = tool_skill_validate(ctx, path="skills/fixme")
    assert second.data["ok"] is True, "validator judged stale bytecode"


@pytest.mark.asyncio
async def test_reactivate_text_only_skill_is_idempotent(ctx, tmp_path):
    """A skill with no tools.py still reports already-active without error."""
    skill = SkillInfo(
        name="texty",
        description="No tools.",
        location=tmp_path / "texty",
        body="Just prose.",
        has_native_tools=False,
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "texty", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="texty")
    result = await tool_activate_skill(ctx, name="texty")
    assert "already active" in _text(result).lower()


@pytest.mark.asyncio
async def test_reactivate_does_not_duplicate_mismatched_definition_names(ctx, tmp_path):
    """A TOOLS key and its TOOL_DEFINITIONS function.name need not match.

    Retracting by TOOLS keys while filtering definitions by function.name
    leaves the old definition behind, and the reload appends a second copy.
    Providers reject duplicate function declarations outright — Vertex answers
    `400 Duplicate function declaration found: <name>` at the provider call,
    before any tool runs, so the conversation just breaks (#684).
    """
    skill_dir = tmp_path / "mismatch"
    src = (
        "def fn(ctx):\n    return 'x'\n\n"
        "TOOLS = {'internal_key': fn}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', "
        "'function': {'name': 'weather_fetch'}}]\n"
    )
    skill = _native_skill(skill_dir, name="mismatch", tools_py=src)
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "mismatch", {"status": "always", "hash": _compute_skill_hash(skill)})

    await tool_activate_skill(ctx, name="mismatch")
    await tool_activate_skill(ctx, name="mismatch")  # reload, same source

    names = [td.get("function", {}).get("name") for td in ctx.tools.extra_definitions]
    assert names.count("weather_fetch") == 1, f"duplicate declaration: {names}"


@pytest.mark.asyncio
async def test_reload_after_restore_still_retracts(ctx, tmp_path):
    """restore_skills is the post-restart registration path. If it doesn't
    record what it registered, the first reload afterwards can't retract and
    the old tool name lingers."""
    from decafclaw.tools.skill_tools import restore_skills

    skill_dir = tmp_path / "restored"
    skill = _two_tool_skill(skill_dir, "restored", "old_name", "v1")
    ctx.config.discovered_skills = [skill]
    ctx.skills.activated["restored"] = _compute_skill_hash(skill)
    await restore_skills(ctx)
    assert "old_name" in ctx.tools.extra

    (skill_dir / "tools.py").write_text(
        "def fn(ctx):\n    return 'v2'\n"
        "TOOLS = {'new_name': fn}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'new_name'}}]\n"
    )
    _save_permission(ctx.config, "restored", {"status": "always", "hash": _compute_skill_hash(skill)})
    await tool_activate_skill(ctx, name="restored")

    assert "new_name" in ctx.tools.extra
    assert "old_name" not in ctx.tools.extra, "restore path left an un-retractable tool"


def test_skill_loader_dataclass_with_future_annotations(tmp_path):
    """A skill whose tools.py uses `from __future__ import annotations` and a
    @dataclass loads successfully without raising AttributeError."""
    from decafclaw.skills import build_skill_info, build_skill_tool_owners, validate_skill_md

    skill_dir = tmp_path / "dc_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: dc_skill\ndescription: Dataclass skill.\n---\nBody.")
    (skill_dir / "tools.py").write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class MyData:\n"
        "    val: str\n"
        "\n"
        "def sample_tool(ctx, item: MyData):\n"
        "    return item.val\n"
        "\n"
        "TOOLS = {'sample_tool': sample_tool}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'sample_tool'}}]\n"
    )
    res = validate_skill_md(skill_dir / "SKILL.md")
    info = build_skill_info(res)
    info.trust_tier = "bundled"
    owners = build_skill_tool_owners([info])
    assert "sample_tool" in owners


def test_skill_loader_sys_modules_replacement(tmp_path):
    import sys

    from decafclaw.skills import build_skill_info, validate_skill_md
    from decafclaw.tools.skill_tools import _import_tools_module

    skill_dir = tmp_path / "reload_skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: reload_skill\ndescription: Reload skill.\n---\nBody.")
    tools_path = skill_dir / "tools.py"
    tools_path.write_text("TOOLS = {}\nTOOL_DEFINITIONS = []\n")

    res = validate_skill_md(skill_dir / "SKILL.md")
    info = build_skill_info(res)
    mod_name = f"decafclaw_skill_{info.name}"
    m1 = _import_tools_module(mod_name, tools_path)
    m2 = _import_tools_module(mod_name, tools_path)
    assert m1 is not m2
    assert sys.modules.get(mod_name) is m2


def test_skill_loader_failed_reload_preserves_previous_module(tmp_path):
    import sys

    from decafclaw.tools.skill_tools import _import_tools_module

    skill_dir = tmp_path / "fail_reload_skill"
    skill_dir.mkdir()
    tools_path = skill_dir / "tools.py"
    tools_path.write_text("VALID_VAR = 'original'\n")

    mod_name = "decafclaw_skill_fail_reload"
    m1 = _import_tools_module(mod_name, tools_path)
    assert sys.modules.get(mod_name) is m1
    assert m1.VALID_VAR == "original"

    # Now write broken code (syntax error) for reload
    tools_path.write_text("def broken(\n")
    with pytest.raises(SyntaxError):
        _import_tools_module(mod_name, tools_path)

    # The previous valid module should be preserved in sys.modules
    assert sys.modules.get(mod_name) is m1
    assert sys.modules.get(mod_name).VALID_VAR == "original"
