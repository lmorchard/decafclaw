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
    bus = EventBus()
    context = Context(config=config, event_bus=bus)
    context.conv_id = "test-conv"
    context.channel_id = "test-channel"
    context.user_id = "testuser"
    return context
