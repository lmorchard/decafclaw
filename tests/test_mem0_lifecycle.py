import asyncio
import threading
import time
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


class _SlowSyncClient:
    """Stand-in for mem0's synchronous ``Memory`` client.

    Real calls embed text, run an extraction LLM, and write to Qdrant before
    returning, all inside a blocking call.
    """

    def __init__(self, delay: float):
        self.delay = delay
        self.thread_ids: dict[str, int] = {}

    def search(self, *args, **kwargs):
        self.thread_ids["search"] = threading.get_ident()
        time.sleep(self.delay)
        return [{"memory": "User lives in Portland."}]

    def add(self, *args, **kwargs):
        self.thread_ids["add"] = threading.get_ident()
        time.sleep(self.delay)
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_mem0_sync_client_calls_do_not_block_event_loop(ctx, config):
    """WHEN a synchronous mem0 client call is slow (extraction LLM, FastEmbed,
    Qdrant I/O), DecafClaw SHALL run it off the event loop so other
    conversations keep running and the PRE_COMPOSE timeout can fire.

    Inline calls stall the whole service: the turn loop, WebSocket traffic,
    and the hook timeout itself all wait for the syscall to return.
    """
    client = _SlowSyncClient(delay=0.3)
    ext = Mem0Extension({}, config, client=client)
    ctx.user_id = "user123"
    ctx.skip_memory_extension = False
    transcript = [{"role": "user", "content": "I like dark roast."}]
    main_thread = threading.get_ident()

    ticks = 0
    stop = asyncio.Event()

    async def heartbeat():
        nonlocal ticks
        while not stop.is_set():
            await asyncio.sleep(0.01)
            ticks += 1

    hb = asyncio.create_task(heartbeat())
    try:
        await ext.on_turn_settle_hook(ctx, transcript)
        settle_ticks = ticks
        ticks = 0
        facts = await ext.pre_compose_hook(ctx, "Where do I live?")
    finally:
        stop.set()
        await hb

    assert facts == "User lives in Portland."
    assert client.thread_ids["add"] != main_thread
    assert client.thread_ids["search"] != main_thread
    assert settle_ticks >= 5, "on_turn_settle_hook blocked the event loop"
    assert ticks >= 5, "pre_compose_hook blocked the event loop"


class _OverlapDetectingClient:
    def __init__(self, delay: float):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def add(self, *args, **kwargs):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._guard:
            self.active -= 1
        return {"status": "ok"}


@pytest.mark.asyncio
async def test_mem0_settle_writes_are_serialized(ctx, config):
    """WHEN two conversations settle at the same time, mem0's read-modify-write
    add() calls SHALL not overlap, or both can insert the same extracted fact."""
    client = _OverlapDetectingClient(delay=0.05)
    ext = Mem0Extension({}, config, client=client)
    ctx.user_id = "user123"
    ctx.skip_memory_extension = False
    transcript = [{"role": "user", "content": "I like dark roast."}]

    await asyncio.gather(*(ext.on_turn_settle_hook(ctx, transcript) for _ in range(3)))

    assert client.max_active == 1
