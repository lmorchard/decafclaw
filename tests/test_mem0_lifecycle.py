from unittest.mock import MagicMock

import pytest

from decafclaw.config_types import ExtensionConfig
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import init_extensions
from decafclaw.extensions.mem0 import Mem0Extension


def test_mem0_local_hook_registration(config):
    """WHEN an enabled extensions.mem0 block is configured,
    DecafClaw SHALL initialize the local embedded store and register its
    PRE_COMPOSE and ON_TURN_SETTLE hooks without network egress.
    """
    config.extensions["mem0"] = ExtensionConfig(
        enabled=True,
        type="mem0",
        options={
            "qdrant_path": "data/mem0_qdrant",
            "embedder_model": "BAAI/bge-small-en-v1.5",
            "llm_base_url": "http://synobian:4100/v1",
            "llm_model": "mandelbrot3-flint",
        },
    )

    bus = EventBus()
    ext_mgr = init_extensions(config, bus)

    assert ext_mgr.is_enabled("mem0")
    mem0_ext = ext_mgr.get("mem0")
    assert isinstance(mem0_ext, Mem0Extension)

    # Verify config enforces local-first with zero third-party cloud egress
    built_cfg = mem0_ext._build_mem0_config()
    assert built_cfg["vector_store"]["provider"] == "qdrant"
    assert built_cfg["vector_store"]["config"]["path"] == "data/mem0_qdrant"
    assert built_cfg["embedder"]["provider"] == "fastembed"
    assert built_cfg["embedder"]["config"]["model"] == "BAAI/bge-small-en-v1.5"
    assert built_cfg["llm"]["config"]["openai_base_url"] == "http://synobian:4100/v1"
    assert built_cfg["llm"]["config"]["model"] == "mandelbrot3-flint"

    # Verify pre_compose_timeout default
    assert mem0_ext.pre_compose_timeout == 5.0

    # Verify both hooks are registered on the event bus
    pre_compose_hooks = bus.get_lifecycle_hooks(TurnLifecycle.PRE_COMPOSE)
    settle_hooks = bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE)

    assert len(pre_compose_hooks) == 1
    assert len(settle_hooks) == 1


@pytest.mark.asyncio
async def test_mem0_hooks_execution_and_skip(ctx, config):
    """Test search and extraction dispatch on mock client and skip flag handling."""
    mock_client = MagicMock()
    mock_client.search.return_value = [{"memory": "User lives in Portland."}]
    mock_client.add.return_value = {"status": "ok"}

    ext = Mem0Extension({}, config, client=mock_client)
    bus = EventBus()
    ext.enable(bus)

    # 1. Normal pre_compose
    ctx.user_id = "user123"
    ctx.skip_memory_extension = False
    result = await ext.pre_compose_hook(ctx, "Where do I live?")
    assert result == "User lives in Portland."
    mock_client.search.assert_called_with("Where do I live?", filters={"user_id": "user123"}, limit=5)

    # 2. Skip memory extension flag honored
    ctx.skip_memory_extension = True
    skipped_result = await ext.pre_compose_hook(ctx, "Where do I live?")
    assert skipped_result is None

    # 3. Settle hook extraction
    ctx.skip_memory_extension = False
    transcript = [
        {"role": "user", "content": "I like dark roast."},
        {"role": "assistant", "content": "Got it."},
    ]
    await ext.on_turn_settle_hook(ctx, transcript)
    mock_client.add.assert_called_with(
        [
            {"role": "user", "content": "I like dark roast."},
            {"role": "assistant", "content": "Got it."},
        ],
        user_id="user123",
    )

    # 4. Settle hook skips when flag set
    mock_client.add.reset_mock()
    ctx.skip_memory_extension = True
    await ext.on_turn_settle_hook(ctx, transcript)
    mock_client.add.assert_not_called()
