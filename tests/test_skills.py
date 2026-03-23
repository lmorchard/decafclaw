"""Tests for the skills system — parsing, discovery, catalog, and activation."""

import json
from pathlib import Path

import pytest

from decafclaw.media import ToolResult
from decafclaw.skills import SkillInfo, build_catalog_text, discover_skills, parse_skill_md
from decafclaw.tools.skill_tools import (
    _load_permissions,
    _save_permission,
    tool_activate_skill,
)


def _write_skill(skill_dir, frontmatter, body="Some instructions.", tools_py=False):
    """Helper to create a SKILL.md (and optionally tools.py) in a directory."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}")
    if tools_py:
        (skill_dir / "tools.py").write_text("TOOLS = {}\nTOOL_DEFINITIONS = []")


# -- parse_skill_md tests --



def _text(result):
    """Extract text from str or ToolResult."""
    return result.text if isinstance(result, ToolResult) else result


def test_parse_valid_skill_md(tmp_path):
    skill_dir = tmp_path / "my-skill"
    _write_skill(skill_dir, "name: my-skill\ndescription: Does stuff.")
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.name == "my-skill"
    assert info.description == "Does stuff."
    assert info.body == "Some instructions."
    assert info.has_native_tools is False
    assert info.requires_env == []
    assert info.user_invocable is True


def test_parse_all_fields(tmp_path):
    skill_dir = tmp_path / "full-skill"
    frontmatter = (
        "name: full-skill\n"
        "description: Full featured skill.\n"
        "user-invocable: false\n"
        "requires:\n"
        "  env:\n"
        "    - API_KEY\n"
        "    - SECRET"
    )
    _write_skill(skill_dir, frontmatter, tools_py=True)
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.name == "full-skill"
    assert info.has_native_tools is True
    assert info.requires_env == ["API_KEY", "SECRET"]
    assert info.user_invocable is False


def test_parse_minimal_skill_md(tmp_path):
    skill_dir = tmp_path / "minimal"
    _write_skill(skill_dir, "name: minimal\ndescription: Bare minimum.")
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.name == "minimal"


def test_parse_missing_description(tmp_path):
    skill_dir = tmp_path / "no-desc"
    _write_skill(skill_dir, "name: no-desc")
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is None


def test_parse_missing_name(tmp_path):
    skill_dir = tmp_path / "no-name"
    _write_skill(skill_dir, "description: No name here.")
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is None


def test_parse_unparseable_yaml(tmp_path):
    skill_dir = tmp_path / "bad-yaml"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\n: : : invalid\n---\nBody.")
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is None


def test_parse_detects_native_tools(tmp_path):
    skill_dir = tmp_path / "native"
    _write_skill(skill_dir, "name: native\ndescription: Has tools.", tools_py=True)
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.has_native_tools is True


def test_parse_no_frontmatter(tmp_path):
    skill_dir = tmp_path / "no-fm"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Just some markdown, no frontmatter.")
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is None


def test_parse_command_frontmatter(tmp_path):
    """Parse allowed-tools, context, argument-hint from frontmatter."""
    skill_dir = tmp_path / "migrate-todos"
    _write_skill(
        skill_dir,
        'name: migrate-todos\ndescription: "Migrate todos"\n'
        'allowed-tools: vault_read, vault_write, shell\n'
        'context: fork\n'
        'argument-hint: "[date]"',
        body="Do the migration. $ARGUMENTS",
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.allowed_tools == ["vault_read", "vault_write", "shell"]
    assert info.context == "fork"
    assert info.argument_hint == "[date]"


def test_parse_command_frontmatter_defaults(tmp_path):
    """New frontmatter fields have sensible defaults."""
    skill_dir = tmp_path / "basic"
    _write_skill(skill_dir, 'name: basic\ndescription: "A basic skill"')
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.allowed_tools == []
    assert info.context == "inline"
    assert info.argument_hint == ""
    assert info.requires_skills == []


def test_parse_required_skills(tmp_path):
    """Parse required-skills list from frontmatter."""
    skill_dir = tmp_path / "migrate"
    _write_skill(
        skill_dir,
        'name: migrate\ndescription: "Migrate"\n'
        'context: fork\n'
        'required-skills:\n  - markdown_vault\n  - tabstack',
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.requires_skills == ["markdown_vault", "tabstack"]


# -- find_command / list_commands tests --


def test_find_command():
    from decafclaw.skills import find_command
    skills = [
        SkillInfo(name="weather", description="Weather", location=Path("."), user_invocable=True),
        SkillInfo(name="tabstack", description="Tabstack", location=Path("."), user_invocable=False),
        SkillInfo(name="migrate", description="Migrate", location=Path("."), user_invocable=True),
    ]
    assert find_command("weather", skills) is not None
    assert find_command("weather", skills).name == "weather"
    assert find_command("tabstack", skills) is None  # not user_invocable
    assert find_command("nonexistent", skills) is None


def test_list_commands():
    from decafclaw.skills import list_commands
    skills = [
        SkillInfo(name="weather", description="Weather", location=Path("."), user_invocable=True),
        SkillInfo(name="tabstack", description="Tabstack", location=Path("."), user_invocable=False),
        SkillInfo(name="migrate", description="Migrate", location=Path("."), user_invocable=True),
    ]
    cmds = list_commands(skills)
    assert len(cmds) == 2
    assert cmds[0].name == "migrate"  # sorted
    assert cmds[1].name == "weather"


# -- discover_skills tests --


def test_discover_from_single_dir(tmp_path, config):
    """Discovers skills from the workspace skills directory."""
    skills_dir = config.workspace_path / "skills"
    _write_skill(skills_dir / "alpha", "name: alpha\ndescription: Alpha skill.")
    _write_skill(skills_dir / "beta", "name: beta\ndescription: Beta skill.")

    skills = discover_skills(config)
    names = [s.name for s in skills]
    assert "alpha" in names
    assert "beta" in names


def test_discover_priority_ordering(tmp_path, config):
    """Workspace skill shadows agent-level skill with same name."""
    # Create same-named skill in both locations
    ws_skills = config.workspace_path / "skills"
    _write_skill(ws_skills / "dupe", "name: dupe\ndescription: Workspace version.")

    agent_skills = config.agent_path / "skills"
    _write_skill(agent_skills / "dupe", "name: dupe\ndescription: Agent version.")

    skills = discover_skills(config)
    dupe_skills = [s for s in skills if s.name == "dupe"]
    assert len(dupe_skills) == 1
    assert dupe_skills[0].description == "Workspace version."


def test_discover_skips_unmet_requires(tmp_path, config, monkeypatch):
    """Skills with unmet requires.env are not included."""
    monkeypatch.delenv("MISSING_VAR", raising=False)
    skills_dir = config.workspace_path / "skills"
    _write_skill(
        skills_dir / "needs-var",
        "name: needs-var\ndescription: Needs env.\nrequires:\n  env:\n    - MISSING_VAR",
    )

    skills = discover_skills(config)
    assert not any(s.name == "needs-var" for s in skills)


def test_discover_includes_when_requires_met(tmp_path, config, monkeypatch):
    """Skills are included when requires.env is satisfied."""
    monkeypatch.setenv("PRESENT_VAR", "value")
    skills_dir = config.workspace_path / "skills"
    _write_skill(
        skills_dir / "has-var",
        "name: has-var\ndescription: Has env.\nrequires:\n  env:\n    - PRESENT_VAR",
    )

    skills = discover_skills(config)
    assert any(s.name == "has-var" for s in skills)


def test_discover_skips_dirs_without_skill_md(tmp_path, config):
    """Directories without SKILL.md are ignored."""
    skills_dir = config.workspace_path / "skills"
    (skills_dir / "not-a-skill").mkdir(parents=True)
    (skills_dir / "not-a-skill" / "README.md").write_text("Not a skill.")

    skills = discover_skills(config)
    assert not any(s.name == "not-a-skill" for s in skills)


# -- build_catalog_text tests --


def test_build_catalog_text_formats_correctly(tmp_path):
    from decafclaw.skills import SkillInfo

    skills = [
        SkillInfo(name="alpha", description="Does A.", location=tmp_path / "a"),
        SkillInfo(name="beta", description="Does B.", location=tmp_path / "b"),
    ]
    text = build_catalog_text(skills)
    assert "## Available Skills" in text
    assert "- **alpha**: Does A." in text
    assert "- **beta**: Does B." in text
    assert "activate_skill" in text


def test_build_catalog_text_empty():
    text = build_catalog_text([])
    assert text == ""


# -- permissions tests --


def test_load_permissions_missing_file(config):
    """Returns empty dict when permissions file doesn't exist."""
    perms = _load_permissions(config)
    assert perms == {}


def test_save_and_load_permission(config):
    """Saves a permission and loads it back."""
    _save_permission(config, "tabstack", "always")
    perms = _load_permissions(config)
    assert perms["tabstack"] == "always"


def test_save_permission_preserves_existing(config):
    """Saving a new permission preserves existing ones."""
    _save_permission(config, "alpha", "always")
    _save_permission(config, "beta", "always")
    perms = _load_permissions(config)
    assert perms["alpha"] == "always"
    assert perms["beta"] == "always"


# -- activation tests --


def _make_skill_info(tmp_path, name="test-skill", body="Instructions here.",
                     has_native_tools=False):
    """Create a SkillInfo for testing."""
    location = tmp_path / name
    location.mkdir(parents=True, exist_ok=True)
    return SkillInfo(
        name=name, description=f"{name} description",
        location=location, body=body,
        has_native_tools=has_native_tools,
    )


@pytest.mark.asyncio
async def test_activate_unknown_skill(ctx):
    """Activating an unknown skill returns an error."""
    ctx.config.discovered_skills = []
    result = await tool_activate_skill(ctx, name="nonexistent")
    assert "[error:" in _text(result)
    assert "not found" in _text(result)


@pytest.mark.asyncio
async def test_activate_already_active(ctx, tmp_path):
    """Re-activating returns 'already active'."""
    skill = _make_skill_info(tmp_path)
    ctx.config.discovered_skills = [skill]
    ctx.activated_skills = {skill.name}
    _save_permission(ctx.config, skill.name, "always")

    result = await tool_activate_skill(ctx, name=skill.name)
    assert "already active" in _text(result)


@pytest.mark.asyncio
async def test_activate_with_always_permission(ctx, tmp_path):
    """Skill with 'always' permission activates without confirmation."""
    skill = _make_skill_info(tmp_path)
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, skill.name, "always")

    result = await tool_activate_skill(ctx, name=skill.name)
    assert "Instructions here." in _text(result)
    assert skill.name in getattr(ctx, "activated_skills", set())


@pytest.mark.asyncio
async def test_discover_bundled_tabstack(config, monkeypatch):
    """Bundled tabstack skill is discovered when TABSTACK_API_KEY is set."""
    monkeypatch.setenv("TABSTACK_API_KEY", "test-key")
    skills = discover_skills(config)
    assert any(s.name == "tabstack" for s in skills)


@pytest.mark.asyncio
async def test_discover_bundled_tabstack_skipped_without_key(config, monkeypatch):
    """Bundled tabstack skill is NOT discovered when TABSTACK_API_KEY is missing."""
    monkeypatch.delenv("TABSTACK_API_KEY", raising=False)
    skills = discover_skills(config)
    assert not any(s.name == "tabstack" for s in skills)


@pytest.mark.asyncio
async def test_activate_native_skill(ctx, tmp_path):
    """Native skill activation imports tools.py and registers tools on ctx."""
    skill_dir = tmp_path / "native-skill"
    skill_dir.mkdir(parents=True)
    # Create a tools.py with a simple tool
    (skill_dir / "tools.py").write_text(
        "TOOLS = {'native_test': lambda ctx: 'hello'}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'native_test'}}]\n"
        "_init_called = False\n"
        "def init(config):\n"
        "    global _init_called\n"
        "    _init_called = True\n"
    )
    skill = SkillInfo(
        name="native-skill", description="Native test.",
        location=skill_dir, body="Native instructions.",
        has_native_tools=True,
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "native-skill", "always")

    result = await tool_activate_skill(ctx, name="native-skill")
    assert "Native instructions." in _text(result)
    assert "native_test" in _text(result)
    assert "native_test" in ctx.extra_tools
    assert len(ctx.extra_tool_definitions) == 1
