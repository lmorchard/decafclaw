"""LLM provider types — protocol and shared type definitions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Protocol

# Provider type constants — used in config.json "providers" section
PROVIDER_OPENAI_COMPAT = "openai-compat"
PROVIDER_OPENAI = "openai"
PROVIDER_OPENAI_RESPONSES = "openai-responses"
PROVIDER_VERTEX = "vertex"
# Legacy alias
PROVIDER_LITELLM = "litellm"
# Test-only: replayed in-memory provider (decafclaw.testing.ScriptedProvider).
# Registered directly via the registry — init_providers never instantiates it.
PROVIDER_SCRIPTED = "scripted"

# Completion finish reason constants
FINISH_REASON_STOP = "stop"
FINISH_REASON_TOOL_CALLS = "tool_calls"
FINISH_REASON_LENGTH = "length"
FINISH_REASON_ERROR = "error"
FINISH_REASON_CANCELLED = "cancelled"

# Callback type for streaming chunks
# on_chunk(chunk_type, data) where chunk_type is one of:
#   "text", "thinking", "tool_call_start", "tool_call_delta", "tool_call_end", "done"
StreamCallback = Callable[[str, Any], Any]


class ContextLengthExceededError(Exception):
    """Raised when the LLM provider rejects the request due to token limits."""

    pass


class LLMRetryExhaustedError(Exception):
    """Raised when a provider adapter's bounded retry budget is fully spent.

    Carries the terminal retry status/body so the agent loop's recovery policy
    can distinguish "retries exhausted, nothing committed" (stop as an explicit
    uncertain outcome) from a live retry decision (#1144).
    """

    def __init__(
        self, message: str, *, status: int | None = None, body: str | None = None, retry_after: str | None = None
    ):
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after


class Provider(Protocol):
    """Interface for LLM providers.

    Each provider handles auth, request formatting, and response normalization
    for a specific API (OpenAI, Vertex/Gemini, etc.).

    All methods return data in the internal format:
    - complete() returns {"content", "tool_calls", "role", "usage", "finish_reason"}
    - optional provider_data carries private replay state into assistant history
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
          - "thinking": str or None
          - "tool_calls": list or None
          - "role": "assistant"
          - "usage": dict or None
          - "finish_reason": str | None (e.g. "stop", "length", "error", "cancelled")
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

    async def get_model_info(
        self,
        model: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Get model metadata (e.g. token limits), or None if unsupported."""
        return None


@dataclass
class DiscoveredModel:
    """One model entry returned by a provider's model listing.

    Capability fields are tri-state: True/False when the gateway reports
    the fact, None when it does not. ``None`` never means "capable" —
    unknown capabilities gate auto-enablement and are surfaced to the
    catalog UI so the user can opt in deliberately (#1211).

    ``confirmed`` marks a live-confirmed id: True when the gateway itself
    listed the model (the default), False for curated static-list guesses
    no live listing has verified — those land disabled by default so an
    unserved id never surfaces enabled and fails on first use.
    """

    id: str
    chat: bool | None = None
    tools: bool | None = None
    context_window_size: int | None = None
    owned_by: str = ""
    confirmed: bool = True
