import asyncio
import json

import pytest

from decafclaw.config import Config
from decafclaw.config_types import AgentConfig
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
    cfg = Config(agent=AgentConfig(data_home=str(tmp_path), id="t"))
    return cfg


@pytest.fixture(autouse=True)
def clear_metrics():
    # Clear in-memory metrics before each test
    _prom_counters.clear()
    _prom_sums.clear()
    _prom_counts.clear()


@pytest.mark.asyncio
async def test_llm_call_latency_recorded(config):
    subscriber = make_metrics_subscriber(config)
    await subscriber({
        "type": "llm_end",
        "model": "gpt-4o",
        "duration_ms": 120.5
    })

    # Check Prometheus memory
    prom_out = format_prometheus_metrics()
    assert "llm_calls_total" in prom_out
    assert 'llm_calls_total{model="gpt-4o"} 1.0' in prom_out
    assert 'llm_call_latency_ms_sum{model="gpt-4o"} 120.5' in prom_out
    assert 'llm_call_latency_ms_count{model="gpt-4o"} 1' in prom_out

    # Check JSONL log
    jsonl_path = config.workspace_path / "metrics.jsonl"
    assert jsonl_path.exists()
    records = [json.loads(l) for l in jsonl_path.read_text().splitlines()]

    latency_rec = next(r for r in records if r["metric_name"] == "llm_call_latency_ms")
    assert latency_rec["labels"]["model"] == "gpt-4o"
    assert latency_rec["value"] == 120.5

    # Check SQLite
    db_path = config.workspace_path / "metrics.sqlite"
    assert db_path.exists()
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT metric_name, labels, value FROM metrics").fetchall()
    conn.close()

    found = False
    for name, labels_str, val in rows:
        if name == "llm_call_latency_ms" and "gpt-4o" in labels_str:
            assert val == 120.5
            found = True
    assert found


@pytest.mark.asyncio
async def test_tool_usage_metrics_recorded(config):
    subscriber = make_metrics_subscriber(config)
    await subscriber({
        "type": "tool_end",
        "tool": "vault_write",
        "result_text": "wrote page",
        "duration_ms": 50.0
    })

    prom_out = format_prometheus_metrics()
    assert "tool_calls_total" in prom_out
    assert 'tool_calls_total{outcome="success",tool="vault_write"} 1.0' in prom_out
    assert 'tool_duration_ms_sum{outcome="success",tool="vault_write"} 50.0' in prom_out

    # Test error
    await subscriber({
        "type": "tool_end",
        "tool": "bash",
        "result_text": "[error: exit code 1]",
        "duration_ms": 10.0
    })

    prom_out = format_prometheus_metrics()
    assert 'tool_calls_total{outcome="error",tool="bash"} 1.0' in prom_out
    assert 'errors_total{component="tool",name="bash"} 1.0' in prom_out


def test_metrics_endpoint_or_query(config):
    from unittest.mock import Mock
    event_bus = Mock()
    app = create_app(config, event_bus)

    from fastapi.testclient import TestClient
    client = TestClient(app)

    # Seed a metric
    subscriber = make_metrics_subscriber(config)
    asyncio.run(subscriber({
        "type": "loop_breaker",
        "action": "stop"
    }))

    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert "loop_breaker_trips_total" in resp.text
    assert 'loop_breaker_trips_total{action="stop"} 1.0' in resp.text
