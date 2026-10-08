"""Opt-in, bounded Luna Responses tests. Run with -m integration -n 0."""

import base64
import json
import os
import struct
import zlib

import pytest
from dotenv import load_dotenv

from decafclaw.llm.history import assistant_message
from decafclaw.llm.providers.openai_responses import OpenAIResponsesProvider

load_dotenv()
pytestmark = pytest.mark.integration


def red_image():
    """Small synthetic image; no workspace/user data goes to the API."""

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = (b"\0" + b"\xff\0\0" * 32) * 32
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 32, 32, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
@pytest.mark.parametrize("streaming", [False, True])
async def test_luna_reasoning_tools_image_and_multiple_iterations(streaming):
    provider = OpenAIResponsesProvider(api_key=os.environ["OPENAI_API_KEY"], url=os.getenv("OPENAI_RESPONSES_URL", ""))
    tools = [
        {
            "type": "function",
            "function": {
                "name": "weather",
                "description": "Get weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            },
        }
    ]
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Use weather to get Paris weather. Also inspect this image."},
                {"type": "image_url", "image_url": {"url": red_image()}},
            ],
        }
    ]
    chunks = []
    for city in ("Paris", "London"):
        result = await provider.complete(
            "gpt-6-luna",
            history,
            tools=tools,
            reasoning_effort="medium",
            streaming=streaming,
            on_chunk=lambda kind, data: chunks.append((kind, data)),
            timeout=60,
            max_output_tokens=2048,
        )
        assert result["finish_reason"] == "tool_calls"
        assert result["provider_data"]["openai_responses"]["output"]
        # A reasoning-enabled model may spend zero reasoning tokens on a
        # simple call. When it emits reasoning, that state must be replayable.
        reasoning = [
            item for item in result["provider_data"]["openai_responses"]["output"] if item["type"] == "reasoning"
        ]
        assert all(item.get("encrypted_content") for item in reasoning)
        assert len(result["tool_calls"]) <= 3
        history.append(assistant_message(result))
        for call in result["tool_calls"]:
            assert call["function"]["name"] == "weather"
            assert json.loads(call["function"]["arguments"])["city"].lower() == city.lower()
            history.append(
                {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"city": city, "weather": "sunny"})}
            )
        if city == "Paris":
            history.append({"role": "user", "content": "Now use weather for London."})
    history.append({"role": "user", "content": "Summarize the weather for both cities and the image color briefly."})
    final = await provider.complete(
        "gpt-6-luna",
        history,
        reasoning_effort="medium",
        streaming=streaming,
        on_chunk=lambda kind, data: chunks.append((kind, data)),
        timeout=60,
        max_output_tokens=2048,
    )
    assert final["finish_reason"] == "stop"
    assert "paris" in final["content"].lower() and "london" in final["content"].lower()
    assert "red" in final["content"].lower()
    if streaming:
        assert any(kind == "tool_call_start" for kind, _ in chunks)
        assert any(kind == "text" for kind, _ in chunks)
