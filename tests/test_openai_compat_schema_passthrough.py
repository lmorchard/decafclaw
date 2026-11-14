"""Provider schema pass-through: non-Vertex adapters must not mutate canonical tool schemas (#1147).

The Vertex adapter is the only provider that adapts (strips) tool parameter
schemas. The OpenAI-compatible and OpenAI Responses adapters pass the schema
through, so this file pins two properties for each:

- The wire request carries the parameter schema unchanged (no silent strip or
  rewrite — a pass-through adapter that starts mutating would change what the
  model sees with no review trail).
- The canonical registry object is never mutated in place: the adapter must
  derive its wire copy, so one provider's adaptation can't leak into another
  provider's input or the registry contract.

All HTTP is mocked below the completion guard, so this file runs offline.
"""

import copy
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
from decafclaw.llm.providers.openai_responses import OpenAIResponsesProvider

# Bypass the completion guard only because every transport is mocked below.
pytestmark = pytest.mark.live_llm

# A full JSON-Schema parameter shape: every construct the Vertex adapter strips
# (``$ref``/``$defs``, ``multipleOf``, ``propertyNames``) plus legitimate
# constraints. If a pass-through adapter rewrites any of it, the deep-equality
# assertions below fail.
SCHEMA = {
    "type": "object",
    "$defs": {"Name": {"type": "string", "minLength": 1}},
    "properties": {
        "name": {"$ref": "#/$defs/Name"},
        "count": {"type": "number", "multipleOf": 5},
        "flags": {
            "type": "object",
            "propertyNames": {"pattern": "^[a-z]+$"},
            "additionalProperties": {"type": "boolean"},
        },
    },
    "required": ["name", "count"],
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "example_tool",
            "description": "Example tool with a full JSON-Schema shape",
            "parameters": copy.deepcopy(SCHEMA),
        },
    }
]

MESSAGES = [{"role": "user", "content": "hello"}]


def _mock_client(payload, status=200):
    client = AsyncMock()
    client.post.return_value = httpx.Response(status, json=payload, request=httpx.Request("POST", "https://test/v1"))
    client.__aenter__.return_value = client
    return client


def _chat_payload():
    return {
        "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _responses_payload():
    return {
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


@pytest.mark.asyncio
async def test_openai_compat_passes_schema_through_unmutated():
    """openai_compat sends the tools list by reference — pin that it is unchanged on the wire
    and that the canonical object survives intact."""
    provider = OpenAICompatProvider(url="https://test")
    canonical = copy.deepcopy(TOOLS)
    client = _mock_client(_chat_payload())
    with patch("httpx.AsyncClient", return_value=client):
        await provider.complete("test-model", MESSAGES, tools=TOOLS)

    wire = client.post.call_args.kwargs["json"]["tools"]
    assert wire == canonical  # no strip, no rewrite on the wire
    assert TOOLS == canonical  # registry contract not mutated in place


@pytest.mark.asyncio
async def test_openai_responses_passes_schema_through_unmutated():
    """openai_responses derives its wire tools via deepcopy — pin that parameters are unchanged
    and the canonical object survives intact."""
    provider = OpenAIResponsesProvider(api_key="test")
    canonical = copy.deepcopy(TOOLS)
    client = _mock_client(_responses_payload())
    with patch("httpx.AsyncClient", return_value=client):
        await provider.complete("test-model", MESSAGES, tools=TOOLS)

    wire = client.post.call_args.kwargs["json"]["tools"]
    assert len(wire) == 1
    # The Responses API wraps function entries differently, but the parameter
    # schema itself must be byte-for-byte what the registry holds.
    assert wire[0]["parameters"] == canonical[0]["function"]["parameters"]
    assert TOOLS == canonical  # registry contract not mutated in place
