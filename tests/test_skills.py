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
    assert info.auto_approve is False


def test_parse_auto_approve_true(tmp_path):
    """auto-approve: true parses to auto_approve=True (trust gate is later)."""
    skill_dir = tmp_path / "auto"
    _write_skill(
        skill_dir,
        'name: auto\ndescription: "Auto-approve skill"\nauto-approve: true',
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.auto_approve is True


def test_parse_auto_approve_false_explicit(tmp_path):
    """Explicit auto-approve: false is still False."""
    skill_dir = tmp_path / "noauto"
    _write_skill(
        skill_dir,
        'name: noauto\ndescription: "Not auto"\nauto-approve: false',
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.auto_approve is False


def test_parse_required_skills(tmp_path):
    """Parse required-skills list from frontmatter."""
    skill_dir = tmp_path / "migrate"
    _write_skill(
        skill_dir,
        'name: migrate\ndescription: "Migrate"\n'
        'context: fork\n'
        'required-skills:\n  - tabstack\n  - vault',
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.requires_skills == ["tabstack", "vault"]


def test_parse_always_loaded(tmp_path):
    """Parse always-loaded field from frontmatter."""
    skill_dir = tmp_path / "wiki"
    _write_skill(
        skill_dir,
        'name: wiki\ndescription: "Knowledge base"\nalways-loaded: true',
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.always_loaded is True


def test_parse_schedule_field(tmp_path):
    """Parse schedule cron expression from skill frontmatter."""
    skill_dir = tmp_path / "dream"
    _write_skill(
        skill_dir,
        'name: dream\ndescription: "Dream"\nschedule: "0 * * * *"',
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.schedule == "0 * * * *"


def test_parse_schedule_default(tmp_path):
    """Schedule defaults to empty string."""
    skill_dir = tmp_path / "basic"
    _write_skill(skill_dir, 'name: basic\ndescription: "Basic"')
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info.schedule == ""


def test_parse_always_loaded_default(tmp_path):
    """always-loaded defaults to False."""
    skill_dir = tmp_path / "basic"
    _write_skill(skill_dir, 'name: basic\ndescription: "Basic"')
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info.always_loaded is False


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


def test_discover_strips_auto_approve_from_workspace_skill(config, caplog):
    """auto-approve on a workspace skill is ignored with a warning."""
    skills_dir = config.workspace_path / "skills"
    _write_skill(
        skills_dir / "ws-auto",
        "name: ws-auto\ndescription: Workspace.\nauto-approve: true",
    )
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ws-auto"]
    assert len(matching) == 1
    assert matching[0].auto_approve is False
    assert any("auto-approve" in r.message for r in caplog.records)


def test_discover_strips_auto_approve_from_admin_skill(config, caplog):
    """auto-approve on an admin-level skill is ignored with a warning."""
    skills_dir = config.agent_path / "skills"
    _write_skill(
        skills_dir / "admin-auto",
        "name: admin-auto\ndescription: Admin.\nauto-approve: true",
    )
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "admin-auto"]
    assert len(matching) == 1
    assert matching[0].auto_approve is False


def test_discover_honors_auto_approve_on_bundled(config):
    """Bundled skills with auto-approve: true keep the flag set — the
    trust boundary lets bundled skills opt in while admin/workspace
    skills (tested above) get stripped."""
    skills = discover_skills(config)
    auto_approved = [s for s in skills if s.auto_approve]
    # The new background and mcp bundled skills both declare auto-approve.
    auto_names = {s.name for s in auto_approved}
    assert "background" in auto_names, (
        f"bundled `background` skill lost auto_approve flag; got {auto_names}"
    )
    assert "mcp" in auto_names, (
        f"bundled `mcp` skill lost auto_approve flag; got {auto_names}"
    )


def test_discover_strips_auto_approve_from_extra_path_skill(tmp_path, config, caplog):
    """auto-approve on an external-path skill is stripped, same as workspace."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "ext-auto",
        "name: ext-auto\ndescription: External.\nauto-approve: true",
    )
    config.extra_skill_paths = [str(extra)]
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ext-auto"]
    assert len(matching) == 1
    assert matching[0].auto_approve is False
    assert any("auto-approve" in r.message for r in caplog.records)


def test_discover_strips_always_loaded_from_extra_path_skill(tmp_path, config, caplog):
    """always-loaded on an external-path skill is stripped at discovery so
    activation-time tool caching also treats the skill as on-demand, not
    just the catalog text."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "ext-al",
        "name: ext-al\ndescription: Pretender.\nalways-loaded: true",
    )
    config.extra_skill_paths = [str(extra)]
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ext-al"]
    assert len(matching) == 1
    assert matching[0].always_loaded is False
    assert any("always-loaded" in r.message for r in caplog.records)
    catalog = build_catalog_text(skills)
    assert "ext-al" in catalog
    if "## Active Skills" in catalog:
        active_block = catalog.split("## Active Skills")[1].split("##")[0]
        assert "ext-al" not in active_block


def test_discover_strips_always_loaded_from_workspace_skill(config, caplog):
    """Workspace skills declaring always-loaded get the flag stripped (parity
    with auto-approve enforcement; the trust boundary is bundled-only)."""
    skills_dir = config.workspace_path / "skills"
    _write_skill(
        skills_dir / "ws-al",
        "name: ws-al\ndescription: Workspace.\nalways-loaded: true",
    )
    with caplog.at_level("WARNING"):
        skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ws-al"]
    assert len(matching) == 1
    assert matching[0].always_loaded is False
    assert any("always-loaded" in r.message for r in caplog.records)


def test_discover_honors_always_loaded_on_bundled(config):
    """Bundled skills with always-loaded: true keep the flag — the trust
    boundary lets bundled skills opt in while non-bundled skills get
    stripped."""
    skills = discover_skills(config)
    always = {s.name for s in skills if s.always_loaded}
    assert "vault" in always
    assert "background" in always
    assert "mcp" in always


def test_discover_includes_extra_skill_path(tmp_path, config):
    """A skill in extra_skill_paths is discovered."""
    extra = tmp_path / "external"
    _write_skill(extra / "ext-only", "name: ext-only\ndescription: External skill.")
    config.extra_skill_paths = [str(extra)]

    skills = discover_skills(config)
    assert any(s.name == "ext-only" for s in skills)


def test_discover_extra_path_does_not_shadow_bundled(tmp_path, config):
    """An external skill named the same as a bundled skill loses to bundled."""
    extra = tmp_path / "external"
    _write_skill(extra / "vault", "name: vault\ndescription: Imposter vault.")
    config.extra_skill_paths = [str(extra)]

    skills = discover_skills(config)
    vaults = [s for s in skills if s.name == "vault"]
    assert len(vaults) == 1
    assert vaults[0].description != "Imposter vault."


def test_discover_workspace_and_agent_shadow_extra_path(tmp_path, config):
    """Workspace and admin skills both override same-named external skills."""
    extra = tmp_path / "external"
    _write_skill(extra / "shared", "name: shared\ndescription: External version.")
    _write_skill(
        config.agent_path / "skills" / "shared",
        "name: shared\ndescription: Admin version.",
    )
    config.extra_skill_paths = [str(extra)]

    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "shared"]
    assert len(matching) == 1
    assert matching[0].description == "Admin version."

    _write_skill(
        config.workspace_path / "skills" / "shared",
        "name: shared\ndescription: Workspace version.",
    )
    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "shared"]
    assert len(matching) == 1
    assert matching[0].description == "Workspace version."


def test_discover_extra_path_relative_anchored_to_agent(tmp_path, config):
    """A relative entry resolves under config.agent_path."""
    rel_dir = config.agent_path / "external"
    _write_skill(rel_dir / "rel-skill", "name: rel-skill\ndescription: Relative.")
    config.extra_skill_paths = ["external"]

    skills = discover_skills(config)
    assert any(s.name == "rel-skill" for s in skills)


def test_discover_extra_path_expands_user(tmp_path, config, monkeypatch):
    """A leading ~ expands to $HOME."""
    monkeypatch.setenv("HOME", str(tmp_path))
    extra = tmp_path / "shared-skills"
    _write_skill(extra / "homed", "name: homed\ndescription: Tilde skill.")
    config.extra_skill_paths = ["~/shared-skills"]

    skills = discover_skills(config)
    assert any(s.name == "homed" for s in skills)


def test_discover_extra_path_expands_envvar(tmp_path, config, monkeypatch):
    """$VAR substrings are expanded via os.path.expandvars."""
    monkeypatch.setenv("MY_SKILLS_ROOT", str(tmp_path / "myroot"))
    extra = tmp_path / "myroot" / "skills"
    _write_skill(extra / "expanded", "name: expanded\ndescription: Var skill.")
    config.extra_skill_paths = ["$MY_SKILLS_ROOT/skills"]

    skills = discover_skills(config)
    assert any(s.name == "expanded" for s in skills)


def test_discover_extra_path_skipped_when_missing(tmp_path, config, caplog):
    """A non-existent extra path is silently skipped (no error)."""
    config.extra_skill_paths = [str(tmp_path / "does-not-exist")]
    discover_skills(config)  # must not raise
    assert all(
        "does-not-exist" not in r.message
        for r in caplog.records
        if r.levelname == "WARNING"
    )


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
        SkillInfo(name="alpha", description="Does A.", location=tmp_path / "a",
                  has_native_tools=True),
        SkillInfo(name="beta", description="Does B.", location=tmp_path / "b",
                  has_native_tools=True),
    ]
    text = build_catalog_text(skills)
    assert "## Available Skills" in text
    assert "- **alpha**: Does A." in text
    assert "- **beta**: Does B." in text
    assert "activate_skill" in text


def test_build_catalog_text_empty():
    text = build_catalog_text([])
    assert text == ""


def test_build_catalog_text_includes_markdown_only_skills(tmp_path):
    """Markdown-only skills (no tools.py) must appear in the catalog so
    the agent can activate them to load their instructions — even when
    they also have a !command trigger or a cron schedule."""
    skills = [
        SkillInfo(name="command-only", description="Command skill.",
                  location=tmp_path / "c", has_native_tools=False,
                  user_invocable=True),
        SkillInfo(name="scheduled-only", description="Scheduled skill.",
                  location=tmp_path / "s", has_native_tools=False,
                  schedule="0 3 * * *"),
    ]
    text = build_catalog_text(skills)
    assert "- **command-only**: Command skill." in text
    assert "- **scheduled-only**: Scheduled skill." in text


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
    ctx.skills.activated = {skill.name}
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
    assert skill.name in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_with_auto_approve_skips_confirmation(ctx, tmp_path):
    """Skill with auto_approve=True activates without a confirmation prompt."""
    skill = _make_skill_info(tmp_path)
    skill.auto_approve = True
    ctx.config.discovered_skills = [skill]

    # If confirmation were requested, this would hang (no handler wired
    # on this test ctx). Successful activation proves it was skipped.
    result = await tool_activate_skill(ctx, name=skill.name)
    assert "Instructions here." in _text(result)
    assert skill.name in ctx.skills.activated


@pytest.mark.asyncio
async def test_auto_approve_blocked_by_explicit_deny(ctx, tmp_path):
    """User's explicit 'deny' in permissions overrides auto-approve."""
    skill = _make_skill_info(tmp_path)
    skill.auto_approve = True
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, skill.name, "deny")

    result = await tool_activate_skill(ctx, name=skill.name)
    assert "[error:" in _text(result)
    assert "denied" in _text(result)
    assert skill.name not in ctx.skills.activated


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
    assert "native_test" in ctx.tools.extra
    assert len(ctx.tools.extra_definitions) == 1
