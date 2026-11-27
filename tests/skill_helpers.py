"""Shared helpers for the skills test modules (split from tests/test_skills.py, #1419)."""

from decafclaw.media import ToolResult
from decafclaw.skills import SkillInfo


def _write_skill(skill_dir, frontmatter, body="Some instructions.", tools_py=False):
    """Helper to create a SKILL.md (and optionally tools.py) in a directory."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}")
    if tools_py:
        (skill_dir / "tools.py").write_text("TOOLS = {}\nTOOL_DEFINITIONS = []")


def _text(result):
    """Extract text from str or ToolResult."""
    return result.text if isinstance(result, ToolResult) else result


def _make_skill_info(
    tmp_path, name="test-skill", body="Instructions here.", has_native_tools=False, trust_tier="bundled"
):
    """Create a SkillInfo for testing."""
    location = tmp_path / name
    location.mkdir(parents=True, exist_ok=True)
    return SkillInfo(
        name=name,
        description=f"{name} description",
        location=location,
        body=body,
        has_native_tools=has_native_tools,
        trust_tier=trust_tier,
    )


def _native_skill(skill_dir, name="editable", tools_py="", body="Body."):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "tools.py").write_text(tools_py)
    return SkillInfo(
        name=name,
        description="Editable test.",
        location=skill_dir,
        body=body,
        has_native_tools=True,
    )


def _write_ws_skill(ctx, name, frontmatter, body="Body.", tools_py=None):
    """Create skills/<name>/ under the ctx workspace. tools_py is raw source."""
    d = ctx.config.workspace_path / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n{body}")
    if tools_py is not None:
        (d / "tools.py").write_text(tools_py)
    return d


def _tiered_skill(skill_dir, name, tools_py, tier):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "tools.py").write_text(tools_py)
    return SkillInfo(
        name=name,
        description="Tier probe.",
        location=skill_dir,
        body="Body.",
        has_native_tools=True,
        trust_tier=tier,
    )


def _two_tool_skill(skill_dir, name, tool_name, body_ret):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "tools.py").write_text(
        f"def fn(ctx):\n    return {body_ret!r}\n"
        f"TOOLS = {{{tool_name!r}: fn}}\n"
        f"TOOL_DEFINITIONS = [{{'type': 'function', "
        f"'function': {{'name': {tool_name!r}}}}}]\n"
    )
    return SkillInfo(
        name=name,
        description="Shadow probe.",
        location=skill_dir,
        body="Body.",
        has_native_tools=True,
    )
