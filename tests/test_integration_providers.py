"""Integration tests for LLM providers — hit real APIs.

Run with: make test-integration
Requires credentials:
  - Vertex: ADC or GOOGLE_APPLICATION_CREDENTIALS + GCP project
  - OpenAI: OPENAI_API_KEY env var

Tests are skipped if credentials are not available.
"""

import json
import os

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _has_vertex_credentials():
    """Check if Vertex AI credentials are available."""
    try:
        import google.auth
        creds, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return bool(project)
    except Exception:
        return False


def _vertex_project():
    """Get the GCP project from ADC."""
    try:
        import google.auth
        _, project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return project
    except Exception:
        return ""


has_vertex = _has_vertex_credentials()
has_openai = bool(os.getenv("OPENAI_API_KEY"))


@pytest.fixture
def vertex_provider():
    from decafclaw.llm.providers.vertex import VertexProvider
    return VertexProvider(
        project=_vertex_project(),
        region=os.getenv("VERTEX_REGION", "us-central1"),
    )


@pytest.fixture
def openai_provider():
    from decafclaw.llm.providers.openai import OpenAIProvider
    return OpenAIProvider(api_key=os.getenv("OPENAI_API_KEY", ""))


@pytest.fixture
def openai_compat_provider():
    from decafclaw.llm.providers.openai_compat import OpenAICompatProvider
    url = os.getenv("OPENAI_COMPAT_URL", os.getenv("LITELLM_URL", ""))
    api_key = os.getenv("OPENAI_COMPAT_API_KEY", os.getenv("LITELLM_API_KEY", ""))
    if not url:
        pytest.skip("OPENAI_COMPAT_URL / LITELLM_URL not set")
    return OpenAICompatProvider(url=url, api_key=api_key)


# ---------------------------------------------------------------------------
# Vertex/Gemini
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not has_vertex, reason="No Vertex AI credentials")
class TestVertexProvider:
    MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")

    @pytest.mark.asyncio
    async def test_simple_completion(self, vertex_provider):
        """Basic non-streaming completion returns text."""
        result = await vertex_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "Say 'hello' and nothing else."}],
            streaming=False,
        )
        assert result["content"] is not None
        assert "hello" in result["content"].lower()
        assert result["role"] == "assistant"
        assert result["usage"] is not None

    @pytest.mark.asyncio
    async def test_streaming_completion(self, vertex_provider):
        """Streaming returns text via callbacks and final result."""
        chunks = []

        async def on_chunk(chunk_type, data):
            chunks.append((chunk_type, data))

        result = await vertex_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "Say 'world' and nothing else."}],
            streaming=True,
            on_chunk=on_chunk,
        )
        assert result["content"] is not None
        assert "world" in result["content"].lower()

        # Should have received text chunks and a done event
        types = [t for t, _ in chunks]
        assert "text" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_tool_call(self, vertex_provider):
        """Model can call a tool and return structured arguments."""
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        }]
        result = await vertex_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "What's the weather in Paris?"}],
            tools=tools,
            streaming=False,
        )
        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) >= 1
        tc = result["tool_calls"][0]
        assert tc["function"]["name"] == "get_weather"
        args = json.loads(tc["function"]["arguments"])
        assert "city" in args

    @pytest.mark.asyncio
    async def test_tool_call_streaming(self, vertex_provider):
        """Streaming tool calls emit start/end events."""
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                    },
                    "required": ["city"],
                },
            },
        }]
        chunks = []

        async def on_chunk(chunk_type, data):
            chunks.append((chunk_type, data))

        result = await vertex_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "What's the weather in Tokyo?"}],
            tools=tools,
            streaming=True,
            on_chunk=on_chunk,
        )
        assert result["tool_calls"] is not None

        types = [t for t, _ in chunks]
        assert "tool_call_start" in types
        assert "tool_call_end" in types

    @pytest.mark.asyncio
    async def test_system_message(self, vertex_provider):
        """System messages are translated to systemInstruction."""
        result = await vertex_provider.complete(
            self.MODEL,
            [
                {"role": "system", "content": "You are a pirate. Always say 'arrr'."},
                {"role": "user", "content": "Greet me."},
            ],
            streaming=False,
        )
        assert result["content"] is not None
        assert "arr" in result["content"].lower()

    @pytest.mark.asyncio
    async def test_multi_turn(self, vertex_provider):
        """Multi-turn conversation works with role translation."""
        result = await vertex_provider.complete(
            self.MODEL,
            [
                {"role": "user", "content": "My name is Alice."},
                {"role": "assistant", "content": "Nice to meet you, Alice!"},
                {"role": "user", "content": "What's my name?"},
            ],
            streaming=False,
        )
        assert result["content"] is not None
        assert "alice" in result["content"].lower()

    @pytest.mark.asyncio
    async def test_tool_response_roundtrip(self, vertex_provider):
        """Full tool call → tool response → final answer roundtrip."""
        tools = [{
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a fact",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                    },
                    "required": ["query"],
                },
            },
        }]

        # First turn: model should call the tool
        r1 = await vertex_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "Use the lookup tool to find the capital of France."}],
            tools=tools,
            streaming=False,
        )
        assert r1["tool_calls"] is not None
        tc = r1["tool_calls"][0]

        # Second turn: provide tool response, get final answer
        r2 = await vertex_provider.complete(
            self.MODEL,
            [
                {"role": "user", "content": "Use the lookup tool to find the capital of France."},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": r1["tool_calls"],
                },
                {
                    "role": "tool",
                    "name": tc["function"]["name"],
                    "content": json.dumps({"answer": "Paris"}),
                },
            ],
            tools=tools,
            streaming=False,
        )
        assert r2["content"] is not None
        assert "paris" in r2["content"].lower()

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_roundtrip(self, vertex_provider):
        """Multiple parallel tool calls + responses in a single turn."""
        tools = [
            {"type": "function", "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            }},
            {"type": "function", "function": {
                "name": "get_time",
                "description": "Get current time in a timezone",
                "parameters": {"type": "object",
                               "properties": {"timezone": {"type": "string"}},
                               "required": ["timezone"]},
            }},
        ]

        # Ask something that should trigger parallel calls
        r1 = await vertex_provider.complete(
            self.MODEL,
            [{"role": "user",
              "content": "What's the weather in Paris and the time in Tokyo? Use both tools."}],
            tools=tools,
            streaming=False,
        )
        assert r1["tool_calls"] is not None
        assert len(r1["tool_calls"]) >= 2

        # Send both tool responses back
        tool_msgs = []
        for tc in r1["tool_calls"]:
            tool_msgs.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["function"]["name"],
                "content": json.dumps({"result": f"mock data for {tc['function']['name']}"}),
            })

        r2 = await vertex_provider.complete(
            self.MODEL,
            [
                {"role": "user",
                 "content": "What's the weather in Paris and the time in Tokyo? Use both tools."},
                {"role": "assistant", "content": None, "tool_calls": r1["tool_calls"]},
                *tool_msgs,
            ],
            tools=tools,
            streaming=False,
        )
        assert r2["content"] is not None


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not has_openai, reason="OPENAI_API_KEY not set")
class TestOpenAIProvider:
    MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @pytest.mark.asyncio
    async def test_simple_completion(self, openai_provider):
        """Basic non-streaming completion."""
        result = await openai_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "Say 'hello' and nothing else."}],
            streaming=False,
        )
        assert result["content"] is not None
        assert "hello" in result["content"].lower()
        assert result["usage"] is not None

    @pytest.mark.asyncio
    async def test_streaming_completion(self, openai_provider):
        """Streaming returns text via callbacks."""
        chunks = []

        async def on_chunk(chunk_type, data):
            chunks.append((chunk_type, data))

        result = await openai_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "Say 'world' and nothing else."}],
            streaming=True,
            on_chunk=on_chunk,
        )
        assert result["content"] is not None
        types = [t for t, _ in chunks]
        assert "text" in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_tool_call(self, openai_provider):
        """Tool call with structured arguments."""
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                    },
                    "required": ["city"],
                },
            },
        }]
        result = await openai_provider.complete(
            self.MODEL,
            [{"role": "user", "content": "What's the weather in London?"}],
            tools=tools,
            streaming=False,
        )
        assert result["tool_calls"] is not None
        tc = result["tool_calls"][0]
        assert tc["function"]["name"] == "get_weather"
        args = json.loads(tc["function"]["arguments"])
        assert "city" in args
