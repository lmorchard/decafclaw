
from unittest.mock import AsyncMock, patch

import pytest
from opentelemetry import trace


class StubLlmCalls(list):
    """List of captured LLM calls with customizable canned response."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = {
            "content": "hi",
            "tool_calls": None,
            "role": "assistant",
            "usage": {},
        }


@pytest.fixture
def stub_llm(monkeypatch):
    """Stub LLM seams, record received calls, and return canned model responses."""
    calls = StubLlmCalls()

    async def fake_call(config=None, messages=None, tools=None, *args, **kwargs):
        record = dict(kwargs)
        record["config"] = config
        record["messages"] = messages
        record["tools"] = tools
        if args:
            record["args"] = args
        calls.append(record)
        return dict(calls.response)

    async def fake_provider_complete(self, model, messages, *, tools=None, **kwargs):
        record = dict(kwargs)
        record["model"] = model
        record["messages"] = messages
        record["tools"] = tools
        calls.append(record)
        return dict(calls.response)

    monkeypatch.setattr("decafclaw.agent.call_llm", fake_call)
    monkeypatch.setattr("decafclaw.llm.call_llm", fake_call)
    monkeypatch.setattr("decafclaw.llm.call_llm_streaming", fake_call)
    monkeypatch.setattr(
        "decafclaw.llm.providers.openai_compat.OpenAICompatProvider.complete",
        fake_provider_complete,
    )
    monkeypatch.setattr(
        "decafclaw.llm.providers.vertex.VertexProvider.complete",
        fake_provider_complete,
    )
    return calls


@pytest.fixture(autouse=True)
def guard_chat_completions(monkeypatch, request):
    """Guard chat completion seams against unstubbed real network calls in tests."""
    if (
        request.node.get_closest_marker("live_llm")
        or request.node.get_closest_marker("integration")
    ):
        return

    if "stub_llm" in request.fixturenames:
        return

    async def _guard_fail(*args, **kwargs):
        raise RuntimeError(
            f"Unstubbed LLM call in test: {request.node.nodeid}. "
            "Use stub_llm fixture or mark with @pytest.mark.live_llm"
        )

    monkeypatch.setattr("decafclaw.agent.call_llm", _guard_fail)
    monkeypatch.setattr("decafclaw.llm.call_llm_streaming", _guard_fail)
    monkeypatch.setattr(
        "decafclaw.llm.providers.openai_compat.OpenAICompatProvider.complete",
        _guard_fail,
    )
    monkeypatch.setattr(
        "decafclaw.llm.providers.vertex.VertexProvider.complete",
        _guard_fail,
    )


@pytest.fixture(autouse=True)
def mock_friction_llm(request):
    if "friction" in request.node.name:
        with patch("decafclaw.friction.call_structured", new_callable=AsyncMock) as mock_call_structured:
            mock_call_structured.return_value = {
                "themes": [
                    {"theme": "Use standard logger instead of print", "proposed_addition": "Always use the standard logger, never use print.", "occurrences": 3}
                ]
            }
            yield mock_call_structured
    else:
        yield

@pytest.fixture(autouse=True)
def reset_otlp_singletons():
    yield
    trace._TRACER_PROVIDER = None
    if hasattr(trace._TRACER_PROVIDER_SET_ONCE, "__bool__"):
        # Actually it's an object `_SetOnce` in newer opentelemetry.
        pass

    # In opentelemetry-api 1.x, there is no _TRACER_PROVIDER_SET_ONCE boolean.
    # It might be in opentelemetry.util._once.Once
    if hasattr(trace, "_TRACER_PROVIDER_SET_ONCE"):
        trace._TRACER_PROVIDER_SET_ONCE._done = False
