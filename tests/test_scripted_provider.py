"""Unit tests for decafclaw.testing.ScriptedProvider (issue #923).

These exercise the provider contract directly (no agent runtime): response
replay, streaming chunk sequences, error/cancel semantics, request capture,
request-aware factories, and the registry helpers.
"""

from __future__ import annotations

import asyncio

import pytest

from decafclaw.testing import (
    ScriptedProvider,
    ScriptedProviderError,
    ScriptedResponse,
    ScriptExhaustedError,
    UnexpectedCallError,
    register_scripted_provider,
    unregister_scripted_provider,
)

USER_MSG = [{"role": "user", "content": "hello"}]


async def run_streaming(provider: ScriptedProvider, **kwargs):
    """Run provider.complete(streaming=True) and collect (chunk_type, data)."""
    chunks: list[tuple[str, object]] = []

    async def on_chunk(chunk_type: str, data) -> None:
        chunks.append((chunk_type, data))

    response = await provider.complete("scripted-model", USER_MSG, streaming=True, on_chunk=on_chunk, **kwargs)
    return response, chunks


class TestReplay:
    async def test_replays_queued_responses_in_order_and_captures_requests(self):
        provider = ScriptedProvider(
            [
                ScriptedResponse.text("first"),
                ScriptedResponse.text("second", finish_reason="length"),
            ]
        )
        r1 = await provider.complete("m", USER_MSG)
        r2 = await provider.complete("m", USER_MSG, tools=[{"type": "function"}])

        assert r1["content"] == "first"
        assert r1["finish_reason"] == "stop"
        assert r2["content"] == "second"
        assert r2["finish_reason"] == "length"

        assert len(provider.requests) == 2
        assert provider.requests[0].model == "m"
        assert provider.requests[0].call_index == 0
        assert provider.requests[0].streaming is False
        assert provider.requests[0].tools is None
        assert provider.requests[0].messages == USER_MSG
        assert provider.requests[1].call_index == 1
        assert provider.requests[1].tools == [{"type": "function"}]

    async def test_explicit_streaming_flag_recorded(self):
        provider = ScriptedProvider([ScriptedResponse.text("ok")])
        await provider.complete("m", USER_MSG, streaming=True, on_chunk=_noop)
        assert provider.requests[0].streaming is True

    async def test_extend_supports_single_response_and_nested_sequence(self):
        provider = ScriptedProvider([ScriptedResponse.text("first")])
        provider.extend(ScriptedResponse.text("second"))
        provider.extend([ScriptedResponse.text("third"), ScriptedResponse.text("fourth")])
        r1 = await provider.complete("m", USER_MSG)
        r2 = await provider.complete("m", USER_MSG)
        r3 = await provider.complete("m", USER_MSG)
        r4 = await provider.complete("m", USER_MSG)
        assert [r["content"] for r in (r1, r2, r3, r4)] == ["first", "second", "third", "fourth"]


class TestStreaming:
    async def test_standard_chunk_sequence_with_tool_call(self):
        provider = ScriptedProvider(
            [
                ScriptedResponse(
                    content="thinking then calling",
                    thinking="pondering",
                    tool_calls=[
                        {
                            "id": "call_9",
                            "type": "function",
                            "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                        }
                    ],
                    usage={"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12},
                )
            ]
        )
        response, chunks = await run_streaming(provider)

        types = [ct for ct, _ in chunks]
        assert types == [
            "thinking",
            "text",
            "tool_call_start",
            "tool_call_delta",
            "tool_call_end",
            "done",
        ]
        start = dict(chunks[2][1])
        assert start == {"index": 0, "name": "echo", "id": "call_9"}
        assert dict(chunks[3][1]) == {"index": 0, "arguments_delta": '{"text": "hi"}'}
        assert dict(chunks[4][1]) == {"index": 0, "name": "echo", "arguments": '{"text": "hi"}'}

        # Final response matches the non-streaming body.
        assert response["content"] == "thinking then calling"
        assert response["thinking"] == "pondering"
        assert response["tool_calls"][0]["id"] == "call_9"
        assert response["finish_reason"] == "tool_calls"
        assert response["usage"] == {"prompt_tokens": 3, "completion_tokens": 9, "total_tokens": 12}

    async def test_text_only_stream_finishes_stop(self):
        provider = ScriptedProvider([ScriptedResponse.text("just text")])
        response, chunks = await run_streaming(provider)
        assert [ct for ct, _ in chunks] == ["text", "done"]
        assert response["finish_reason"] == "stop"

    async def test_explicit_stream_chunks_define_delivery_and_final(self):
        provider = ScriptedProvider(
            [
                ScriptedResponse(
                    content="full body",
                    stream_chunks=[
                        ("text", "par"),
                        ("text", "tial"),
                        ("error", "stream cut"),
                    ],
                )
            ]
        )
        response, chunks = await run_streaming(provider)
        assert [ct for ct, _ in chunks] == ["text", "text", "error", "done"]
        # Final content is what was emitted, not the response body.
        assert response["content"] == "partial"
        assert response["finish_reason"] == "error"

    async def test_error_without_content_raises(self):
        provider = ScriptedProvider([ScriptedResponse(error="boom")])
        with pytest.raises(ScriptedProviderError, match="boom"):
            await provider.complete("m", USER_MSG)

        provider2 = ScriptedProvider([ScriptedResponse(stream_chunks=[("error", "mid-stream cut")])])
        with pytest.raises(ScriptedProviderError, match="mid-stream cut"):
            await run_streaming(provider2)

    async def test_non_streaming_error_raises(self):
        provider = ScriptedProvider([ScriptedResponse(error="server exploded")])
        with pytest.raises(ScriptedProviderError, match="server exploded"):
            await provider.complete("m", USER_MSG)

    async def test_on_chunk_subscriber_error_does_not_abort_stream(self, caplog):
        import logging

        provider = ScriptedProvider([ScriptedResponse.text("streamed text")])
        chunks = []

        def broken_chunk(chunk_type: str, data):
            if chunk_type == "text":
                raise ValueError("callback crashed")
            chunks.append((chunk_type, data))

        with caplog.at_level(logging.DEBUG, logger="decafclaw.testing.scripted_provider"):
            response = await provider.complete("m", USER_MSG, streaming=True, on_chunk=broken_chunk)

        assert response["content"] == "streamed text"
        assert [ct for ct, _ in chunks] == ["done"]
        assert any("callback failed" in record.message for record in caplog.records)


class TestScriptExhaustion:
    async def test_raises_with_actionable_message(self):
        provider = ScriptedProvider([ScriptedResponse.text("only one")])
        await provider.complete("m", USER_MSG)
        with pytest.raises(ScriptExhaustedError) as exc_info:
            await provider.complete("m", USER_MSG)

        message = str(exc_info.value)
        assert "call #1" in message
        assert "2 calls" in message
        assert "ScriptedResponse" in message
        assert provider.next_call_index == 2


class TestCancel:
    async def test_cancel_before_delivery_returns_cancelled(self):
        provider = ScriptedProvider([ScriptedResponse.text("never delivered")])
        cancel = asyncio.Event()
        cancel.set()
        response = await provider.complete("m", USER_MSG, cancel_event=cancel)
        assert response["finish_reason"] == "cancelled"

    async def test_cancel_while_held_returns_cancelled(self):
        hold = asyncio.Event()
        cancel = asyncio.Event()
        provider = ScriptedProvider([ScriptedResponse.text("held", hold=hold)])
        task = asyncio.create_task(
            provider.complete("m", USER_MSG, streaming=True, on_chunk=_noop, cancel_event=cancel)
        )
        cancel.set()
        response = await task
        assert response["finish_reason"] == "cancelled"

    async def test_streaming_cancel_between_chunks_keeps_partial(self):
        cancel = asyncio.Event()
        provider = ScriptedProvider(
            [
                ScriptedResponse(
                    content="one two three",
                    stream_chunks=[("text", "one"), ("text", "two"), ("text", "three")],
                )
            ]
        )

        async def on_chunk(chunk_type: str, data) -> None:
            if chunk_type == "text" and data == "two":
                cancel.set()

        response = await provider.complete("m", USER_MSG, streaming=True, on_chunk=on_chunk, cancel_event=cancel)
        assert response["content"] == "onetwo"
        assert response["finish_reason"] == "cancelled"


class TestFactories:
    async def test_request_aware_factory_selects_by_request(self):
        calls = []

        def factory(request):
            calls.append(request)
            if request.tools:
                return ScriptedResponse.text("saw tools")
            return ScriptedResponse.text("no tools")

        provider = ScriptedProvider(factory)
        r1 = await provider.complete("m", USER_MSG)
        r2 = await provider.complete("m", USER_MSG, tools=[{"type": "function", "function": {"name": "echo"}}])
        assert r1["content"] == "no tools"
        assert r2["content"] == "saw tools"
        assert len(provider.requests) == 2
        assert provider.requests[1].tools[0]["function"]["name"] == "echo"
        assert calls[0].call_index == 0

    async def test_factory_wrong_return_raises_unexpected_call(self):
        provider = ScriptedProvider(lambda request: "not a response")
        with pytest.raises(UnexpectedCallError, match="must return ScriptedResponse"):
            await provider.complete("m", USER_MSG)

    async def test_factory_wrong_return_with_empty_messages(self):
        provider = ScriptedProvider(lambda request: "not a response")
        with pytest.raises(UnexpectedCallError, match="must return ScriptedResponse"):
            await provider.complete("m", [])


class TestEmbeddingsAndModelInfo:
    async def test_embed_without_embedder_raises(self):
        provider = ScriptedProvider([ScriptedResponse.text("ok")])
        with pytest.raises(RuntimeError, match="embedder"):
            await provider.embed("m", "text")

    async def test_embed_uses_embedder(self):
        async def embed(model, text):
            return [0.1, 0.2, 0.3]

        provider = ScriptedProvider([ScriptedResponse.text("ok")], embedder=embed)
        assert await provider.embed("m", "text") == [0.1, 0.2, 0.3]

    async def test_get_model_info_returns_none(self):
        provider = ScriptedProvider([ScriptedResponse.text("ok")])
        assert await provider.get_model_info("m") is None


class TestRegistryHelpers:
    async def test_register_and_unregister(self):
        provider = register_scripted_provider("scripted-test", [ScriptedResponse.text("ok")])
        assert provider.name == "scripted-test"
        try:
            from decafclaw.llm.registry import get_provider

            assert get_provider("scripted-test") is provider
            result = await get_provider("scripted-test").complete("m", USER_MSG)
            assert result["content"] == "ok"
        finally:
            unregister_scripted_provider("scripted-test")
        with pytest.raises(KeyError):
            from decafclaw.llm.registry import get_provider

            get_provider("scripted-test")


async def _noop(*args, **kwargs) -> None:
    pass
