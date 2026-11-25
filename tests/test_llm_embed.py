"""Tests for the shared embedding HTTP transport in llm/embed.py (#1319).

Covers the transport itself (retry on 429, fail-open on error, sync/async
header sources) and the vertex adoption (URL/body/extraction wiring + delegation).

HTTP is mocked below the provider with a real ``httpx.Response`` so
``.status_code``/``.json()``/``.raise_for_status()`` behave like the real thing;
backoff sleeps are patched (never a fixed ``asyncio.sleep`` wait).
"""

import json
from unittest.mock import AsyncMock, patch

import httpx

from decafclaw.llm.embed import post_embedding

# ---------------------------------------------------------------------------
# httpx stand-ins
# ---------------------------------------------------------------------------


def _make_response(status_code: int, json_data=None) -> httpx.Response:
    """A real ``httpx.Response`` so status/json/raise_for_status behave natively."""
    request = httpx.Request("POST", "http://test")
    content = json.dumps(json_data).encode() if json_data is not None else b""
    return httpx.Response(status_code, request=request, content=content)


def _fake_client(responses):
    """Build an ``AsyncClient`` stand-in that pops canned responses, in order.

    Returns ``(client_cls, calls)``; patch ``decafclaw.llm.embed.httpx.AsyncClient``
    with ``client_cls``. Each attempt constructs a fresh client, but they share the
    response queue and call log so a multi-attempt sequence plays back in order.
    """
    queue = list(responses)
    calls: list[dict] = []

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, json=None, headers=None, timeout=None):
            calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            return queue.pop(0)

    return _Client, calls


def _raising_client(calls):
    """An ``AsyncClient`` stand-in whose ``post`` always raises a network error."""

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, json=None, headers=None, timeout=None):
            calls.append(url)
            raise httpx.ConnectError("boom")

    return _Client


def _extract_openai(data):
    """openai_compat / legacy response shape."""
    return data["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# Transport: success / retry / fail-open
# ---------------------------------------------------------------------------


async def test_success_returns_extracted_vector():
    client_cls, calls = _fake_client([_make_response(200, {"data": [{"embedding": [0.1, 0.2]}]})])
    with patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls):
        result = await post_embedding(
            url="http://test/embeddings",
            body={"model": "m", "input": "hi"},
            headers={"Authorization": "Bearer k"},
            extract=_extract_openai,
        )
    assert result == [0.1, 0.2]
    assert len(calls) == 1
    assert calls[0]["url"] == "http://test/embeddings"
    assert calls[0]["json"] == {"model": "m", "input": "hi"}
    assert calls[0]["headers"] == {"Authorization": "Bearer k"}


async def test_429_retries_then_succeeds():
    client_cls, calls = _fake_client(
        [
            _make_response(429),
            _make_response(200, {"data": [{"embedding": [1.0]}]}),
        ]
    )
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await post_embedding(url="http://test/embeddings", body={}, headers={}, extract=_extract_openai)
    assert result == [1.0]
    assert len(calls) == 2
    sleep.assert_awaited_once_with(1)  # 2**0


async def test_429_exhausted_returns_none():
    client_cls, calls = _fake_client([_make_response(429)] * 4)
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await post_embedding(url="http://test/embeddings", body={}, headers={}, extract=_extract_openai)
    assert result is None
    assert len(calls) == 4  # initial attempt + 3 retries
    assert [c.args[0] for c in sleep.await_args_list] == [1, 2, 4]


async def test_non_429_http_error_returns_none_without_retry():
    client_cls, calls = _fake_client([_make_response(500)])
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await post_embedding(url="http://test/embeddings", body={}, headers={}, extract=_extract_openai)
    assert result is None
    assert len(calls) == 1  # only 429 retries; a 500 fails open immediately
    sleep.assert_not_awaited()


async def test_network_error_returns_none():
    calls: list = []
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", _raising_client(calls)),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await post_embedding(url="http://test/embeddings", body={}, headers={}, extract=_extract_openai)
    assert result is None
    sleep.assert_not_awaited()


async def test_unparseable_response_returns_none():
    # 200 but the body lacks the shape ``extract`` expects -> KeyError -> fail open.
    client_cls, calls = _fake_client([_make_response(200, {"unexpected": True})])
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await post_embedding(url="http://test/embeddings", body={}, headers={}, extract=_extract_openai)
    assert result is None
    sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# Transport: header sources (dict / sync callable / async callable)
# ---------------------------------------------------------------------------


async def test_headers_dict_passed_through():
    client_cls, calls = _fake_client([_make_response(200, {"data": [{"embedding": [1]}]})])
    with patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls):
        await post_embedding(url="u", body={}, headers={"Authorization": "Bearer k"}, extract=_extract_openai)
    assert calls[0]["headers"] == {"Authorization": "Bearer k"}


async def test_headers_sync_callable_resolved():
    client_cls, calls = _fake_client([_make_response(200, {"data": [{"embedding": [1]}]})])
    with patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls):
        await post_embedding(
            url="u", body={}, headers=lambda: {"Authorization": "Bearer sync"}, extract=_extract_openai
        )
    assert calls[0]["headers"] == {"Authorization": "Bearer sync"}


async def test_headers_async_callable_awaited():
    client_cls, calls = _fake_client([_make_response(200, {"data": [{"embedding": [1]}]})])

    async def headers():
        return {"Authorization": "Bearer async"}

    with patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls):
        await post_embedding(url="u", body={}, headers=headers, extract=_extract_openai)
    assert calls[0]["headers"] == {"Authorization": "Bearer async"}


async def test_headers_resolved_per_attempt():
    """A token is refreshed on every attempt: the header source runs once per try."""
    client_cls, calls = _fake_client(
        [
            _make_response(429),
            _make_response(200, {"data": [{"embedding": [1]}]}),
        ]
    )
    invocations: list = []

    async def headers():
        invocations.append(1)
        return {"Authorization": "Bearer t"}

    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock),
    ):
        await post_embedding(url="u", body={}, headers=headers, extract=_extract_openai)
    assert len(invocations) == 2  # one per attempt


# ---------------------------------------------------------------------------
# Vertex adoption: URL / body / extraction wiring + delegation
# ---------------------------------------------------------------------------


async def _fake_get_token(self):
    return "tok"


def _vertex_response(values):
    return _make_response(200, {"predictions": [{"embeddings": {"values": values}}]})


async def test_vertex_embed_success():
    from decafclaw.llm.providers.vertex import VertexProvider

    provider = VertexProvider(project="proj", region="global")
    client_cls, calls = _fake_client([_vertex_response([0.5, 0.6])])
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch.object(VertexProvider, "_get_token", _fake_get_token),
    ):
        result = await provider.embed("text-embedding-004", "hello")

    assert result == [0.5, 0.6]
    call = calls[0]
    assert call["url"].endswith("/models/text-embedding-004:predict")
    assert call["json"] == {"instances": [{"content": "hello"}]}
    assert call["headers"]["Authorization"] == "Bearer tok"


async def test_vertex_embed_429_retries_then_succeeds():
    from decafclaw.llm.providers.vertex import VertexProvider

    provider = VertexProvider(project="proj", region="global")
    client_cls, calls = _fake_client([_make_response(429), _vertex_response([1.0])])
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", client_cls),
        patch.object(VertexProvider, "_get_token", _fake_get_token),
        patch("decafclaw.llm.embed.asyncio.sleep", new_callable=AsyncMock) as sleep,
    ):
        result = await provider.embed("text-embedding-004", "hello")

    assert result == [1.0]
    assert len(calls) == 2
    sleep.assert_awaited_once_with(1)


async def test_vertex_embed_error_returns_none():
    from decafclaw.llm.providers.vertex import VertexProvider

    provider = VertexProvider(project="proj", region="global")
    calls: list = []
    with (
        patch("decafclaw.llm.embed.httpx.AsyncClient", _raising_client(calls)),
        patch.object(VertexProvider, "_get_token", _fake_get_token),
    ):
        result = await provider.embed("text-embedding-004", "hello")

    assert result is None


async def test_vertex_embed_delegates_to_shared_transport():
    """Pin the delegation contract: vertex.embed routes through post_embedding."""
    from decafclaw.llm.providers import vertex

    provider = vertex.VertexProvider(project="proj", region="global")
    with patch.object(vertex, "post_embedding", new=AsyncMock(return_value=[0.1])) as mock_post:
        await provider.embed("text-embedding-004", "hello")

    mock_post.assert_awaited_once()
    kwargs = mock_post.await_args.kwargs
    assert kwargs["url"].endswith("/models/text-embedding-004:predict")
    assert kwargs["body"] == {"instances": [{"content": "hello"}]}
    # The async header source is passed through (a callable, not a pre-resolved dict).
    assert callable(kwargs["headers"])
    # The vertex extractor maps a predictions payload to its values.
    assert kwargs["extract"]({"predictions": [{"embeddings": {"values": [9.0]}}]}) == [9.0]
