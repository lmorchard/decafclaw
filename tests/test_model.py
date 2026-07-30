"""Tests for model selection and routing (replaces old effort tests)."""

import dataclasses
import logging

import pytest

from decafclaw.config import resolve_model
from decafclaw.config_types import ModelConfig, ProviderConfig


def test_resolve_model_known(config):
    """Known model name resolves to provider + model config."""
    config = dataclasses.replace(config, providers={
        "vertex": ProviderConfig(type="vertex", project="test"),
    }, model_configs={
        "gemini-flash": ModelConfig(provider="vertex", model="gemini-2.5-flash"),
    })
    pc, mc = resolve_model(config, "gemini-flash")
    assert pc.type == "vertex"
    assert mc.model == "gemini-2.5-flash"


def test_resolve_model_unknown_raises(config):
    """Unknown model name raises KeyError."""
    with pytest.raises(KeyError, match="Unknown model config"):
        resolve_model(config, "nonexistent")


def test_resolve_model_default(config):
    """Empty name falls back to default_model."""
    config = dataclasses.replace(config, providers={
        "oai": ProviderConfig(type="openai", api_key="sk-test"),
    }, model_configs={
        "gpt4": ModelConfig(provider="oai", model="gpt-4o"),
    }, default_model="gpt4")
    pc, mc = resolve_model(config, "")
    assert mc.model == "gpt-4o"


def test_resolve_model_no_default_raises(config):
    """No model name and no default_model raises KeyError."""
    with pytest.raises(KeyError, match="No model name"):
        resolve_model(config, "")


def test_resolve_model_bad_provider_raises(config):
    """Model referencing unknown provider raises KeyError."""
    config = dataclasses.replace(config, providers={}, model_configs={
        "test": ModelConfig(provider="missing", model="foo"),
    })
    with pytest.raises(KeyError, match="unknown provider"):
        resolve_model(config, "test")


@pytest.mark.asyncio
async def test_set_model_tool(ctx):
    """set_model changes ctx.active_model and returns confirmation."""
    from decafclaw.tools.model_tools import tool_set_model

    # Set up model configs on the context's config
    ctx.config = dataclasses.replace(ctx.config, providers={
        "vertex": ProviderConfig(type="vertex", project="test"),
    }, model_configs={
        "gemini-flash": ModelConfig(provider="vertex", model="gemini-2.5-flash"),
    })

    result = await tool_set_model(ctx, model="gemini-flash")
    assert ctx.active_model == "gemini-flash"
    assert "gemini-flash" in result


@pytest.mark.asyncio
async def test_set_model_invalid(ctx):
    """set_model rejects unknown model names."""
    from decafclaw.tools.model_tools import tool_set_model

    result = await tool_set_model(ctx, model="nonexistent")
    assert "[error:" in result.text
    assert ctx.active_model == ""  # unchanged


def test_model_restored_from_history(ctx):
    """Active model is restored from the last model event in history."""
    from decafclaw.archive import append_message, read_archive

    conv_id = ctx.conv_id
    append_message(ctx.config, conv_id, {"role": "user", "content": "hello"})
    append_message(ctx.config, conv_id, {"role": "model", "content": "gemini-pro"})
    append_message(ctx.config, conv_id, {"role": "user", "content": "think about this"})

    history = read_archive(ctx.config, conv_id)
    # Scan for last model event (same logic as agent.py)
    for msg in reversed(history):
        if msg.get("role") == "model":
            ctx.active_model = msg.get("content", "")
            break
    assert ctx.active_model == "gemini-pro"


def test_skill_model_parsed(tmp_path):
    """Skill frontmatter model field is parsed into SkillInfo."""
    from decafclaw.skills import parse_skill_md

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: test-skill\n"
        "description: A test skill\n"
        "model: gemini-flash\n"
        "context: fork\n"
        "---\n"
        "Do the thing.\n"
    )
    info = parse_skill_md(skill_md)
    assert info is not None
    assert info.model == "gemini-flash"
    assert info.context == "fork"


def test_skill_legacy_effort_parsed_as_model(tmp_path):
    """Legacy effort field in SKILL.md is read as model (backward compat)."""
    from decafclaw.skills import parse_skill_md

    skill_md = tmp_path / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: test-skill\n"
        "description: A test skill\n"
        "effort: fast\n"
        "---\n"
        "Do the thing.\n"
    )
    info = parse_skill_md(skill_md)
    assert info is not None
    assert info.model == "fast"


def test_skill_model_default_empty(tmp_path):
    """Skill without model field has empty string."""
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
    assert info.model == ""


# -- Unrecognized model names (#729) -------------------------------------------


def _config_with_models(config):
    """Config with two named models and a default, for override tests."""
    return dataclasses.replace(config, providers={
        "vertex": ProviderConfig(type="vertex", project="test"),
    }, model_configs={
        "vertex-gemini-flash": ModelConfig(provider="vertex", model="gemini-2.5-flash"),
        "vertex-gemini-pro": ModelConfig(provider="vertex", model="gemini-2.5-pro"),
    }, default_model="vertex-gemini-flash")


def test_unrecognized_active_model_warns(ctx, caplog):
    """An active_model that isn't a model_configs key warns loudly.

    Regression for #729: `model: strong` in a SCHEDULE.md (a leftover from
    the removed effort system) fell through to the default model with only
    an INFO line, so bundled `dream`/`garden` ran on Flash for months.
    """
    from decafclaw.agent import _resolve_model_override

    config = _config_with_models(ctx.config)
    ctx.active_model = "strong"

    with caplog.at_level(logging.WARNING, logger="decafclaw.agent"):
        override = _resolve_model_override(ctx, config)

    assert override == {"model_name": "vertex-gemini-flash"}

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, f"expected one warning, got {warnings}"
    msg = warnings[0].getMessage()
    assert "strong" in msg
    # The warning must name the fallback and the valid choices, or it's
    # not actionable from a log line alone.
    assert "vertex-gemini-flash" in msg
    assert "vertex-gemini-pro" in msg


def test_recognized_active_model_does_not_warn(ctx, caplog):
    """A valid active_model is used and produces no warning."""
    from decafclaw.agent import _resolve_model_override

    config = _config_with_models(ctx.config)
    ctx.active_model = "vertex-gemini-pro"

    with caplog.at_level(logging.WARNING, logger="decafclaw.agent"):
        override = _resolve_model_override(ctx, config)

    assert override == {"model_name": "vertex-gemini-pro"}
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_empty_active_model_does_not_warn(ctx, caplog):
    """No active_model is the normal case, not a misconfiguration."""
    from decafclaw.agent import _resolve_model_override

    config = _config_with_models(ctx.config)
    ctx.active_model = ""

    with caplog.at_level(logging.WARNING, logger="decafclaw.agent"):
        override = _resolve_model_override(ctx, config)

    assert override == {"model_name": "vertex-gemini-flash"}
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_no_bundled_schedule_declares_a_stale_model():
    """Bundled SCHEDULE.md files must not name a model.

    Model config names are per-deployment (`vertex-gemini-pro` exists in one
    agent's config.json and not another's), so a bundled skill has nothing
    portable to declare. Upgrading a bundled task's model is an admin-overlay
    decision. This guards against reintroducing `model: strong`.
    """
    from decafclaw.schedules import parse_schedule_file
    from decafclaw.skills import _BUNDLED_SKILLS_DIR

    offenders = []
    for sched_md in sorted(_BUNDLED_SKILLS_DIR.glob("*/SCHEDULE.md")):
        task = parse_schedule_file(sched_md)
        if task is not None and task.model:
            offenders.append(f"{sched_md.parent.name}: model={task.model!r}")
    assert offenders == []
