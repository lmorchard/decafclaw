"""Tests for DiagnosticsAndTelemetryExtension (#1488)."""

import json
import logging
from unittest.mock import patch

import pytest

from decafclaw.config import load_config
from decafclaw.config_types import ExtensionConfig
from decafclaw.context_composer import SourceEntry, read_last_request_sidecar
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import clear_extension_types, init_extensions
from decafclaw.extensions.diagnostics_and_telemetry import DiagnosticsAndTelemetryExtension
from decafclaw.metrics import _prom_counters, _prom_counts, _prom_sums, format_prometheus_metrics


@pytest.fixture(autouse=True)
def _clean_registry_and_metrics():
    clear_extension_types()
    _prom_counters.clear()
    _prom_sums.clear()
    _prom_counts.clear()
    yield
    clear_extension_types()
    _prom_counters.clear()
    _prom_sums.clear()
    _prom_counts.clear()


def _extension(config) -> DiagnosticsAndTelemetryExtension:
    return DiagnosticsAndTelemetryExtension({}, config)


@pytest.mark.asyncio
async def test_capture_freezes_request_and_settle_writes_sidecar(ctx):
    """BEFORE_LLM_CALL snapshots the payload; ON_TURN_SETTLE persists it."""
    ctx.composer.last_sources = [SourceEntry(source="system", tokens_estimated=12, items_included=1)]
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    tools = [{"type": "function", "function": {"name": "memory_recent"}}]
    ext.on_before_llm_call(ctx, messages, tools)
    # Later loop iterations append to the live list; the snapshot must not grow.
    messages.append({"role": "assistant", "content": "later"})

    await ext.on_turn_settle_hook(ctx)

    data = read_last_request_sidecar(ctx.config, ctx.conv_id)
    assert data is not None
    assert data["model"] == ctx.config.llm.model
    assert data["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert data["tools"] == tools
    assert data["sources"] == [
        {"details": {}, "items_included": 1, "items_truncated": 0, "source": "system", "tokens_estimated": 12}
    ]


@pytest.mark.asyncio
async def test_last_llm_call_wins(ctx):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)

    ext.on_before_llm_call(ctx, [{"role": "user", "content": "first"}], [])
    ext.on_before_llm_call(ctx, [{"role": "tool", "content": "second"}], [])
    await ext.on_turn_settle_hook(ctx)

    data = read_last_request_sidecar(ctx.config, ctx.conv_id)
    assert data["messages"] == [{"role": "tool", "content": "second"}]


@pytest.mark.asyncio
async def test_settle_without_capture_writes_nothing(ctx):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)

    await ext.on_turn_settle_hook(ctx)

    assert read_last_request_sidecar(ctx.config, ctx.conv_id) is None


@pytest.mark.asyncio
async def test_empty_conversation_id_is_noop(ctx):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)
    ctx.conv_id = ""
    ctx.channel_id = ""

    ext.on_before_llm_call(ctx, [{"role": "user", "content": "hi"}], [])
    await ext.on_turn_settle_hook(ctx)

    assert ext._snapshots == {}


@pytest.mark.asyncio
async def test_attach_to_bus_and_dispatch(ctx):
    """The extension's hooks are what the bus gives the core loop."""
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)

    before = ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.BEFORE_LLM_CALL)
    settle = ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE)
    assert before == [ext.on_before_llm_call]
    assert settle == [ext.on_turn_settle_hook]

    before[0](ctx, [{"role": "user", "content": "bus"}], [])
    await settle[0](ctx, [])

    assert read_last_request_sidecar(ctx.config, ctx.conv_id)["messages"][0]["content"] == "bus"


@pytest.mark.asyncio
async def test_sidecar_write_failure_is_isolated(ctx, caplog):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)
    ext.on_before_llm_call(ctx, [{"role": "user", "content": "hi"}], [])

    with caplog.at_level(logging.WARNING):
        with patch(
            "decafclaw.extensions.diagnostics_and_telemetry._write_snapshot",
            side_effect=OSError("disk error"),
        ):
            await ext.on_turn_settle_hook(ctx)

    assert "Failed to write last request sidecar" in caplog.text


@pytest.mark.asyncio
async def test_metric_failure_does_not_block_sidecar(ctx, caplog):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)
    ctx.tokens.total_prompt = 10
    ext.on_before_llm_call(ctx, [{"role": "user", "content": "hi"}], [])

    with caplog.at_level(logging.WARNING):
        with patch(
            "decafclaw.extensions.diagnostics_and_telemetry.record_metric",
            side_effect=RuntimeError("metrics down"),
        ):
            await ext.on_turn_settle_hook(ctx)

    assert read_last_request_sidecar(ctx.config, ctx.conv_id) is not None
    assert "Failed to record token metrics" in caplog.text


@pytest.mark.asyncio
async def test_token_counters_recorded(ctx):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)
    ctx.tokens.total_prompt = 120
    ctx.tokens.total_cached_prompt = 50
    ctx.tokens.total_completion = 30
    ext.on_before_llm_call(ctx, [{"role": "user", "content": "hi"}], [])

    await ext.on_turn_settle_hook(ctx)

    prom = format_prometheus_metrics()
    model = ctx.config.llm.model
    assert f'llm_tokens_total{{kind="prompt",model="{model}"}} 120.0' in prom
    assert f'llm_tokens_total{{kind="cached",model="{model}"}} 50.0' in prom
    assert f'llm_tokens_total{{kind="completion",model="{model}"}} 30.0' in prom


@pytest.mark.asyncio
async def test_token_counters_respect_metrics_switch(ctx):
    ctx.config.telemetry.metrics_enabled = False
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)
    ctx.tokens.total_prompt = 120
    ext.on_before_llm_call(ctx, [{"role": "user", "content": "hi"}], [])

    await ext.on_turn_settle_hook(ctx)

    assert "llm_tokens_total" not in format_prometheus_metrics()
    assert read_last_request_sidecar(ctx.config, ctx.conv_id) is not None


@pytest.mark.asyncio
async def test_disable_detaches_hooks_and_drops_snapshots(ctx):
    ext = _extension(ctx.config)
    ext.enable(ctx.event_bus)
    ext.on_before_llm_call(ctx, [{"role": "user", "content": "hi"}], [])

    ext.disable(ctx.event_bus)

    assert ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.BEFORE_LLM_CALL) == []
    assert ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE) == []
    assert ext._snapshots == {}
    await ext.on_turn_settle_hook(ctx)
    assert read_last_request_sidecar(ctx.config, ctx.conv_id) is None


def test_disabled_extension_has_no_hooks(config):
    config.extensions["diagnostics_and_telemetry"] = ExtensionConfig(
        enabled=False, type="diagnostics_and_telemetry", options={}
    )
    bus = EventBus()

    manager = init_extensions(config, bus)

    assert not manager.is_enabled("diagnostics_and_telemetry")
    assert bus.get_lifecycle_hooks(TurnLifecycle.BEFORE_LLM_CALL) == []
    assert bus.get_lifecycle_hooks(TurnLifecycle.ON_TURN_SETTLE) == []


def test_enabled_extension_initialized_from_config(ctx):
    ctx.config.extensions["diagnostics_and_telemetry"] = ExtensionConfig(
        enabled=True, type="diagnostics_and_telemetry", options={}
    )
    manager = init_extensions(ctx.config, ctx.event_bus)

    assert manager.is_enabled("diagnostics_and_telemetry")
    assert ctx.event_bus.get_lifecycle_hooks(TurnLifecycle.BEFORE_LLM_CALL) == [
        manager.get("diagnostics_and_telemetry").on_before_llm_call
    ]


def test_loaded_config_enables_diagnostics_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr("decafclaw.config.load_dotenv", lambda **kw: None)
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    config = load_config()

    entry = config.extensions["diagnostics_and_telemetry"]
    assert entry.enabled is True
    assert entry.type == "diagnostics_and_telemetry"


def test_config_can_disable_default_extension(tmp_path, monkeypatch):
    monkeypatch.setattr("decafclaw.config.load_dotenv", lambda **kw: None)
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"extensions": {"diagnostics_and_telemetry": {"enabled": False}}})
    )
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    config = load_config()

    assert config.extensions["diagnostics_and_telemetry"].enabled is False
