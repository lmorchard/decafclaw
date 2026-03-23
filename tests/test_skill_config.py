"""Tests for skill config resolution at activation time."""

import dataclasses
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from decafclaw.tools.skill_tools import _call_init


@dataclass
class MockSkillConfig:
    api_key: str = field(default="", metadata={"secret": True, "env_alias": "MOCK_API_KEY"})
    timeout: int = 30


@pytest.mark.asyncio
async def test_call_init_with_skill_config(ctx):
    """init(config, skill_config) is called when module has SkillConfig."""
    received = {}

    def mock_init(config, skill_config):
        received["config"] = config
        received["skill_config"] = skill_config

    module = MagicMock()
    module.init = mock_init
    module.SkillConfig = MockSkillConfig

    ctx.config = dataclasses.replace(ctx.config, skills={"mock": {"api_key": "test-key"}})

    await _call_init(module, ctx.config, "mock")
    assert received["skill_config"].api_key == "test-key"
    assert received["skill_config"].timeout == 30  # default


@pytest.mark.asyncio
async def test_call_init_with_env_override(ctx, monkeypatch):
    """Env vars override JSON config for skill config."""
    received = {}

    def mock_init(config, skill_config):
        received["skill_config"] = skill_config

    module = MagicMock()
    module.init = mock_init
    module.SkillConfig = MockSkillConfig

    monkeypatch.setenv("MOCK_API_KEY", "env-key")
    ctx.config = dataclasses.replace(ctx.config, skills={"mock": {"api_key": "json-key"}})

    await _call_init(module, ctx.config, "mock")
    assert received["skill_config"].api_key == "env-key"


@pytest.mark.asyncio
async def test_call_init_without_skill_config(ctx):
    """init(config) is called when module has no SkillConfig (backward compat)."""
    received = {}

    def mock_init(config):
        received["config"] = config

    module = SimpleNamespace(init=mock_init)

    await _call_init(module, ctx.config, "mock")
    assert "config" in received


@pytest.mark.asyncio
async def test_call_init_no_init_function(ctx):
    """No-op when module has no init()."""
    module = SimpleNamespace()

    await _call_init(module, ctx.config, "mock")  # should not raise
