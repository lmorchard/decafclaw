"""Offline Responses wire fixtures, including reasoning/function replay."""

import asyncio
import copy
import dataclasses
import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from decafclaw.config_types import ModelConfig, ProviderConfig
from decafclaw.llm import call_llm, call_llm_streaming, init_providers
from decafclaw.llm.history import assistant_message
from decafclaw.llm.providers.openai_responses import OpenAIResponsesProvider

# Bypass the completion guard only because every transport is mocked below.
pytestmark = pytest.mark.live_llm

MODEL = "gpt-6-luna"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "weather",
            "description": "Get weather",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}, "units": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
]
REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
CALLS = [
    {
        "type": "function_call",
        "id": f"fc_{i}",
        "call_id": f"call_{i}",
        "name": "weather",
        "arguments": json.dumps({"city": city}),
        "status": "completed",
    }
    for i, city in enumerate(["Paris", "London"])
]
MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "phase": "final_answer",
    "content": [{"type": "output_text", "text": "Sunny."}],
}


def response(output=None, status="completed", **extra):
    return {
        "id": "resp_1",
        "status": status,
        "output": copy.deepcopy(output if output is not None else [MESSAGE]),
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_tokens_details": {"cached_tokens": 50},
            "output_tokens_details": {"reasoning_tokens": 10},
        },
        **extra,
    }


def mock_http(payload, status=200):
    client = AsyncMock()
    client.post.return_value = httpx.Response(
        status, json=payload, request=httpx.Request("POST", "https://test/v1/responses")
    )
    client.__aenter__.return_value = client
    return client


async def test_reasoning_parallel_roundtrip_and_wire_format():
    provider = OpenAIResponsesProvider(api_key="test")
    client = mock_http(response([REASONING, *CALLS]))
    history = [{"role": "system", "content": "Be helpful"}, {"role": "user", "content": "Weather?"}]
    with patch("httpx.AsyncClient", return_value=client):
        first = await provider.complete(MODEL, history, tools=TOOLS, reasoning_effort="medium")
        request = client.post.call_args.kwargs["json"]
        assert client.post.call_args.args[0] == "https://api.openai.com/v1/responses"
        assert request["store"] is False
        assert request["include"] == ["reasoning.encrypted_content"]
        assert request["reasoning"] == {"effort": "medium"}
        assert "messages" not in request
        assert request["tools"][0]["strict"] is False
        assert "units" not in request["tools"][0]["parameters"]["required"]
        assert [tc["id"] for tc in first["tool_calls"]] == ["call_0", "call_1"]
        assert first["finish_reason"] == "tool_calls"
        assert first["usage"]["cached_tokens"] == 50
        history.append(assistant_message(first))
        history.extend({"role": "tool", "tool_call_id": tc["id"], "content": "sunny"} for tc in first["tool_calls"])
        client.post.return_value = mock_http(response()).post.return_value
        second = await provider.complete(MODEL, history, tools=TOOLS)
        request = client.post.call_args.kwargs["json"]
        assert request["input"][2:5] == [REASONING, *CALLS]
        assert request["input"][5:] == [
            {"type": "function_call_output", "call_id": f"call_{i}", "output": "sunny"} for i in range(2)
        ]
        assert second["content"] == "Sunny."
        assert "reasoning" not in request


@pytest.mark.parametrize(
    "url,expected",
    [
        ("", "https://api.openai.com/v1"),
        ("https://test", "https://test/v1"),
        ("https://test/v1/", "https://test/v1"),
        ("https://test/custom/v1/responses", "https://test/custom/v1"),
    ],
)
def test_endpoint_and_embeddings(url, expected):
    provider = OpenAIResponsesProvider(url=url)
    assert provider._responses_url() == expected + "/responses"
    assert provider._embeddings_url() == expected + "/embeddings"


async def test_default_and_named_model_forward_effort(config):
    config = dataclasses.replace(
        config,
        providers={"oai": ProviderConfig(type="openai-responses", api_key="test")},
        model_configs={"luna": ModelConfig(provider="oai", model=MODEL, reasoning_effort="low")},
        default_model="luna",
    )
    init_providers(config)
    client = mock_http(response())
    with patch("httpx.AsyncClient", return_value=client):
        await call_llm(config, [], model_name="luna")
        assert client.post.call_args.kwargs["json"]["reasoning"]["effort"] == "low"
        await call_llm(config, [])
        assert client.post.call_args.kwargs["json"]["reasoning"]["effort"] == "low"


class EventSource:
    def __init__(self, events, *, error=None, waiting=None):
        self.events, self.error, self.waiting = events, error, waiting
        self.response = httpx.Response(200)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def aiter_sse(self):
        for event in self.events:
            yield type("Event", (), {"data": json.dumps(event)})()
        if self.waiting:
            self.waiting.set()
            await asyncio.Event().wait()
        if self.error:
            raise self.error


def call_events():
    events = []
    for i, item in enumerate([REASONING, *CALLS]):
        initial = {**item, "arguments": ""} if item["type"] == "function_call" else item
        events.append({"type": "response.output_item.added", "output_index": i, "item": initial})
    for i, item in enumerate(CALLS, 1):
        events.extend(
            [
                {
                    "type": "response.function_call_arguments.delta",
                    "output_index": i,
                    "item_id": item["id"],
                    "delta": item["arguments"],
                },
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": i,
                    "item_id": item["id"],
                    "arguments": item["arguments"],
                },
                {"type": "response.output_item.done", "output_index": i, "item": item},
            ]
        )
    return events


async def test_streaming_parallel_callbacks_and_authoritative_items():
    chunks = []

    async def on_chunk(kind, data):
        chunks.append((kind, data))

    provider = OpenAIResponsesProvider()
    payload = response([REASONING, *CALLS])
    with patch(
        "httpx_sse.aconnect_sse",
        return_value=EventSource([*call_events(), {"type": "response.completed", "response": payload}]),
    ):
        result = await provider.complete(MODEL, [], streaming=True, on_chunk=on_chunk)
    assert result["finish_reason"] == "tool_calls"
    assert [tc["function"]["arguments"] for tc in result["tool_calls"]] == [tc["arguments"] for tc in CALLS]
    assert [kind for kind, _ in chunks].count("tool_call_start") == 2
    assert [kind for kind, _ in chunks].count("tool_call_end") == 2
    assert chunks[-1][0] == "done"
    assert result["provider_data"]["openai_responses"]["output"] == payload["output"]


@pytest.mark.parametrize("terminal,reason", [(None, "error"), ("failed", "error"), ("incomplete", "length")])
async def test_complete_looking_calls_require_successful_terminal(terminal, reason):
    events = call_events()
    if terminal:
        events.append(
            {
                "type": "response." + terminal,
                "response": response([REASONING, *CALLS], terminal, incomplete_details={"reason": "max_output_tokens"}),
            }
        )
    with patch("httpx_sse.aconnect_sse", return_value=EventSource(events)):
        result = await OpenAIResponsesProvider().complete(MODEL, [], streaming=True)
    assert result["finish_reason"] == reason
    assert "provider_data" not in result


async def test_cancel_while_stream_stalls():
    waiting, cancel = asyncio.Event(), asyncio.Event()
    with patch("httpx_sse.aconnect_sse", return_value=EventSource(call_events(), waiting=waiting)):
        task = asyncio.create_task(OpenAIResponsesProvider().complete(MODEL, [], streaming=True, cancel_event=cancel))
        await asyncio.wait_for(waiting.wait(), 1)
        cancel.set()
        result = await asyncio.wait_for(task, 1)
    assert result["finish_reason"] == "cancelled"
    assert "provider_data" not in result


async def test_multimodal_instructions_legacy_calls_and_refusal():
    provider = OpenAIResponsesProvider()
    client = mock_http(response([{**MESSAGE, "content": [{"type": "refusal", "refusal": "Cannot help."}]}]))
    history = [
        {"role": "system", "content": "first"},
        {"role": "developer", "content": "second"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc", "detail": "low"}},
            ],
        },
        {
            "role": "assistant",
            "content": "Checking",
            "tool_calls": [{"id": "old", "function": {"name": "weather", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "old", "content": "sunny"},
    ]
    with patch("httpx.AsyncClient", return_value=client):
        result = await provider.complete(MODEL, history)
    items = client.post.call_args.kwargs["json"]["input"]
    assert [item.get("role") for item in items[:3]] == ["system", "developer", "user"]
    assert items[2]["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64,abc", "detail": "low"}
    assert items[-2]["call_id"] == items[-1]["call_id"] == "old"
    assert result["content"] == "Cannot help."


async def test_replay_provenance_edits_and_switching_back():
    provider = OpenAIResponsesProvider(name="primary")
    client = mock_http(response([REASONING, MESSAGE]))
    with patch("httpx.AsyncClient", return_value=client):
        message = assistant_message(await provider.complete(MODEL, []))
    original = copy.deepcopy(message)
    assert provider._input(MODEL, [message]) == [REASONING, MESSAGE]
    for foreign in [
        OpenAIResponsesProvider(name="other"),
        OpenAIResponsesProvider(name="primary", url="https://other/v1"),
    ]:
        assert foreign._input(MODEL, [message]) == [{"role": "assistant", "content": "Sunny."}]
    assert provider._input("other-model", [message]) == [{"role": "assistant", "content": "Sunny."}]
    assert message == original  # switching providers never mutates the archive
    assert provider._input(MODEL, [message]) == [REASONING, MESSAGE]  # switch back
    message["content"] = "Edited answer"
    assert provider._input(MODEL, [message]) == [{"role": "assistant", "content": "Edited answer"}]


async def test_streaming_text_and_named_effort(config):
    config = dataclasses.replace(
        config,
        providers={"oai": ProviderConfig(type="openai-responses")},
        model_configs={"luna": ModelConfig(provider="oai", model=MODEL, reasoning_effort="medium")},
        default_model="luna",
    )
    init_providers(config)
    chunks = []
    events = [
        {"type": "response.output_text.delta", "delta": "Sunny."},
        {"type": "response.output_item.done", "output_index": 0, "item": MESSAGE},
        {"type": "response.completed", "response": response()},
    ]
    with patch("httpx_sse.aconnect_sse", return_value=EventSource(events)) as sse:
        result = await call_llm_streaming(config, [], on_chunk=lambda kind, data: chunks.append((kind, data)))
    assert sse.call_args.kwargs["json"]["reasoning"] == {"effort": "medium"}
    assert result["content"] == "Sunny."
    assert chunks[0] == ("text", "Sunny.")
    assert result["provider_data"]["openai_responses"]["output"][0]["phase"] == "final_answer"


@pytest.mark.parametrize("status", [400, 401])
async def test_http_client_errors_do_not_retry(status):
    client = mock_http({"error": {"message": "bad request"}}, status)
    with (
        patch("httpx.AsyncClient", return_value=client),
        pytest.raises(RuntimeError, match=f"LLM API error \\({status}\\)"),
    ):
        await OpenAIResponsesProvider().complete(MODEL, [])
    assert client.post.call_count == 1


@pytest.mark.parametrize("status", [429, 500])
async def test_retryable_errors_before_output(status):
    client = mock_http(response())
    client.post.side_effect = [mock_http({"error": "retry"}, status).post.return_value, client.post.return_value]
    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("decafclaw.llm.providers.openai_responses._cancellable_sleep", new_callable=AsyncMock) as wait,
    ):
        result = await OpenAIResponsesProvider().complete(MODEL, [])
    assert result["content"] == "Sunny."
    assert client.post.call_count == 2
    wait.assert_awaited_once()


async def test_retry_exhaustion_is_bounded():
    client = mock_http({"error": "retry"}, 429)
    with (
        patch("httpx.AsyncClient", return_value=client),
        patch("decafclaw.llm.providers.openai_responses._cancellable_sleep", new_callable=AsyncMock),
        pytest.raises(RuntimeError, match="after 3 retries"),
    ):
        await OpenAIResponsesProvider().complete(MODEL, [])
    assert client.post.call_count == 4


async def test_disconnect_preserves_partial_but_never_retries_callbacks():
    chunks = []
    with patch("httpx_sse.aconnect_sse", return_value=EventSource(call_events(), error=httpx.ReadError("lost"))) as sse:
        result = await OpenAIResponsesProvider().complete(
            MODEL, [], streaming=True, on_chunk=lambda kind, data: chunks.append((kind, data))
        )
    assert result["finish_reason"] == "error"
    assert len(result["tool_calls"]) == 2
    assert sse.call_count == 1
    assert "provider_data" not in result


async def test_timeout_before_output_raises():
    client = mock_http(response())
    client.post.side_effect = httpx.ReadTimeout("timeout")
    with patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.ReadTimeout):
        await OpenAIResponsesProvider().complete(MODEL, [], timeout=1)


@pytest.mark.parametrize(
    "status,reason,expected",
    [
        ("incomplete", "content_filter", "error"),
        ("incomplete", "max_output_tokens", "length"),
        ("failed", None, "error"),
        ("cancelled", None, "cancelled"),
    ],
)
async def test_nonstream_terminal_status(status, reason, expected):
    client = mock_http(response(CALLS, status, incomplete_details={"reason": reason}))
    with patch("httpx.AsyncClient", return_value=client):
        result = await OpenAIResponsesProvider().complete(MODEL, [])
    assert result["finish_reason"] == expected
    assert "provider_data" not in result


async def test_cancelled_before_request():
    cancel = asyncio.Event()
    cancel.set()
    with patch("httpx.AsyncClient") as client:
        result = await OpenAIResponsesProvider().complete(MODEL, [], cancel_event=cancel)
    client.assert_not_called()
    assert result["finish_reason"] == "cancelled"


async def test_other_providers_do_not_send_replay_data():
    from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
    from decafclaw.llm.providers.vertex import _build_request_body

    history = [
        {
            "role": "assistant",
            "content": "Sunny.",
            "provider_data": {"openai_responses": {"output": [REASONING, MESSAGE]}},
        }
    ]
    client = mock_http({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    with patch("httpx.AsyncClient", return_value=client):
        await OpenAICompatProvider("https://test/v1").complete("other", history)
    assert "provider_data" not in client.post.call_args.kwargs["json"]["messages"][0]
    assert "opaque" not in json.dumps(_build_request_body(history))
    assert "provider_data" in history[0]


def test_model_effort_loader_and_validation():
    from decafclaw.config import _load_model_configs

    model = _load_model_configs({"luna": {"model": MODEL, "reasoning_effort": "low"}})["luna"]
    assert model.reasoning_effort == "low"
    with pytest.raises(ValueError, match="Invalid reasoning_effort"):
        _load_model_configs({"luna": {"reasoning_effort": "bogus"}})


async def test_effort_on_other_provider_is_rejected(config):
    config = dataclasses.replace(
        config,
        providers={"vertex": ProviderConfig(type="vertex")},
        model_configs={"luna": ModelConfig(provider="vertex", model=MODEL, reasoning_effort="low")},
        default_model="luna",
    )
    init_providers(config)
    with pytest.raises(ValueError, match="requires an openai-responses provider"):
        await call_llm(config, [])


async def test_stream_error_before_output_surfaces_message():
    with (
        patch(
            "httpx_sse.aconnect_sse", return_value=EventSource([{"type": "error", "message": "Use regional hostname"}])
        ),
        pytest.raises(RuntimeError, match="Use regional hostname"),
    ):
        await OpenAIResponsesProvider().complete(MODEL, [], streaming=True)


async def test_auxiliary_model_effort_follows_bound_client(ctx):
    ctx.config = dataclasses.replace(
        ctx.config,
        providers={"oai": ProviderConfig(type="openai-responses")},
        model_configs={
            "primary": ModelConfig(provider="oai", model=MODEL, reasoning_effort="medium"),
            "aux": ModelConfig(provider="oai", model=MODEL, reasoning_effort="low"),
        },
        default_model="primary",
        auxiliary_model="aux",
    )
    init_providers(ctx.config)
    client = mock_http(response())
    with patch("httpx.AsyncClient", return_value=client):
        await ctx.aux_llm().complete([])
    assert client.post.call_args.kwargs["json"]["reasoning"] == {"effort": "low"}


async def test_embeddings_use_separate_endpoint():
    client = mock_http({"data": [{"embedding": [0.1, 0.2]}]})
    provider = OpenAIResponsesProvider(url="https://us.api.openai.com/v1/responses")
    with patch("httpx.AsyncClient", return_value=client):
        result = await provider.embed("embedding-model", "sample")
    assert result == [0.1, 0.2]
    assert client.post.call_args.args[0] == "https://us.api.openai.com/v1/embeddings"
    assert client.post.call_args.kwargs["json"] == {"model": "embedding-model", "input": "sample"}


async def test_interleaved_argument_events_resolve_item_id():
    events = call_events()
    # IDs remain authoritative when a proxy omits output_index on deltas.
    events = [
        {key: value for key, value in event.items() if key != "output_index"}
        if event["type"].startswith("response.function_call_arguments.")
        else event
        for event in events
    ]
    events.append({"type": "response.completed", "response": response([REASONING, *CALLS])})
    with patch("httpx_sse.aconnect_sse", return_value=EventSource(events)):
        result = await OpenAIResponsesProvider().complete(MODEL, [], streaming=True)
    assert [json.loads(call["function"]["arguments"])["city"] for call in result["tool_calls"]] == ["Paris", "London"]
