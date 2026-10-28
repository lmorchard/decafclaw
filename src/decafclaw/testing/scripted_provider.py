"""Scripted in-memory LLM provider for deterministic integration tests.

Queue assistant responses (text, thinking, and/or tool calls) and the
provider replays them through the normal completion/streaming contract —
the same ``complete()`` entry point and the same ``on_chunk`` event
ordering as a real provider — so tests can exercise complete
orchestration (agent loop, tool execution, confirmations, stream
interruption, cancellation) without network access or model credentials.

Responses are queued in order. Each ``complete()`` call consumes one
entry:

- A ``ScriptedResponse`` is replayed as-is.
- A callable is a *request-aware factory*: it receives the captured
  ``ScriptedRequest`` and returns the ``ScriptedResponse`` to replay, so
  a test can select the response based on what the runtime actually sent
  (e.g. which tool list was offered on call #2). A bare callable (not
  wrapped in a list) is a persistent factory: it answers every
  ``complete()`` call.

When the queue runs out, ``ScriptExhaustedError`` is raised with an
actionable message (call index plus a digest of the requests already
made). A factory that does not return a ``ScriptedResponse`` raises
``UnexpectedCallError``.

Streaming mirrors real-provider behavior:

- Without an explicit ``stream_chunks`` list, the chunk sequence is
  derived from the response body (thinking → text → tool-call
  start/delta/end), then a ``done`` event.
- With an explicit ``stream_chunks`` list, exactly those
  ``(chunk_type, data)`` pairs are emitted, then ``done``. The final
  response dict is built from what was actually emitted.
- ``error`` simulates a stream dying. It may be an exception instance,
  an exception class, or a plain message string (raised as a
  ``ScriptedProviderError``). In streaming mode it is evaluated after
  the chunks are emitted: if text/thinking/tool calls were already
  accumulated, the provider converts it to
  ``finish_reason="error"`` with the partial content (like a real SSE
  disconnect); with nothing accumulated it propagates to the caller.
  An ``("error", message)`` pair in an explicit ``stream_chunks`` list
  kills the stream at that point (same semantics). In non-streaming
  mode it always raises.
- ``hold`` is a barrier: the provider awaits the event before delivering
  the response, racing ``cancel_event``. If the turn is cancelled while
  the provider is held, it returns a ``finish_reason="cancelled"``
  response. ``cancel_event`` is also checked between streamed chunks.

``embed()`` raises unless an ``embedder`` callable was configured — a
scripted model cannot embed, and a test that needs embeddings should say
so explicitly. ``get_model_info()`` returns ``None``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from ..llm import register_provider, unregister_provider
from ..llm.types import (
    FINISH_REASON_CANCELLED,
    FINISH_REASON_ERROR,
    FINISH_REASON_STOP,
    FINISH_REASON_TOOL_CALLS,
    PROVIDER_SCRIPTED,
    StreamCallback,
)

__all__ = [
    "PROVIDER_SCRIPTED",
    "ScriptedProvider",
    "ScriptedProviderError",
    "ScriptExhaustedError",
    "ScriptedRequest",
    "ScriptedResponse",
    "UnexpectedCallError",
    "register_scripted_provider",
    "unregister_scripted_provider",
]

log = logging.getLogger(__name__)


class ScriptedProviderError(RuntimeError):
    """Base error for scripted provider misuse."""


class ScriptExhaustedError(ScriptedProviderError):
    """Raised when ``complete()`` is called with no responses left."""


class UnexpectedCallError(ScriptedProviderError):
    """Raised when a request-aware factory does not answer a call."""


def _raise_scripted_error(error: Any) -> None:
    """Raise a scripted error value: an exception instance or class, or
    a plain message string (wrapped in ``ScriptedProviderError``)."""
    if isinstance(error, BaseException):
        raise error
    if isinstance(error, type) and issubclass(error, BaseException):
        raise error()
    if isinstance(error, str):
        raise ScriptedProviderError(f"scripted provider error: {error}")
    raise ScriptedProviderError(f"scripted provider error (unrecognized error value: {error!r})")


@dataclass
class ScriptedRequest:
    """A captured LLM request — exactly what the runtime sent."""

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    streaming: bool
    call_index: int


@dataclass
class ScriptedResponse:
    """One scripted assistant response, mirroring the internal
    completion format (``content``/``thinking``/``tool_calls``/``usage``).

    ``tool_calls`` entries use the standard shape::

        {"id": "call_1", "type": "function",
         "function": {"name": "...", "arguments": "<json string>"}}

    ``stream_chunks`` and ``hold`` control streaming delivery (see the
    module docstring). ``finish_reason=None`` derives to ``"tool_calls"``
    when the response has tool calls, else ``"stop"``.
    """

    content: str | None = None
    thinking: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    error: BaseException | type[BaseException] | str | None = None
    stream_chunks: list[tuple[str, Any]] | None = None
    hold: asyncio.Event | None = None

    @classmethod
    def text(cls, content: str, **kwargs: Any) -> "ScriptedResponse":
        """A plain text response."""
        return cls(content=content, **kwargs)

    @classmethod
    def tool_call(
        cls,
        name: str,
        arguments: dict[str, Any] | str,
        call_id: str = "call_1",
        **kwargs: Any,
    ) -> "ScriptedResponse":
        """A response with a single tool call (arguments json-stringified)."""

        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        return cls(
            tool_calls=[
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            ],
            **kwargs,
        )


def _exhausted_message(call_index: int, request: ScriptedRequest, past: list[ScriptedRequest]) -> str:
    def _last_user(req: ScriptedRequest) -> str:
        for msg in reversed(req.messages):
            if msg.get("role") == "user" and msg.get("content"):
                return str(msg["content"])[:80]
        return ""

    total = call_index + 1
    unit = "call" if total == 1 else "calls"
    lines = [
        f"ScriptedProvider exhausted: complete() call #{call_index} "
        f"(streaming={request.streaming}, model={request.model!r}, "
        f"{len(request.messages)} messages, {len(request.tools or [])} tools) "
        f"arrived but the script has no responses left — {total} {unit} total: "
        f"{call_index} answered, 1 unanswered.",
        "Queue entries are ScriptedResponse objects or request-aware factories; "
        "add a response for this call, or the call was unexpected — assert on provider.requests.",
    ]
    for i, req in enumerate(past):
        lines.append(
            f"  call #{i}: model={req.model!r}, {len(req.messages)} messages, "
            f"{len(req.tools or [])} tools, last user: {_last_user(req)!r}"
        )
    return "\n".join(lines)


class _StreamAccumulator:
    """Accumulates a stream from emitted chunks (mirrors
    OpenAICompatProvider._StreamState accumulation rules)."""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._thinking: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}

    @property
    def content(self) -> str:
        return "".join(self._text)

    @property
    def thinking(self) -> str:
        return "".join(self._thinking)

    @property
    def tool_calls(self) -> list[dict[str, Any]] | None:
        if not self._tool_calls:
            return None
        return [self._tool_calls[i] for i in sorted(self._tool_calls)]

    def accumulate(self, chunk_type: str, data: Any) -> None:
        if chunk_type == "text":
            self._text.append(str(data))
        elif chunk_type == "thinking":
            self._thinking.append(str(data))
        elif chunk_type == "tool_call_start":
            idx = data.get("index", 0) if isinstance(data, dict) else 0
            name = data.get("name", "") if isinstance(data, dict) else str(data)
            self._tool_calls[idx] = {
                "id": (data.get("id") if isinstance(data, dict) else None) or f"call_{idx}",
                "type": "function",
                "function": {"name": name, "arguments": ""},
            }
        elif chunk_type == "tool_call_delta":
            idx = data.get("index", 0) if isinstance(data, dict) else 0
            tc = self._tool_calls.setdefault(
                idx,
                {"id": f"call_{idx}", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            tc["function"]["arguments"] += (data or {}).get("arguments_delta", "")
        elif chunk_type == "tool_call_end":
            idx = data.get("index", 0) if isinstance(data, dict) else 0
            tc = self._tool_calls.setdefault(
                idx,
                {"id": f"call_{idx}", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if isinstance(data, dict):
                if data.get("name"):
                    tc["function"]["name"] = data["name"]
                if data.get("arguments") is not None:
                    tc["function"]["arguments"] = data["arguments"]


def _default_chunks(response: ScriptedResponse) -> list[tuple[str, Any]]:
    """Derive the standard chunk sequence from a response body.

    (The closing ``done`` event is emitted by the provider on finalize,
    not listed here — matching how real providers emit it after the SSE
    loop ends.)
    """
    chunks: list[tuple[str, Any]] = []
    if response.thinking:
        chunks.append(("thinking", response.thinking))
    if response.content:
        chunks.append(("text", response.content))
    for i, tc in enumerate(response.tool_calls or []):
        func = tc.get("function", {})
        name = func.get("name", "")
        args = func.get("arguments", "")
        chunks.append(("tool_call_start", {"index": i, "name": name, "id": tc.get("id", f"call_{i}")}))
        if args:
            chunks.append(("tool_call_delta", {"index": i, "arguments_delta": args}))
        chunks.append(("tool_call_end", {"index": i, "name": name, "arguments": args}))
    return chunks


async def _await_with_cancel(event: asyncio.Event, cancel_event: asyncio.Event | None) -> bool:
    """Wait for ``event`` (a response hold barrier). Returns True when
    ``cancel_event`` fired first."""
    if cancel_event is None:
        await event.wait()
        return False
    if cancel_event.is_set():
        return True
    hold_task = asyncio.ensure_future(event.wait())
    cancel_task = asyncio.ensure_future(cancel_event.wait())
    try:
        done, _ = await asyncio.wait({hold_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        return cancel_task in done and hold_task not in done
    finally:
        for task in (hold_task, cancel_task):
            if not task.done():
                task.cancel()


def _cancelled_response() -> dict[str, Any]:
    return {
        "content": None,
        "thinking": None,
        "tool_calls": None,
        "role": "assistant",
        "usage": None,
        "finish_reason": FINISH_REASON_CANCELLED,
    }


class ScriptedProvider:
    """Replays queued responses through the standard Provider contract.

    - ``responses``: ordered ``ScriptedResponse`` objects and/or
      request-aware factories (callables taking a ``ScriptedRequest``).
    - ``requests``: the captured requests, in call order — assert on
      them to verify what the runtime actually sent (messages, tool
      list, streaming mode).
    - ``next_call_index``: how many ``complete()`` calls have been made.
    """

    def __init__(
        self,
        responses: (
            Sequence[ScriptedResponse | Callable[[ScriptedRequest], ScriptedResponse]]
            | Callable[[ScriptedRequest], ScriptedResponse]
            | None
        ) = None,
        *,
        model: str = "scripted-model",
        embedder: Any = None,
        name: str = "",
    ) -> None:
        self._factory: Callable | None = None
        if responses is None:
            self._queue: list = []
        elif callable(responses) and not isinstance(responses, (list, tuple)):
            # A bare factory (not wrapped in a list) is persistent: it
            # answers every complete() call, so request-driven scripts
            # never exhaust.
            self._queue = []
            self._factory = responses
        else:
            self._queue = list(responses)
        self._call_index = 0
        self._model = model
        self._embedder = embedder
        self.name = name
        self.requests: list[ScriptedRequest] = []

    @property
    def next_call_index(self) -> int:
        return self._call_index

    def extend(
        self,
        responses: (
            Sequence[ScriptedResponse | Callable[[ScriptedRequest], ScriptedResponse]]
            | ScriptedResponse
            | Callable[[ScriptedRequest], ScriptedResponse]
        ),
    ) -> None:
        """Append responses to the queue (the harness uses this to build
        a script across multiple calls before turns run)."""
        if isinstance(responses, (list, tuple)):
            for r in responses:
                if isinstance(r, (list, tuple)):
                    self._queue.extend(r)
                else:
                    self._queue.append(r)
        else:
            self._queue.append(responses)

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
        request = ScriptedRequest(
            model=model or self._model,
            messages=list(messages),
            tools=list(tools) if tools else None,
            streaming=streaming,
            call_index=self._call_index,
        )
        if not self._queue:
            if self._factory is None:
                # The failed call still counts as a made call.
                self._call_index += 1
                raise ScriptExhaustedError(_exhausted_message(request.call_index, request, self.requests))
            entry = self._factory
        else:
            entry = self._queue.pop(0)
        if callable(entry):
            response = entry(request)
            if not isinstance(response, ScriptedResponse):
                last_user = request.messages[-1].get("content") if request.messages else None
                raise UnexpectedCallError(
                    f"ScriptedProvider call #{request.call_index}: the request-aware factory "
                    f"must return ScriptedResponse, got {type(response).__name__!r}. "
                    f"Last user message: {last_user!r}"
                )
        else:
            response = entry
        self.requests.append(request)
        self._call_index += 1

        if cancel_event is not None and cancel_event.is_set():
            return _cancelled_response()

        if response.hold is not None:
            if await _await_with_cancel(response.hold, cancel_event):
                return _cancelled_response()

        if streaming and on_chunk is not None:
            return await self._stream(response, on_chunk, cancel_event)

        if response.error is not None:
            _raise_scripted_error(response.error)

        return self._finalize(
            response,
            response.content,
            response.thinking,
            response.tool_calls,
            response.finish_reason,
        )

    async def _stream(
        self,
        response: ScriptedResponse,
        on_chunk: StreamCallback,
        cancel_event: asyncio.Event | None,
    ) -> dict[str, Any]:
        chunks = response.stream_chunks if response.stream_chunks is not None else _default_chunks(response)
        acc = _StreamAccumulator()
        cancelled = False
        stream_error: Any = None
        for chunk_type, data in chunks:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if chunk_type != "error":
                acc.accumulate(chunk_type, data)
            try:
                result = on_chunk(chunk_type, data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                # A subscriber error must never kill the stream (same as
                # the real providers' _emit).
                log.debug("on_chunk %s callback failed: %s", chunk_type, exc)
            if chunk_type == "error":
                # Scripted stand-in for a real SSE disconnect: the
                # stream dies right after this chunk.
                stream_error = data
                break
        if cancelled:
            finish_reason = FINISH_REASON_CANCELLED
        elif stream_error is not None or response.error is not None:
            if acc.content or acc.thinking or acc.tool_calls:
                # Stream died after partial delivery — keep the partial
                # content, mark the stream as errored (real-provider
                # disconnect behavior).
                finish_reason = FINISH_REASON_ERROR
            else:
                _raise_scripted_error(stream_error if stream_error is not None else response.error)
        else:
            finish_reason = response.finish_reason
            if finish_reason is None:
                finish_reason = FINISH_REASON_TOOL_CALLS if acc.tool_calls else FINISH_REASON_STOP
        try:
            result = on_chunk("done", {"usage": response.usage})
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            log.debug("on_chunk done callback failed: %s", exc)
        return self._finalize(response, acc.content or None, acc.thinking or None, acc.tool_calls, finish_reason)

    @staticmethod
    def _finalize(
        response: ScriptedResponse,
        content: str | None,
        thinking: str | None,
        tool_calls: list[dict[str, Any]] | None,
        finish_reason: str | None,
    ) -> dict[str, Any]:
        if finish_reason is None:
            finish_reason = FINISH_REASON_TOOL_CALLS if response.tool_calls else FINISH_REASON_STOP
        return {
            "content": content,
            "thinking": thinking,
            "tool_calls": tool_calls,
            "role": "assistant",
            "usage": response.usage,
            "finish_reason": finish_reason,
        }

    async def embed(
        self,
        model: str,
        text: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> list[float] | None:
        if self._embedder is None:
            raise ScriptedProviderError(
                "ScriptedProvider.embed() called but no embedder was configured — "
                "the scripted model cannot embed. Pass embedder=<async callable> "
                "to ScriptedProvider for tests that exercise embeddings."
            )
        result = self._embedder(model, text)
        if asyncio.iscoroutine(result):
            result = await result
        return result

    async def get_model_info(
        self,
        model: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return None


def register_scripted_provider(
    name: str = PROVIDER_SCRIPTED,
    responses: (
        Sequence[ScriptedResponse | Callable[[ScriptedRequest], ScriptedResponse]]
        | Callable[[ScriptedRequest], ScriptedResponse]
        | None
    ) = None,
    *,
    provider: ScriptedProvider | None = None,
    **kwargs: Any,
) -> ScriptedProvider:
    """Register a ScriptedProvider in the global provider registry under
    ``name`` (default ``"scripted"``) and return it.

    This is the injection point for test servers that run their own
    process (e.g. #957's ephemeral child runner): register here, then
    point the process config's ``providers``/``model_configs`` at the
    same name.
    """
    provider = provider or ScriptedProvider(responses, **kwargs)
    provider.name = name
    register_provider(name, provider)
    return provider


def unregister_scripted_provider(name: str = PROVIDER_SCRIPTED) -> None:
    """Remove the scripted provider registration (clean teardown)."""
    unregister_provider(name)
