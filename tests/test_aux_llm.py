import pytest

from decafclaw.config import Config
from decafclaw.config_types import ModelConfig
from decafclaw.context import Context


@pytest.mark.asyncio
async def test_aux_llm_client_selection():
    config = Config()
    config.model_configs = {"primary": ModelConfig(provider="p", model="m"), "aux": ModelConfig(provider="p", model="m")}
    config.default_model = "primary"
    config.auxiliary_model = "aux"

    ctx = Context(config=config, event_bus=None)
    client = ctx.aux_llm()
    assert client.model_name == "aux"

@pytest.mark.asyncio
async def test_aux_llm_fallback():
    config = Config()
    config.model_configs = {"primary": ModelConfig(provider="p", model="m")}
    config.default_model = "primary"

    ctx = Context(config=config, event_bus=None)
    client = ctx.aux_llm()
    assert client.model_name == "primary"

    # Also fallback to active_model if set
    ctx.active_model = "active"
    client = ctx.aux_llm()
    assert client.model_name == "active"
