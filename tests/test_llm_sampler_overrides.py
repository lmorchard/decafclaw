"""Per-call sampler overrides reach each provider's wire request.

``llm_query`` (#1820) can override temperature, top_p, max_tokens, and
stop sequences per call. The tool-level tests pin the kwargs it passes;
these tests pin that the providers translate them into their API fields
instead of silently dropping them.

All HTTP is mocked below the completion guard, so this file runs offline.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
from decafclaw.llm.providers.openai_responses import OpenAIResponsesProvider

# Bypass the completion guard only because every transport is mocked below.
pytestmark = pytest.mark.live_llm

MESSAGES = [{"role": "user", "content": "hello"}]
STOP = ["\nUser:", "\nKorrine:"]

CHAT_PAYLOAD = {
    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}

RESPONSES_PAYLOAD = {
    "id": "resp_1",
    "status": "completed",
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "ok"}],
        }
    ],
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def _mock_client(payload):
    client = AsyncMock()
    client.post.return_value = httpx.Response(200, json=payload, request=httpx.Request("POST", "https://test/v1"))
    client.__aenter__.return_value = client
    return client


@pytest.mark.asyncio
async def test_openai_compat_nonstreaming_body_carries_overrides():
    provider = OpenAICompatProvider(url="https://test")
    client = _mock_client(CHAT_PAYLOAD)
    with patch("httpx.AsyncClient", return_value=client):
        await provider.complete("m", MESSAGES, temperature=0.85, top_p=0.9, max_tokens=256, stop=STOP)

    body = client.post.call_args.kwargs["json"]
    assert body["temperature"] == 0.85
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 256
    assert body["stop"] == STOP


@pytest.mark.asyncio
async def test_openai_compat_streaming_body_carries_overrides():
    provider = OpenAICompatProvider(url="https://test")
    captured: list[dict] = []

    async def fake_attempt(url, body, timeout, cancel_event, state):
        captured.append(body)

    provider._stream_one_attempt = fake_attempt  # type: ignore[method-assign]

    await provider.complete("m", MESSAGES, streaming=True, temperature=0.2, stop=["END"])

    assert captured[0]["temperature"] == 0.2
    assert captured[0]["stop"] == ["END"]


@pytest.mark.asyncio
async def test_openai_responses_maps_overrides():
    provider = OpenAIResponsesProvider(api_key="test")
    client = _mock_client(RESPONSES_PAYLOAD)
    with patch("httpx.AsyncClient", return_value=client):
        await provider.complete("m", MESSAGES, temperature=0.3, top_p=0.8, max_tokens=512)

    body = client.post.call_args.kwargs["json"]
    assert body["temperature"] == 0.3
    assert body["top_p"] == 0.8
    assert body["max_output_tokens"] == 512
    assert "max_tokens" not in body


@pytest.mark.asyncio
async def test_openai_responses_rejects_stop_sequences():
    provider = OpenAIResponsesProvider(api_key="test")

    with pytest.raises(ValueError, match="stop sequences"):
        await provider.complete("m", MESSAGES, stop=STOP)
