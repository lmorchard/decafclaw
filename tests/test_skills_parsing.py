"""Tests for SKILL.md parsing and frontmatter validation (split from #1419)."""

from decafclaw.skills import build_skill_info, parse_skill_md, validate_skill_md
from tests.skill_helpers import _write_skill

# -- parse_skill_md tests --


# -- validate_skill_md tests --


def _checks_by_name(result):
    return {c.name: c for c in result.checks}


def test_validate_ok(tmp_path):
    skill_dir = tmp_path / "ok"
    _write_skill(skill_dir, "name: ok\ndescription: Fine.")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is True
    assert result.first_failure is None
    assert result.meta["name"] == "ok"
    assert result.body.strip() == "Some instructions."


def test_validate_no_frontmatter(tmp_path):
    skill_dir = tmp_path / "no-fm"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Heading, no frontmatter\n")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    assert _checks_by_name(result)["frontmatter"].passed is False
    assert "frontmatter" in result.first_failure.lower()


def test_validate_malformed_yaml_is_frontmatter_failure(tmp_path):
    """Malformed YAML inside the delimiters is reported as a frontmatter
    failure, and the message doesn't claim the file merely fails to 'start
    with' a block (it has delimiters — the YAML itself is invalid)."""
    skill_dir = tmp_path / "bad-yaml"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\n: : : invalid\n---\nBody.")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    fm = _checks_by_name(result)["frontmatter"]
    assert fm.passed is False
    assert "yaml" in fm.message.lower()
    assert "start with" not in fm.message.lower()


def test_validate_missing_name(tmp_path):
    skill_dir = tmp_path / "no-name"
    _write_skill(skill_dir, "description: No name.")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    assert _checks_by_name(result)["name"].passed is False
    # frontmatter passed before name failed
    assert _checks_by_name(result)["frontmatter"].passed is True


def test_validate_missing_description(tmp_path):
    skill_dir = tmp_path / "no-desc"
    _write_skill(skill_dir, "name: no-desc")
    result = validate_skill_md(skill_dir / "SKILL.md")
    assert result.ok is False
    assert _checks_by_name(result)["description"].passed is False


def test_validate_unreadable(tmp_path):
    missing = tmp_path / "gone" / "SKILL.md"
    result = validate_skill_md(missing)
    assert result.ok is False
    assert _checks_by_name(result)["readable"].passed is False


def test_build_skill_info_from_validation(tmp_path):
    skill_dir = tmp_path / "full"
    _write_skill(skill_dir, "name: full\ndescription: Full.", tools_py=True)
    result = validate_skill_md(skill_dir / "SKILL.md")
    info = build_skill_info(result)
    assert info.name == "full"
    assert info.description == "Full."
    assert info.location == skill_dir
    assert info.has_native_tools is True


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
        "allowed-tools: vault_read, vault_write, shell\n"
        "context: fork\n"
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
        'name: migrate\ndescription: "Migrate"\ncontext: fork\nrequired-skills:\n  - tabstack\n  - vault',
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


def test_parse_always_loaded_default(tmp_path):
    """always-loaded defaults to False."""
    skill_dir = tmp_path / "basic"
    _write_skill(skill_dir, 'name: basic\ndescription: "Basic"')
    info = parse_skill_md(skill_dir / "SKILL.md")
    assert info.always_loaded is False
