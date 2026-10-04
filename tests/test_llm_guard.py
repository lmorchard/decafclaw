"""Tests for pytest autouse LLM guard and stub_llm fixture."""

import json
from unittest.mock import patch

import pytest

from decafclaw import agent, llm
from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
from decafclaw.llm.providers.vertex import VertexProvider


@pytest.mark.asyncio
async def test_unstubbed_agent_call_llm_raises(config):
    """Calling agent.call_llm without stub_llm or live_llm raises RuntimeError."""
    with pytest.raises(RuntimeError, match=r"Unstubbed LLM call in test:.*Use stub_llm fixture or mark with @pytest\.mark\.live_llm"):
        await agent.call_llm(config, [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_unstubbed_streaming_call_raises(config):
    """Calling llm.call_llm_streaming without stub_llm or live_llm raises RuntimeError."""
    with pytest.raises(RuntimeError, match=r"Unstubbed LLM call in test:.*Use stub_llm fixture or mark with @pytest\.mark\.live_llm"):
        await llm.call_llm_streaming(config, [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_unstubbed_provider_complete_raises():
    """Calling OpenAICompatProvider.complete without stub_llm or live_llm raises RuntimeError."""
    provider = OpenAICompatProvider(url="http://test-endpoint")
    with pytest.raises(RuntimeError, match=r"Unstubbed LLM call in test:.*Use stub_llm fixture or mark with @pytest\.mark\.live_llm"):
        await provider.complete("dummy-model", [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_unstubbed_vertex_complete_raises():
    """Calling VertexProvider.complete without stub_llm or live_llm raises RuntimeError."""
    provider = VertexProvider(project="test-project")
    with pytest.raises(RuntimeError, match=r"Unstubbed LLM call in test:.*Use stub_llm fixture or mark with @pytest\.mark\.live_llm"):
        await provider.complete("gemini-2.5-flash", [{"role": "user", "content": "hello"}])


@pytest.mark.asyncio
async def test_unstubbed_llm_call_llm_raises(config):
    """Calling decafclaw.llm.call_llm routes to provider and raises RuntimeError."""
    with pytest.raises(RuntimeError, match=r"Unstubbed LLM call in test:.*Use stub_llm fixture or mark with @pytest\.mark\.live_llm"):
        await llm.call_llm(config, [{"role": "user", "content": "hello"}])


@pytest.mark.live_llm
@pytest.mark.asyncio
async def test_live_llm_marker_opts_out_of_guard(config):
    """Tests marked with @pytest.mark.live_llm bypass the autouse guard."""
    # When live_llm is active, the functions are the original implementations, not _guard_fail
    assert agent.call_llm.__name__ != "_guard_fail"
    assert llm.call_llm_streaming.__name__ != "_guard_fail"
    assert OpenAICompatProvider.complete.__name__ != "_guard_fail"

    # Wire mock at network layer succeeds without triggering guard RuntimeError
    class FakeSSEEvent:
        def __init__(self, data):
            self.data = data

    class FakeResponse:
        def __init__(self, status_code=200, body=b""):
            self.status_code = status_code
            self.headers = {}
            self._body = body

        async def aread(self):
            return self._body

    class FakeEventSource:
        def __init__(self, events):
            self.response = FakeResponse(200)
            self._events = events

        async def aiter_sse(self):
            for event in self._events:
                yield event

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    events = [
        FakeSSEEvent(json.dumps({"choices": [{"delta": {"content": "live call opt out"}}]})),
        FakeSSEEvent("[DONE]"),
    ]

    with patch("httpx_sse.aconnect_sse", return_value=FakeEventSource(events)):
        result = await llm.call_llm_streaming(config, [])
        assert result["content"] == "live call opt out"


@pytest.mark.asyncio
async def test_stub_llm_fixture_succeeds(config, stub_llm):
    """Tests requesting the stub_llm fixture succeed and record call arguments."""
    messages = [{"role": "user", "content": "hello"}]

    resp1 = await agent.call_llm(config, messages, model_name="custom-model")
    assert resp1["content"] == "hi"
    assert len(stub_llm) == 1
    assert stub_llm[0]["config"] is config
    assert stub_llm[0]["messages"] == messages
    assert stub_llm[0]["model_name"] == "custom-model"

    resp2 = await llm.call_llm_streaming(config, messages)
    assert resp2["content"] == "hi"
    assert len(stub_llm) == 2

    provider = OpenAICompatProvider(url="http://test")
    resp3 = await provider.complete("test-model", messages)
    assert resp3["content"] == "hi"
    assert len(stub_llm) == 3
    assert stub_llm[2]["model"] == "test-model"


@pytest.mark.asyncio
async def test_stub_llm_canned_response_customization(config, stub_llm):
    """The canned response in stub_llm can be customized per test."""
    stub_llm.response = {
        "content": "custom canned response",
        "tool_calls": None,
        "role": "assistant",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    result = await agent.call_llm(config, [{"role": "user", "content": "ping"}])
    assert result["content"] == "custom canned response"
    assert result["usage"]["completion_tokens"] == 5
