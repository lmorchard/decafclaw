"""Shared test fixtures."""

from pathlib import Path

import pytest

from decafclaw.config import Config
from decafclaw.config_types import AgentConfig, ReflectionConfig
from decafclaw.context import Context
from decafclaw.events import EventBus


@pytest.fixture
def tmp_data(tmp_path):
    """Provides a temporary data directory."""
    return tmp_path


@pytest.fixture
def config(tmp_data):
    """Provides a Config pointing at temporary directories."""
    return Config(
        agent=AgentConfig(
            data_home=str(tmp_data),
            id="test-agent",
            user_id="testuser",
        ),
        reflection=ReflectionConfig(enabled=False),
    )


@pytest.fixture
def ctx(config):
    """Provides a Context with config and event bus."""
    from decafclaw.extensions import (
        ensure_builtin_extension_types,
        get_default_enabled_extension_types,
        get_extension_type,
    )
    from decafclaw.skills import discover_skills

    bus = EventBus()
    context = Context(config=config, event_bus=bus)
    context.conv_id = "test-conv"
    context.channel_id = "test-channel"
    context.user_id = "testuser"
    context.config.discovered_skills = discover_skills(config)
    # Mirror runtime seeding: default-enabled core extensions run on every
    # conversation. `load_config` seeds these entries in production; the test
    # Config is built directly, so enable the registered defaults here.
    ensure_builtin_extension_types()
    for name in get_default_enabled_extension_types():
        factory = get_extension_type(name)
        assert factory is not None
        factory({}, config).enable(bus)
    return context


@pytest.fixture(autouse=True)
def mock_embed_text(monkeypatch, config):
    """Globally mock embed_text to prevent real network calls and return a dummy vector."""
    from unittest.mock import AsyncMock
    dimensions = getattr(getattr(config, "embedding", None), "dimensions", 768)
    dummy_vec = [0.0] * dimensions
    mock_embed = AsyncMock(return_value=dummy_vec)
    monkeypatch.setattr("decafclaw.embeddings.embed_text", mock_embed)
    monkeypatch.setattr("decafclaw.llm.embed_text", mock_embed)
    monkeypatch.setattr("decafclaw.memory_context.embed_text", mock_embed)
    return mock_embed

