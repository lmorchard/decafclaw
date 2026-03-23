"""Tests for effort level resolution and routing."""

import dataclasses

import pytest

from decafclaw.config import resolve_effort


def test_resolve_effort_known_level(config):
    """Known effort level merges model entry over config.llm."""
    config = dataclasses.replace(config, models={
        "strong": {"model": "gemini-2.5-pro"},
    })
    resolved = resolve_effort(config, "strong")
    assert resolved.model == "gemini-2.5-pro"
    assert resolved.url == config.llm.url  # inherits


def test_resolve_effort_with_url_override(config):
    """Effort entry can override url and api_key."""
    config = dataclasses.replace(config, models={
        "strong": {"model": "pro", "url": "https://other", "api_key": "sk-other"},
    })
    resolved = resolve_effort(config, "strong")
    assert resolved.model == "pro"
    assert resolved.url == "https://other"
    assert resolved.api_key == "sk-other"


def test_resolve_effort_unknown_level(config):
    """Unknown effort level falls back to config.llm."""
    resolved = resolve_effort(config, "unknown")
    assert resolved.model == config.llm.model


def test_resolve_effort_no_models_section(config):
    """Absent models section falls back to config.llm."""
    resolved = resolve_effort(config, "strong")
    assert resolved.model == config.llm.model


def test_resolve_effort_default(config):
    """'default' effort level uses the default model entry."""
    config = dataclasses.replace(config, models={
        "default": {"model": "flash"},
    })
    resolved = resolve_effort(config, "default")
    assert resolved.model == "flash"


@pytest.mark.asyncio
async def test_set_effort_tool(ctx):
    """set_effort changes ctx.effort and returns confirmation."""
    from decafclaw.tools.effort_tools import tool_set_effort

    result = await tool_set_effort(ctx, level="strong")
    assert ctx.effort == "strong"
    assert "strong" in result


@pytest.mark.asyncio
async def test_set_effort_invalid_level(ctx):
    """set_effort rejects unknown levels."""
    from decafclaw.tools.effort_tools import tool_set_effort

    result = await tool_set_effort(ctx, level="turbo")
    assert "[error:" in result.text
    assert ctx.effort == "default"  # unchanged


def test_effort_restored_from_history(ctx):
    """Effort level is restored from the last effort event in history."""
    from decafclaw.archive import append_message, read_archive

    conv_id = ctx.conv_id
    append_message(ctx.config, conv_id, {"role": "user", "content": "hello"})
    append_message(ctx.config, conv_id, {"role": "effort", "content": "strong"})
    append_message(ctx.config, conv_id, {"role": "user", "content": "think about this"})

    history = read_archive(ctx.config, conv_id)
    # Scan for last effort event (same logic as agent.py)
    for msg in reversed(history):
        if msg.get("role") == "effort":
            ctx.effort = msg.get("content", "default")
            break
    assert ctx.effort == "strong"


def test_skill_effort_parsed(tmp_path):
    """Skill frontmatter effort field is parsed into SkillInfo."""
    from decafclaw.skills import parse_skill_md

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: test-skill\n"
        "description: A test skill\n"
        "effort: fast\n"
        "context: fork\n"
        "---\n"
        "Do the thing.\n"
    )
    info = parse_skill_md(skill_md)
    assert info is not None
    assert info.effort == "fast"
    assert info.context == "fork"


def test_skill_effort_default_empty(tmp_path):
    """Skill without effort field has empty string."""
    from decafclaw.skills import parse_skill_md

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: test-skill\n"
        "description: A test skill\n"
        "---\n"
        "Do the thing.\n"
    )
    info = parse_skill_md(skill_md)
    assert info is not None
    assert info.effort == ""
