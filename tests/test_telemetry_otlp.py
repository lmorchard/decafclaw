import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from decafclaw.agent import IterationOutcome, TurnRunner
from decafclaw.config import Config
from decafclaw.context import Context
from decafclaw.context_composer import ContextComposer
from decafclaw.llm import call_llm
from decafclaw.tool_execution import execute_single_tool


@pytest.fixture
def memory_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    yield exporter
    exporter.clear()
    trace._TRACER_PROVIDER = None

@pytest.mark.asyncio
async def test_otlp_spans_started_on_core_pathways(memory_exporter):
    ctx = Context(config=Config(), event_bus=None)
    ctx.conv_id = "test_conv"

    composer = ContextComposer()
    try:
        await composer.compose(ctx, "test_prompt", [])
    except Exception:
        pass

    tc = {"id": "call_123", "type": "function", "function": {"name": "test_tool", "arguments": "{}"}}
    semaphore = asyncio.Semaphore(1)
    try:
        await execute_single_tool(ctx, tc, semaphore)
    except Exception:
        pass

    runner = TurnRunner(ctx=ctx, config=Config(), history=[], user_message="", archive_text=[], attachments=[])
    try:
        await runner._run_iteration(1)
    except Exception:
        pass

    spans = memory_exporter.get_finished_spans()
    span_names = [span.name for span in spans]

    assert "ContextComposer.compose" in span_names
    assert "execute_single_tool" in span_names
    assert "TurnRunner._run_iteration" in span_names

@pytest.mark.asyncio
async def test_llm_client_emits_span(memory_exporter):
    config = Config()
    messages = [{"role": "user", "content": "hi"}]

    try:
        await call_llm(config, messages)
    except Exception:
        pass

    spans = memory_exporter.get_finished_spans()
    span_names = [span.name for span in spans]

    assert "call_llm" in span_names or "llm_provider.complete" in span_names or "OpenAICompatProvider.complete" in span_names

@pytest.mark.asyncio
async def test_no_otlp_exporter_configured(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    config = Config()
    ctx = Context(config=config, event_bus=None)
    composer = ContextComposer()

    trace._TRACER_PROVIDER = None

    try:
        await composer.compose(ctx, "test_prompt", [])
    except Exception:
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 0

