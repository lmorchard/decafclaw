"""Tests for skill discovery rejections and refresh reporting (split from #1419)."""

from pathlib import Path

from decafclaw.skills import discover_skills
from decafclaw.tools.skill_tools import _rejection_display_path, tool_refresh_skills
from tests.skill_helpers import _text, _write_skill, _write_ws_skill

# -- discover_skills rejection accumulator --


def test_discover_records_rejections(config, monkeypatch):
    # Add one valid and one malformed skill to workspace skills directory.
    ws_skills = config.workspace_path / "skills"
    _write_skill(ws_skills / "good", "name: good\ndescription: Valid.")
    (ws_skills / "bad").mkdir(parents=True)
    (ws_skills / "bad" / "SKILL.md").write_text("# No frontmatter here\n")

    rejections = []
    skills = discover_skills(config, rejections=rejections)

    names = [s.name for s in skills]
    assert "good" in names
    assert len(rejections) == 1
    assert rejections[0].path.name == "SKILL.md"
    assert "bad" in str(rejections[0].path)
    assert "frontmatter" in rejections[0].reason.lower()


def test_discover_without_accumulator_is_unchanged(config):
    ws_skills = config.workspace_path / "skills"
    (ws_skills / "bad").mkdir(parents=True)
    (ws_skills / "bad" / "SKILL.md").write_text("# No frontmatter\n")
    # No accumulator passed — must not raise, just drop the bad skill.
    skills = discover_skills(config)
    assert all(s.name != "bad" for s in skills)


# -- tool_refresh_skills rejection reporting --


def test_rejection_display_path_relative_to_workspace(config):
    p = config.workspace_path / "skills" / "foo" / "SKILL.md"
    assert _rejection_display_path(config, p) == "skills/foo/SKILL.md"


def test_rejection_display_path_redacts_external_absolute(config):
    """A rejected skill outside workspace/agent roots (e.g. an absolute
    extra_skill_paths entry) must not leak its full host path — show only
    the trailing <skill-dir>/SKILL.md segments."""
    external = Path("/opt/elsewhere/contrib/skills/widget/SKILL.md")
    shown = _rejection_display_path(config, external)
    assert shown == "widget/SKILL.md"
    assert not shown.startswith("/")
    assert "/opt/elsewhere" not in shown


def test_refresh_skills_reports_rejections(ctx):
    ws_skills = ctx.config.workspace_path / "skills"
    (ws_skills / "broken").mkdir(parents=True)
    (ws_skills / "broken" / "SKILL.md").write_text("# missing frontmatter\n")

    text = _text(tool_refresh_skills(ctx))
    assert "Rejected (found but not loaded):" in text
    assert "broken/SKILL.md" in text
    assert "frontmatter" in text.lower()


def test_refresh_skills_survives_malformed_workspace_skill(ctx):
    """refresh_skills must report the broken skill rather than erroring out
    of the tool entirely."""
    _write_ws_skill(
        ctx,
        "badrefresh",
        "name: badrefresh\ndescription: Bad shape.",
        tools_py=(
            "def hello(ctx):\n    return 'hi'\n\nTOOLS = {'hello': hello}\nTOOL_DEFINITIONS = {'hello': hello}\n"
        ),
    )
    text = _text(tool_refresh_skills(ctx))  # must not raise
    assert "Skills refreshed" in text


# -- refresh_skills: misplaced-skill hint --
#
# A skill written to a path discovery doesn't scan is invisible to
# refresh_skills by construction: discover_skills never walks there, so there
# is no SKILL.md to reject and nothing to report. The author sees a normal
# skill list with their skill simply absent, and no reason why. These checks
# probe the two known-wrong shapes so the hint exists even when the author
# never calls skill_validate.


def test_refresh_skills_hints_at_redundant_workspace_prefix(ctx):
    d = ctx.config.workspace_path / "workspace" / "skills" / "blog-tools"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: blog-tools\ndescription: Blog.\n---\nBody.\n")
    text = _text(tool_refresh_skills(ctx))
    assert "workspace/skills/blog-tools" in text
    assert "skills/blog-tools" in text  # the suggested corrected path


def test_refresh_skills_hints_at_overly_nested_skill(ctx):
    d = ctx.config.workspace_path / "skills" / "group" / "nested"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: nested\ndescription: Too deep.\n---\nBody.\n")
    text = _text(tool_refresh_skills(ctx))
    assert "skills/group/nested" in text


def test_refresh_skills_no_hint_when_everything_is_placed_right(ctx):
    _write_ws_skill(ctx, "fine", "name: fine\ndescription: Correct place.")
    text = _text(tool_refresh_skills(ctx))
    assert "misplaced" not in text.lower()


# -- refresh_skills: what changed --


def test_refresh_skills_reports_a_newly_discovered_skill(ctx):
    tool_refresh_skills(ctx)  # establish the baseline
    _write_ws_skill(ctx, "brandnew", "name: brandnew\ndescription: New one.")
    text = _text(tool_refresh_skills(ctx))
    assert "brandnew" in text
    assert "New:" in text


def test_refresh_skills_reports_no_change(ctx):
    tool_refresh_skills(ctx)
    text = _text(tool_refresh_skills(ctx))
    assert "no change" in text.lower()


def test_refresh_skills_reports_a_removed_skill(ctx):
    d = _write_ws_skill(ctx, "goingaway", "name: goingaway\ndescription: Bye.")
    tool_refresh_skills(ctx)
    (d / "SKILL.md").unlink()
    text = _text(tool_refresh_skills(ctx))
    assert "goingaway" in text
    assert "no longer" in text.lower()
