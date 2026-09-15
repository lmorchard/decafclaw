import asyncio
import dataclasses
from unittest.mock import Mock

import pytest

from decafclaw.config import Config
from decafclaw.config_types import AgentConfig
from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.metrics import (
    _prom_counters,
    _prom_counts,
    _prom_sums,
    format_prometheus_metrics,
    make_metrics_subscriber,
)


@pytest.fixture
def config(tmp_path):
    return Config(agent=AgentConfig(data_home=str(tmp_path), id="t"))


@pytest.fixture(autouse=True)
def clear_metrics():
    _prom_counters.clear()
    _prom_sums.clear()
    _prom_counts.clear()


@pytest.mark.asyncio
async def test_llm_call_latency_recorded():
    subscriber = make_metrics_subscriber()
    await subscriber({"type": "llm_end", "model": "gpt-4o", "duration_ms": 120.5})

    prom_out = format_prometheus_metrics()
    assert 'llm_calls_total{model="gpt-4o"} 1.0' in prom_out
    assert 'llm_call_latency_ms_sum{model="gpt-4o"} 120.5' in prom_out
    assert 'llm_call_latency_ms_count{model="gpt-4o"} 1' in prom_out


@pytest.mark.asyncio
async def test_tool_usage_metrics_recorded():
    subscriber = make_metrics_subscriber()
    await subscriber({
        "type": "tool_end", "tool": "vault_write",
        "result_text": "wrote page", "duration_ms": 50.0,
    })

    prom_out = format_prometheus_metrics()
    assert 'tool_calls_total{outcome="success",tool="vault_write"} 1.0' in prom_out
    assert 'tool_duration_ms_sum{outcome="success",tool="vault_write"} 50.0' in prom_out

    await subscriber({
        "type": "tool_end", "tool": "bash",
        "result_text": "[error: exit code 1]", "duration_ms": 10.0,
    })

    prom_out = format_prometheus_metrics()
    assert 'tool_calls_total{outcome="error",tool="bash"} 1.0' in prom_out
    assert 'errors_total{component="tool",name="bash"} 1.0' in prom_out


def test_metrics_endpoint_or_query(config):
    app = create_app(config, Mock())

    from fastapi.testclient import TestClient
    client = TestClient(app)

    subscriber = make_metrics_subscriber()
    asyncio.run(subscriber({"type": "loop_breaker", "action": "stop"}))

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert 'loop_breaker_trips_total{action="stop"} 1.0' in resp.text


# -- #848 review blockers -----------------------------------------------------


@pytest.fixture
def stub_llm(monkeypatch):
    """Stub both LLM seams and record the calls.

    ``_call_llm_with_events`` picks between the module-level ``agent.call_llm``
    and ``call_llm_streaming``, which the streaming branch imports from
    ``.llm`` *inside* the function — so patching one seam leaves the other
    live. That matters more than it looks: ``config.llm.streaming`` defaults to
    ``True`` and the default ``llm.url`` is a LAN address, so a half-patched
    test makes a real request, passes wherever that host is reachable, and
    fails in CI with a ConnectError.

    Tests assert against the returned list, so a live network path cannot pass
    silently — an unpatched seam leaves it empty.
    """
    from decafclaw import agent, llm

    calls: list[dict] = []

    async def fake_call(config, messages, tools=None, **kwargs):
        calls.append(kwargs)
        return {"content": "hi", "tool_calls": None, "role": "assistant", "usage": {}}

    monkeypatch.setattr(agent, "call_llm", fake_call)
    monkeypatch.setattr(llm, "call_llm_streaming", fake_call)
    return calls


def _config_with_streaming(tmp_path, streaming: bool, **kwargs):
    config = Config(agent=AgentConfig(data_home=str(tmp_path), id="t"), **kwargs)
    return dataclasses.replace(
        config, llm=dataclasses.replace(config.llm, streaming=streaming))


async def _publish_llm_end(config, stub_llm, **call_kwargs) -> dict:
    """Run one instrumented LLM call and return the ``llm_end`` event."""
    from decafclaw import agent

    bus = EventBus()
    seen: list[dict] = []
    bus.subscribe(lambda event: seen.append(event))

    ctx = Context(config=config, event_bus=bus)
    await agent._call_llm_with_events(ctx, config, [], [], **call_kwargs)

    assert len(stub_llm) == 1, "the LLM seam was not stubbed — this call hit the network"
    return next(e for e in seen if e.get("type") == "llm_end")


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.asyncio
async def test_llm_end_carries_resolved_model_on_default_path(stub_llm, tmp_path, streaming):
    """Regression: ``model`` was ``ctx.active_model``, which is "" unless the
    conversation pinned a named model config — so every default-model call
    landed in ``llm_calls_total{model=""}``. Prometheus cannot distinguish an
    empty label value from an absent one, which made the label useless.
    """
    config = _config_with_streaming(tmp_path, streaming, default_model="gemini-flash")
    ctx_model = Context(config=config, event_bus=EventBus()).active_model
    assert ctx_model == "", "precondition: nothing pinned the model"

    llm_end = await _publish_llm_end(config, stub_llm)
    assert llm_end["model"] == "gemini-flash"


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.asyncio
async def test_llm_end_prefers_explicit_model_override(stub_llm, tmp_path, streaming):
    config = _config_with_streaming(tmp_path, streaming, default_model="gemini-flash")

    llm_end = await _publish_llm_end(config, stub_llm, model_name="opus-5")
    assert llm_end["model"] == "opus-5"


@pytest.mark.asyncio
async def test_label_values_are_escaped_for_exposition():
    """A quote, backslash, or newline in a label value produces a malformed
    exposition line, and Prometheus fails the *entire* scrape on one parse
    error — so one oddly-named model config would drop every metric. Model
    config names are user-supplied, so this is reachable.
    """
    raw_model = 'gpt"4\\o\nbeta'  # a double quote, a backslash, and a newline

    subscriber = make_metrics_subscriber()
    await subscriber({"type": "llm_end", "model": raw_model, "duration_ms": 1.0})

    # Exact line match: also proves the newline did not split the line in two.
    expected = 'llm_calls_total{model="gpt\\"4\\\\o\\nbeta"} 1.0'
    assert expected in format_prometheus_metrics().splitlines()


def test_metrics_module_does_no_io():
    """Metrics are scrape-only by decision: Prometheus owns retention, so a
    sidecar here would duplicate what the scrape target already stores (#848
    removed the JSONL and SQLite surfaces this shipped with).

    Two structural facts keep that true. The subscriber factory takes no
    ``config``, so it cannot reach ``workspace_path``; and the module imports
    nothing that does I/O. Either would have to be undone deliberately.
    """
    import inspect
    from pathlib import Path

    import decafclaw.metrics as metrics_mod

    assert not inspect.signature(make_metrics_subscriber).parameters, (
        "make_metrics_subscriber must take no config — that is what makes a "
        "durable write impossible to add by accident")

    source = Path(metrics_mod.__file__).read_text()
    body = source.split('"""', 2)[-1]  # skip the module docstring
    for forbidden in ("sqlite3", "import os", "from pathlib", "open(", "to_thread"):
        assert forbidden not in body, f"{forbidden!r} suggests I/O crept back into metrics.py"


def test_metrics_subscriber_is_config_gated():
    """Structural: the four sibling telemetry subscribers in ``runner.py`` are
    each wired behind a ``config.telemetry.*_enabled`` guard. Without one there
    is no way to stop feeding the endpoint.
    """
    from pathlib import Path

    import decafclaw.runner as runner_mod

    source = Path(runner_mod.__file__).read_text()
    idx = source.index("make_metrics_subscriber")
    preceding = source[:idx]
    guard = "if config.telemetry.metrics_enabled:"
    assert guard in preceding, f"{guard!r} must gate make_metrics_subscriber"
    assert preceding.rindex(guard) > preceding.rindex("config.telemetry.retrieval_enabled")


def test_telemetry_config_exposes_metrics_flag():
    from decafclaw.config_types import TelemetryConfig

    cfg = TelemetryConfig()
    assert cfg.metrics_enabled is True
    # No path fields: the subscriber keeps no sidecar.
    assert not any(f.name.startswith("metrics_") and f.name != "metrics_enabled"
                   for f in dataclasses.fields(TelemetryConfig))
