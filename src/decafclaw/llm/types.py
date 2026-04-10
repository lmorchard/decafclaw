"""LLM provider types — protocol and shared type definitions."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Protocol

# Provider type constants — used in config.json "providers" section
PROVIDER_OPENAI_COMPAT = "openai-compat"
PROVIDER_OPENAI = "openai"
PROVIDER_VERTEX = "vertex"
# Legacy alias
PROVIDER_LITELLM = "litellm"

# Callback type for streaming chunks
# on_chunk(chunk_type, data) where chunk_type is one of:
#   "text", "tool_call_start", "tool_call_delta", "tool_call_end", "done"
StreamCallback = Callable[[str, Any], Any]


class Provider(Protocol):
    """Interface for LLM providers.

    Each provider handles auth, request formatting, and response normalization
    for a specific API (OpenAI, Vertex/Gemini, etc.).

    All methods return data in the internal format:
    - complete() returns {"content", "tool_calls", "role", "usage"}
    - embed() returns a list of floats or None
    """

    async def complete(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        streaming: bool = False,
        on_chunk: StreamCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Call the LLM and return the response.

        Returns a dict with:
          - "content": str or None
          - "tool_calls": list or None
          - "role": "assistant"
          - "usage": dict or None
        """
        ...

    async def embed(
        self,
        model: str,
        text: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> list[float] | None:
        """Embed text and return the vector, or None on failure."""
        ...
