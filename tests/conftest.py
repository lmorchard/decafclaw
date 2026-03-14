"""Shared test fixtures."""

import pytest
from pathlib import Path
from decafclaw.config import Config
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
        data_home=str(tmp_data),
        agent_id="test-agent",
        agent_user_id="testuser",
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
