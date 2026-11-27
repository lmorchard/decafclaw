"""Tests for skill discovery, trust tiers, extra paths, and catalog rendering (split from #1419)."""

from pathlib import Path

from decafclaw.skills import (
    SkillInfo,
    build_catalog_text,
    discover_skills,
    find_command,
    find_skill,
    index_skills,
)
from tests.skill_helpers import _write_skill

# -- find_command / list_commands tests --


def test_find_command():
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


# -- index_skills / find_skill tests --


def test_index_skills_first_wins():
    """Duplicate names: the first occurrence wins, matching discovery precedence."""
    first = SkillInfo(name="dup", description="first", location=Path("."))
    second = SkillInfo(name="dup", description="second", location=Path("."))
    other = SkillInfo(name="other", description="other", location=Path("."))

    index = index_skills([first, second, other])

    assert set(index) == {"dup", "other"}
    assert index["dup"] is first  # shadowed, not replaced
    assert index["other"] is other


def test_find_skill_hit_and_miss():
    skill = SkillInfo(name="weather", description="Weather", location=Path("."))
    index = index_skills([skill])

    assert find_skill(index, "weather") is skill
    assert find_skill(index, "nonexistent") is None


def test_find_command_shadowed_by_non_invocable():
    """First occurrence wins: a non-invocable same-name skill shadows an invocable one."""
    skills = [
        SkillInfo(name="weather", description="Weather (workspace)", location=Path("."), user_invocable=False),
        SkillInfo(name="weather", description="Weather (bundled)", location=Path("."), user_invocable=True),
    ]
    assert find_command("weather", skills) is None


def test_find_command_prefix_is_not_a_match():
    """Lookup is exact-name; a prefix of the requested name must not match."""
    skills = [
        SkillInfo(name="weather", description="Weather", location=Path("."), user_invocable=True),
        SkillInfo(name="weather-forecast", description="Forecast", location=Path("."), user_invocable=True),
    ]
    assert find_command("weather-forecast", skills) is not None
    assert find_command("weather-forecast", skills).name == "weather-forecast"
    assert find_command("weather-for", skills) is None


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


def test_discover_shadowed_skill_inherits_native_tools(tmp_path, config):
    """Prompt-only skill in workspace inherits native tools from lower-precedence tier."""
    ws_skills = config.workspace_path / "skills"
    _write_skill(ws_skills / "helper", "name: helper\ndescription: Workspace prompt-only.", tools_py=False)

    admin_skills = config.agent_path / "skills"
    _write_skill(admin_skills / "helper", "name: helper\ndescription: Admin base.", tools_py=True)

    skills = discover_skills(config)
    helper = next(s for s in skills if s.name == "helper")
    assert helper.description == "Workspace prompt-only."
    assert helper.location == ws_skills / "helper"
    assert helper.has_native_tools is True
    assert helper.tools_location == admin_skills / "helper"


def test_discover_shadowed_skill_with_own_tools_does_not_inherit(tmp_path, config):
    """Skill with its own tools.py does not inherit lower-precedence tools_location."""
    ws_skills = config.workspace_path / "skills"
    _write_skill(ws_skills / "helper", "name: helper\ndescription: Workspace with tools.", tools_py=True)

    admin_skills = config.agent_path / "skills"
    _write_skill(admin_skills / "helper", "name: helper\ndescription: Admin base.", tools_py=True)

    skills = discover_skills(config)
    helper = next(s for s in skills if s.name == "helper")
    assert helper.has_native_tools is True
    assert helper.tools_location == ws_skills / "helper"


def test_discover_shadowed_prompt_only_remains_prompt_only(tmp_path, config):
    """Prompt-only override of a prompt-only skill remains prompt-only."""
    ws_skills = config.workspace_path / "skills"
    _write_skill(ws_skills / "helper", "name: helper\ndescription: Workspace prompt-only.", tools_py=False)

    admin_skills = config.agent_path / "skills"
    _write_skill(admin_skills / "helper", "name: helper\ndescription: Admin prompt-only.", tools_py=False)

    skills = discover_skills(config)
    helper = next(s for s in skills if s.name == "helper")
    assert helper.has_native_tools is False
    assert helper.tools_location is None


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


def test_discover_requires_satisfied_by_config(config, monkeypatch):
    """A config-satisfied requires.env no longer hides the skill (C1)."""
    monkeypatch.delenv("TABSTACK_API_KEY", raising=False)
    config.skills["tabstack"] = {"api_key": "test-secret-value"}
    skills = discover_skills(config)
    assert "tabstack" in [s.name for s in skills]


def test_discover_shadowed_skill_with_config_only_env_alias(config, monkeypatch):
    """Admin prompt-only override inherits SkillConfig to satisfy requires.env via config."""
    monkeypatch.delenv("TABSTACK_API_KEY", raising=False)
    config.skills["tabstack"] = {"api_key": "test-secret-value"}

    # Write an admin prompt override for tabstack that retains requires.env but has no tools.py
    admin_skills = config.agent_path / "skills"
    _write_skill(
        admin_skills / "tabstack",
        "name: tabstack\ndescription: Admin tabstack override.\nrequires:\n  env:\n    - TABSTACK_API_KEY",
        tools_py=False,
    )

    skills = discover_skills(config)
    tabstack = next((s for s in skills if s.name == "tabstack"), None)
    assert tabstack is not None
    assert tabstack.description == "Admin tabstack override."
    assert tabstack.location == admin_skills / "tabstack"
    assert tabstack.has_native_tools is True
    assert tabstack.tools_location != admin_skills / "tabstack"


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


def test_discover_keeps_auto_approve_on_admin_skill(config):
    """Admin-tier skills are trusted by placement; auto-approve is honored."""
    skills_dir = config.agent_path / "skills"
    _write_skill(
        skills_dir / "admin-auto",
        "name: admin-auto\ndescription: Admin.\nauto-approve: true",
    )
    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "admin-auto"]
    assert len(matching) == 1
    assert matching[0].auto_approve is True
    assert matching[0].trust_tier == "admin"


def test_discover_honors_auto_approve_on_bundled(config):
    """Bundled skills with auto-approve: true keep the flag set — the
    trust boundary lets bundled skills opt in while admin/workspace
    skills (tested above) get stripped."""
    skills = discover_skills(config)
    auto_approved = [s for s in skills if s.auto_approve]
    # The new background and mcp bundled skills both declare auto-approve.
    auto_names = {s.name for s in auto_approved}
    assert "background" in auto_names, f"bundled `background` skill lost auto_approve flag; got {auto_names}"
    assert "mcp" in auto_names, f"bundled `mcp` skill lost auto_approve flag; got {auto_names}"


def test_discover_includes_skill_creator(config):
    """The bundled skill-creator authoring guide is discovered, text-only,
    and lazy (not always-loaded)."""
    skills = discover_skills(config)
    by_name = {s.name: s for s in skills}
    assert "skill-creator" in by_name, f"bundled skill-creator not discovered; got {sorted(by_name)}"
    sc = by_name["skill-creator"]
    assert sc.has_native_tools is False, "skill-creator must be text-only (no tools.py)"
    assert sc.always_loaded is False, "skill-creator must stay lazy (not always-loaded)"
    assert sc.trust_tier == "bundled"
    assert sc.description.strip(), "skill-creator needs a non-empty description"
    assert sc.body.strip(), "skill-creator needs a non-empty body"


def test_discover_keeps_auto_approve_on_extra_path_skill(tmp_path, config):
    """Extra-path skills are trusted by user config; auto-approve is honored."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "ext-auto",
        "name: ext-auto\ndescription: External.\nauto-approve: true",
    )
    config.extra_skill_paths = [str(extra)]
    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ext-auto"]
    assert len(matching) == 1
    assert matching[0].auto_approve is True
    assert matching[0].trust_tier == "extra"


def test_discover_keeps_always_loaded_on_extra_path_skill(tmp_path, config):
    """Extra-path skills can declare always-loaded; tier is trusted."""
    extra = tmp_path / "external"
    _write_skill(
        extra / "ext-al",
        "name: ext-al\ndescription: Always-on contrib.\nalways-loaded: true",
    )
    config.extra_skill_paths = [str(extra)]
    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "ext-al"]
    assert len(matching) == 1
    assert matching[0].always_loaded is True
    catalog = build_catalog_text(skills)
    # Should appear under Active Skills, not Available Skills.
    assert "## Active Skills" in catalog
    active_block = catalog.split("## Active Skills")[1].split("##")[0]
    assert "ext-al" in active_block


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
    assert all("does-not-exist" not in r.message for r in caplog.records if r.levelname == "WARNING")


def test_trust_tier_assigned_per_scan_path(tmp_path, config):
    """Each skill gets a trust_tier reflecting which scan path produced it."""
    # workspace tier
    _write_skill(
        config.workspace_path / "skills" / "ws-skill",
        "name: ws-skill\ndescription: Workspace skill.",
    )
    # admin tier
    _write_skill(
        config.agent_path / "skills" / "admin-skill",
        "name: admin-skill\ndescription: Admin skill.",
    )
    # extra tier
    extra = tmp_path / "extra-root"
    _write_skill(extra / "extra-skill", "name: extra-skill\ndescription: Extra.")
    config.extra_skill_paths = [str(extra)]

    skills = {s.name: s for s in discover_skills(config)}
    assert skills["ws-skill"].trust_tier == "workspace"
    assert skills["admin-skill"].trust_tier == "admin"
    assert skills["extra-skill"].trust_tier == "extra"
    # Bundled skills come from src/decafclaw/skills/ — at least one
    # should be present and tagged "bundled".
    bundled = [s for s in skills.values() if s.trust_tier == "bundled"]
    assert bundled, "expected at least one bundled skill discovered"


def test_skill_tool_owners_built_from_native_tools(tmp_path, config):
    """build_skill_tool_owners maps each tool name to its owning skill."""
    from decafclaw.skills import build_skill_tool_owners

    skill_dir = tmp_path / "skills-root" / "ownertest"
    _write_skill(
        skill_dir,
        "name: ownertest\ndescription: Owner test.",
        tools_py=False,
    )
    (skill_dir / "tools.py").write_text(
        "TOOLS = {'foo_one': lambda ctx: None, 'foo_two': lambda ctx: None}\n"
        "TOOL_DEFINITIONS = ["
        "{'type': 'function', 'function': {'name': 'foo_one'}},"
        "{'type': 'function', 'function': {'name': 'foo_two'}},"
        "]\n"
    )
    config.extra_skill_paths = [str(tmp_path / "skills-root")]
    skills = discover_skills(config)
    owners = build_skill_tool_owners(skills)
    assert owners.get("foo_one") == "ownertest"
    assert owners.get("foo_two") == "ownertest"


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
        SkillInfo(name="alpha", description="Does A.", location=tmp_path / "a", has_native_tools=True),
        SkillInfo(name="beta", description="Does B.", location=tmp_path / "b", has_native_tools=True),
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
        SkillInfo(
            name="command-only",
            description="Command skill.",
            location=tmp_path / "c",
            has_native_tools=False,
            user_invocable=True,
        ),
        SkillInfo(
            name="scheduled-only", description="Scheduled skill.", location=tmp_path / "s", has_native_tools=False
        ),
    ]
    text = build_catalog_text(skills)
    assert "- **command-only**: Command skill." in text
    assert "- **scheduled-only**: Scheduled skill." in text


def test_compact_skill_summary_uses_short_summary():
    from decafclaw.skills import SkillInfo, compact_skill_summary

    skill = SkillInfo(
        name="test-skill",
        description="Long description sentence that has lots of words. Second sentence.",
        summary="Concise summary under 15 words.",
        location=Path("."),
    )
    assert compact_skill_summary(skill) == "Concise summary under 15 words."


def test_compact_skill_summary_falls_back_when_summary_exceeds_15_words():
    from decafclaw.skills import SkillInfo, compact_skill_summary

    long_summary = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen"
    assert len(long_summary.split()) == 16
    skill = SkillInfo(
        name="test-skill",
        description="First sentence of description. Second sentence of description.",
        summary=long_summary,
        location=Path("."),
    )
    # Should fall back to first sentence of description
    assert compact_skill_summary(skill) == "First sentence of description."


def test_compact_skill_summary_first_sentence_and_truncation():
    from decafclaw.skills import SkillInfo, compact_skill_summary

    # Under 20 words: intact first sentence
    skill1 = SkillInfo(
        name="s1",
        description="Single concise sentence without trailing period",
        location=Path("."),
    )
    assert compact_skill_summary(skill1) == "Single concise sentence without trailing period"

    # Multi-sentence: extracts only first sentence
    skill2 = SkillInfo(
        name="s2",
        description="First sentence here. Second sentence here. Third sentence here.",
        location=Path("."),
    )
    assert compact_skill_summary(skill2) == "First sentence here."

    # First sentence with > 20 words: truncated to exactly 20 words
    words = [f"word{i}" for i in range(25)]
    skill3 = SkillInfo(
        name="s3",
        description=" ".join(words) + ". Another sentence.",
        location=Path("."),
    )
    compact = compact_skill_summary(skill3)
    assert len(compact.split()) == 20
    assert compact == " ".join(words[:20])

    # Handles abbreviations like e.g. and i.e. without premature splitting
    skill4 = SkillInfo(
        name="s4",
        description="Admin tools for inspecting MCP servers (e.g. status, resources). Does NOT expose tools.",
        location=Path("."),
    )
    assert compact_skill_summary(skill4) == "Admin tools for inspecting MCP servers (e.g. status, resources)."


def test_parse_skill_md_parses_summary(tmp_path):
    from decafclaw.skills import parse_skill_md

    skill_dir = tmp_path / "skill_with_summary"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: with-summary\ndescription: Long description here.\nsummary: Short summary.\n---\nBody here.\n"
    )
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info is not None
    assert info.name == "with-summary"
    assert info.summary == "Short summary."
    assert info.body == "Body here."


def test_discover_loads_direct_skill_dir_from_extra_paths(tmp_path, config):
    """An entry in extra_skill_paths that points directly at a skill
    directory (one with SKILL.md at its root) loads that skill."""
    direct_skill = tmp_path / "my-direct-skill"
    _write_skill(
        direct_skill,
        "name: my-direct-skill\ndescription: Loaded directly.",
    )
    config.extra_skill_paths = [str(direct_skill)]

    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "my-direct-skill"]
    assert len(matching) == 1
    assert matching[0].description == "Loaded directly."


def test_discover_mixed_extra_paths(tmp_path, config):
    """extra_skill_paths can mix direct skill dirs and directory-of-skills
    entries; both kinds load."""
    direct = tmp_path / "direct-skill"
    _write_skill(direct, "name: direct-skill\ndescription: Direct.")

    dir_of_skills = tmp_path / "skills-root"
    _write_skill(dir_of_skills / "child-a", "name: child-a\ndescription: Child A.")
    _write_skill(dir_of_skills / "child-b", "name: child-b\ndescription: Child B.")

    config.extra_skill_paths = [str(direct), str(dir_of_skills)]

    skills = discover_skills(config)
    names = {s.name for s in skills}
    assert "direct-skill" in names
    assert "child-a" in names
    assert "child-b" in names


def test_discover_skips_missing_extra_path(tmp_path, config):
    """A non-existent path in extra_skill_paths is silently skipped —
    no crash, no unrelated skills lost."""
    real_skill = tmp_path / "real-skill"
    _write_skill(real_skill, "name: real-skill\ndescription: Real.")

    config.extra_skill_paths = [
        str(tmp_path / "does-not-exist"),
        str(real_skill),
    ]

    skills = discover_skills(config)
    names = {s.name for s in skills}
    assert "real-skill" in names


def test_discover_admin_skill_shadows_direct_extra_path(tmp_path, config):
    """A skill at admin level (data/{agent_id}/skills/) wins over a
    direct-skill-dir entry in extra_skill_paths with the same name —
    this is how per-deployment overrides of shared optional skills work."""
    # Direct entry in extra_skill_paths
    shared = tmp_path / "shared-loc"
    _write_skill(shared, "name: shared-skill\ndescription: From contrib.")

    # Admin-level shadow
    admin_skills = config.agent_path / "skills"
    _write_skill(
        admin_skills / "shared-skill",
        "name: shared-skill\ndescription: Local override.",
    )

    config.extra_skill_paths = [str(shared)]

    skills = discover_skills(config)
    matching = [s for s in skills if s.name == "shared-skill"]
    assert len(matching) == 1
    assert matching[0].description == "Local override."
