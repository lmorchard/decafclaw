import pytest

from decafclaw.config import Config
from decafclaw.config_types import ModelConfig
from decafclaw.context import Context


@pytest.mark.asyncio
async def test_aux_llm_client_selection():
    config = Config()
    config.model_configs = {
        "primary": ModelConfig(provider="p", model="m"),
        "aux": ModelConfig(provider="p", model="m"),
    }
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


class _FakeCatalog:
    """Minimal stand-in for the loaded sidecar catalog."""

    def __init__(self, aux_approval_model: str, default_model: str = "") -> None:
        self.aux_approval_model = aux_approval_model
        self.default_model = default_model


@pytest.mark.asyncio
async def test_aux_approval_llm_honors_sidecar_pointer(monkeypatch):
    """#1213: the auto-approval judge resolves through the sidecar pointer."""
    from decafclaw.config_types import ProviderConfig

    config = Config()
    config.providers = {"local": ProviderConfig(type="openai-compat", url="http://x/v1")}
    config.model_configs["local/on"] = ModelConfig(provider="local", model="on")
    monkeypatch.setattr("decafclaw.model_catalog.load_catalog", lambda c: _FakeCatalog("local/on"))

    ctx = Context(config=config, event_bus=None)
    ctx.active_model = "active"
    assert ctx.aux_approval_llm().model_name == "local/on"


@pytest.mark.asyncio
async def test_aux_approval_llm_inherits_active_conversation_model(monkeypatch):
    """#1213: with nothing set, the auto-approval judge rides the conversation model."""
    config = Config()
    monkeypatch.setattr("decafclaw.model_catalog.load_catalog", lambda c: _FakeCatalog(""))

    ctx = Context(config=config, event_bus=None)
    ctx.active_model = "active"
    client = ctx.aux_approval_llm()
    assert client.model_name == "active"
