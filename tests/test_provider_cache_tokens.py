"""Prompt-cache token surfacing (#480 Phase 1).

Providers already receive cached-prompt-token counts but drop them. These
tests pin the normalization: every provider usage dict grows a ``cached_tokens``
key (0 when the provider reports none), so the agent merge point stays
provider-agnostic.
"""

import pytest

from decafclaw.llm.providers import openai_compat, vertex

# -- Vertex (Gemini usageMetadata.cachedContentTokenCount) --------------------


def test_vertex_parse_usage_extracts_cached_tokens():
    data = {
        "usageMetadata": {
            "promptTokenCount": 1000,
            "candidatesTokenCount": 50,
            "totalTokenCount": 1050,
            "cachedContentTokenCount": 800,
        }
    }
    usage = vertex._parse_usage(data)
    assert usage["prompt_tokens"] == 1000
    assert usage["cached_tokens"] == 800


def test_vertex_parse_usage_cached_defaults_zero():
    data = {"usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2,
                              "totalTokenCount": 12}}
    usage = vertex._parse_usage(data)
    assert usage["cached_tokens"] == 0


@pytest.mark.asyncio
async def test_vertex_streaming_usage_carries_cached():
    state = vertex._VertexStreamState()
    await state.process_chunk({
        "usageMetadata": {
            "promptTokenCount": 500,
            "candidatesTokenCount": 20,
            "totalTokenCount": 520,
            "cachedContentTokenCount": 300,
        }
    })
    assert state.usage["cached_tokens"] == 300


# -- OpenAI-compat (prompt_tokens_details.cached_tokens) ----------------------


def test_openai_compat_cached_tokens_helper():
    assert openai_compat._cached_tokens(
        {"prompt_tokens": 1200, "prompt_tokens_details": {"cached_tokens": 900}}
    ) == 900


def test_openai_compat_cached_tokens_absent_is_zero():
    assert openai_compat._cached_tokens({"prompt_tokens": 1200}) == 0
    assert openai_compat._cached_tokens(None) == 0


@pytest.mark.asyncio
async def test_openai_compat_streaming_usage_carries_cached():
    state = openai_compat._StreamState()
    await state.process_chunk({
        "choices": [],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 900},
        },
    })
    assert state.usage["cached_tokens"] == 900
