"""OpenAI-compatible provider — raw HTTP to any OpenAI-compat endpoint.

Works with LiteLLM, Ollama, vLLM, OpenRouter, and any other service
that implements the OpenAI chat completions API.
"""

import asyncio
import json
import logging
from typing import Any

import httpx

from ..history import without_provider_data
from ..types import DiscoveredModel, LLMRetryExhaustedError, StreamCallback

log = logging.getLogger(__name__)

# Max SSE events kept for diagnostic dump on empty LLM responses
_MAX_DIAGNOSTIC_EVENTS = 50
_MAX_RETRIES = 3


def _sanitize_tool_call_id(tc_id: str) -> str:
    """Strip LiteLLM's embedded thinking data from tool call IDs.

    LiteLLM packs Gemini thinking tokens into tool_call_id fields as base64
    after a ``__thought__`` delimiter (e.g. ``call_abc123__thought__CiUB...``).
    These can be ~3KB+ each and bloat conversation history. The actual ID is
    just the part before ``__thought__``.
    """
    return tc_id.split("__thought__")[0]


def _clean_messages(messages: list) -> list:
    """Strip provider_data and internal UI/context keys before sending to OpenAI API."""
    return [{k: v for k, v in m.items() if k != "thinking"} for m in without_provider_data(messages)]


# ---------------------------------------------------------------------------
# Model-listing payload parsing (#1211)
# ---------------------------------------------------------------------------
#
# ``GET {base}/models`` metadata quality varies wildly across gateways:
# LiteLLM reports ``mode`` / ``supports_function_calling`` / ``max_input_tokens``,
# OpenRouter reports ``architecture.modality`` / ``supported_parameters`` /
# ``context_length``, Azure-style gateways report ``capabilities``, and
# Ollama / vLLM / plain OpenAI return bare IDs. The parser normalizes the
# known shapes and leaves everything else as unknown (None) — unknown is
# never treated as capable.


def _as_positive_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _parse_litellm_capabilities(entry: dict, caps: dict) -> None:
    """LiteLLM proxy fields: mode, supports_function_calling, token limits."""
    mode = entry.get("mode")
    if isinstance(mode, str) and mode:
        caps["chat"] = mode == "chat"
    sfc = entry.get("supports_function_calling")
    if isinstance(sfc, bool):
        caps["tools"] = sfc
    ctx = _as_positive_int(entry.get("max_input_tokens"))
    if ctx:
        caps["context_window_size"] = ctx


def _parse_openrouter_capabilities(entry: dict, caps: dict) -> None:
    """OpenRouter fields: architecture.modality, supported_parameters, context_length."""
    arch = entry.get("architecture")
    modality = arch.get("modality") if isinstance(arch, dict) else None
    if isinstance(modality, str) and "->" in modality:
        inputs, _, outputs = modality.partition("->")
        # Chat models emit text; audio-input models are transcription/speech.
        caps["chat"] = "text" in outputs.split("+") and "audio" not in inputs.split("+")
    params = entry.get("supported_parameters")
    if isinstance(params, list):
        caps["tools"] = "tools" in params or "tool_choice" in params
    ctx = _as_positive_int(entry.get("context_length"))
    if ctx:
        caps["context_window_size"] = ctx


def _parse_azure_capabilities(entry: dict, caps: dict) -> None:
    """Azure OpenAI-style ``capabilities`` object (chat_completion flag)."""
    capabilities = entry.get("capabilities")
    if not isinstance(capabilities, dict):
        return
    flag = capabilities.get("chat_completion")
    if isinstance(flag, bool) and "chat" not in caps:
        caps["chat"] = flag


def parse_models_payload(payload: Any) -> list[DiscoveredModel]:
    """Parse a ``GET {base}/models`` response into normalized entries.

    Models the gateway positively identifies as non-chat (embeddings,
    image/audio/realtime endpoints) are dropped — only chat-capable
    candidates belong in the catalog (#1211). Bare-ID entries are kept
    with unknown capabilities so the user can opt in from the catalog UI.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    result: list[DiscoveredModel] = []
    seen: set[str] = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id") or entry.get("name")
        if not isinstance(model_id, str) or not model_id or model_id in seen:
            continue
        seen.add(model_id)

        caps: dict = {}
        _parse_litellm_capabilities(entry, caps)
        _parse_openrouter_capabilities(entry, caps)
        _parse_azure_capabilities(entry, caps)

        # Positively non-chat (embedding, image, audio, realtime, legacy
        # completion endpoints) → excluded from the catalog. Unknown →
        # kept as a disabled candidate.
        if caps.get("chat") is False:
            continue

        owned_raw = entry.get("owned_by")
        result.append(
            DiscoveredModel(
                id=model_id,
                chat=caps.get("chat"),
                tools=caps.get("tools"),
                context_window_size=caps.get("context_window_size"),
                owned_by=owned_raw if isinstance(owned_raw, str) else "",
            )
        )
    return result


class OpenAICompatProvider:
    """Provider for OpenAI-compatible endpoints (LiteLLM, Ollama, vLLM, etc.)."""

    def __init__(self, url: str, api_key: str = ""):
        self.url = url
        self.api_key = api_key

    async def get_model_info(
        self,
        model: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Get model metadata (e.g. token limits), or None if unsupported."""
        return None

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _completions_url(self) -> str:
        """Ensure the URL ends with /chat/completions."""
        url = self.url.rstrip("/")
        if not url.endswith("/chat/completions"):
            if url.endswith("/v1"):
                url += "/chat/completions"
            else:
                url += "/v1/chat/completions"
        return url

    def _embeddings_url(self) -> str:
        """Derive embeddings URL from the base URL."""
        url = self.url.rstrip("/")
        if url.endswith("/chat/completions"):
            return url.replace("/chat/completions", "/embeddings")
        if url.endswith("/v1"):
            return url + "/embeddings"
        return url + "/v1/embeddings"

    def _models_url(self) -> str:
        """Derive the model-listing URL from the base URL (#1211).

        Accepts the same shapes as ``_completions_url``/``_embeddings_url``:
        a bare base, a ``/v1`` base, or a full endpoint URL ending in
        ``/chat/completions``, ``/responses``, or ``/embeddings``.
        """
        url = self.url.rstrip("/")
        for suffix in ("/chat/completions", "/responses", "/embeddings"):
            if url.endswith(suffix):
                url = url[: -len(suffix)]
                break
        if url.endswith("/v1"):
            return url + "/models"
        return url + "/v1/models"

    async def discover_models(self, *, timeout: int = 30) -> list[DiscoveredModel]:
        """List models the gateway serves via ``GET {base}/models`` (#1211).

        Raises on HTTP/network errors — callers record the failure and fall
        back to the cached catalog.
        """
        url = self._models_url()
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), timeout=timeout)
        resp.raise_for_status()
        return parse_models_payload(resp.json())

    async def complete(
        self,
        model: str,
        messages: list,
        *,
        tools: list | None = None,
        streaming: bool = False,
        on_chunk: StreamCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        timeout: int = 300,
        **kwargs,
    ) -> dict:
        if streaming:
            return await self._complete_streaming(
                model,
                messages,
                tools=tools,
                on_chunk=on_chunk,
                cancel_event=cancel_event,
                timeout=timeout,
            )
        return await self._complete_nonstreaming(
            model,
            messages,
            tools=tools,
            timeout=timeout,
        )

    async def _complete_nonstreaming(
        self,
        model,
        messages,
        *,
        tools=None,
        timeout=300,
    ) -> dict:
        url = self._completions_url()
        body: dict = {"model": model, "messages": _clean_messages(messages)}
        if tools:
            body["tools"] = tools

        log.debug("LLM request: model=%s, messages=%d, tools=%d", model, len(messages), len(tools) if tools else 0)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=body,
                headers=self._headers(),
                timeout=timeout,
            )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if choices:
            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason")
        else:
            message = {}
            finish_reason = "error"
        usage = data.get("usage")

        if usage:
            usage["cached_tokens"] = _cached_tokens(usage)
            log.debug(
                "LLM usage: prompt=%s, completion=%s, cached=%s",
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
                usage.get("cached_tokens"),
            )
        log.debug(
            "LLM response: content=%s, tool_calls=%d, finish_reason=%s",
            bool(message.get("content")),
            len(message.get("tool_calls", [])),
            finish_reason,
        )

        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                tc["id"] = _sanitize_tool_call_id(tc.get("id", ""))

        thinking = message.get("reasoning_content") or message.get("reasoning") or message.get("thinking") or None
        return {
            "content": message.get("content"),
            "thinking": thinking,
            "tool_calls": tool_calls,
            "role": "assistant",
            "usage": usage,
            "finish_reason": finish_reason,
        }

    async def _complete_streaming(
        self,
        model,
        messages,
        *,
        tools=None,
        on_chunk=None,
        cancel_event=None,
        timeout=300,
    ) -> dict:
        url = self._completions_url()
        body: dict = {
            "model": model,
            "messages": _clean_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools

        log.debug(
            "LLM streaming request: model=%s, messages=%d, tools=%d", model, len(messages), len(tools) if tools else 0
        )

        state = _StreamState(on_chunk)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                await self._stream_one_attempt(
                    url,
                    body,
                    timeout,
                    cancel_event,
                    state,
                )
                break  # Success
            except _RetryableError as e:
                if attempt >= _MAX_RETRIES:
                    raise LLMRetryExhaustedError(
                        f"LLM failed after {_MAX_RETRIES} retries (status {e.status}): {e.body[:200]}",
                        status=e.status,
                        body=e.body,
                        retry_after=e.retry_after,
                    ) from e
                delay = min(int(e.retry_after or 2**attempt), 30)
                log.warning(
                    "LLM %s (%d), retrying in %ds (attempt %d/%d): %s",
                    "rate limited" if e.status == 429 else "server error",
                    e.status,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                    e.body[:200],
                )
                await _cancellable_sleep(delay, cancel_event)
            except Exception as e:
                if not state.content_parts and not state.thinking_parts and not state.tool_calls_in_progress:
                    raise
                log.error("LLM streaming error (partial content kept): %s", e)
                state.finish_reason = "error"
                break

        return await state.finalize()

    async def _stream_one_attempt(self, url, body, timeout, cancel_event, state):
        """Execute a single streaming attempt, raising _RetryableError on 429/5xx."""
        from httpx_sse import aconnect_sse

        async with httpx.AsyncClient() as client:
            async with aconnect_sse(
                client,
                "POST",
                url,
                json=body,
                headers=self._headers(),
                timeout=httpx.Timeout(timeout),
            ) as event_source:
                status = event_source.response.status_code
                if 400 <= status < 500 and status != 429:
                    error_body = (await event_source.response.aread()).decode(errors="replace")[:500]
                    raise Exception(f"LLM API error ({status}): {error_body}")
                if status == 429 or status >= 500:
                    error_body = (await event_source.response.aread()).decode(errors="replace")[:500]
                    retry_after = event_source.response.headers.get("retry-after")
                    raise _RetryableError(status, error_body, retry_after)

                await self._consume_sse_events(event_source, cancel_event, state)

    async def _consume_sse_events(self, event_source, cancel_event, state):
        """Read SSE events and update streaming state."""
        async for event in event_source.aiter_sse():
            if cancel_event and cancel_event.is_set():
                log.info("LLM streaming cancelled by user")
                state.finish_reason = "cancelled"
                break
            if event.data == "[DONE]":
                state.saw_done = True
                break

            event_type = getattr(event, "event", None)
            if event_type and event_type != "message":
                log.warning("LLM SSE event type=%s: %s", event_type, event.data[:500])

            try:
                chunk = json.loads(event.data)
            except json.JSONDecodeError:
                log.debug("LLM non-JSON SSE data: %s", event.data[:200])
                continue

            state.record_event(chunk)
            await state.process_chunk(chunk)

    async def embed(
        self,
        model: str,
        text: str,
        *,
        timeout: int = 30,
        **kwargs,
    ) -> list[float] | None:
        url = self._embeddings_url()
        body = {"model": model, "input": text}

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url,
                        json=body,
                        headers=self._headers(),
                        timeout=timeout,
                    )
                if resp.status_code == 429 and attempt < max_retries:
                    delay = 2**attempt
                    log.warning(
                        "Embedding API rate limited, retrying in %ds (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
            except httpx.HTTPStatusError as e:
                log.error("Embedding API call failed: %s", e)
                return None
            except Exception as e:
                log.error("Embedding API call failed: %s", e)
                return None
        return None


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


def _cached_tokens(usage: dict | None) -> int:
    """Prompt cache-hit token count from an OpenAI-compatible usage dict.

    OpenAI reports it under ``prompt_tokens_details.cached_tokens``; providers
    that don't cache omit it. Returns 0 when absent (#480)."""
    if not usage:
        return 0
    details = usage.get("prompt_tokens_details") or {}
    return details.get("cached_tokens", 0) or 0


class _RetryableError(Exception):
    """Raised on 429/5xx to trigger retry logic."""

    def __init__(self, status: int, body: str, retry_after: str | None = None):
        self.status = status
        self.body = body
        self.retry_after = retry_after


async def _cancellable_sleep(delay: float, cancel_event: asyncio.Event | None):
    """Sleep that can be interrupted by a cancel event."""
    if cancel_event:
        try:
            await asyncio.wait_for(cancel_event.wait(), timeout=delay)
            log.info("LLM retry sleep cancelled by user")
            raise asyncio.CancelledError()
        except asyncio.TimeoutError:
            pass
    else:
        await asyncio.sleep(delay)


class _StreamState:
    """Accumulates streaming state across SSE events."""

    def __init__(self, on_chunk: StreamCallback | None = None):
        self.on_chunk = on_chunk
        self.content_parts: list[str] = []
        self.thinking_parts: list[str] = []
        self.tool_calls_in_progress: dict = {}
        self.usage: dict | None = None
        self.finish_reason: str | None = None
        self.saw_done: bool = False
        self._all_events: list = []

    def record_event(self, chunk: dict):
        self._all_events.append(chunk)
        if len(self._all_events) > _MAX_DIAGNOSTIC_EVENTS:
            self._all_events.pop(0)

    async def process_chunk(self, chunk: dict):
        """Process a single parsed SSE chunk."""
        chunk_usage = chunk.get("usage")
        if chunk_usage:
            chunk_usage["cached_tokens"] = _cached_tokens(chunk_usage)
            self.usage = chunk_usage

        choices = chunk.get("choices", [])
        if not choices:
            if chunk.get("error"):
                self.finish_reason = "error"
            elif not chunk.get("usage"):
                log.debug("LLM chunk with no choices: %s", json.dumps(chunk)[:500])
            return

        finish_reason = choices[0].get("finish_reason")
        if finish_reason:
            self.finish_reason = finish_reason
            if finish_reason not in ("stop", "tool_calls"):
                log.warning("LLM finish_reason: %s", finish_reason)

        delta = choices[0].get("delta", {})

        # Thinking / reasoning content
        thinking = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking")
        if thinking:
            self.thinking_parts.append(thinking)
            await self._emit("thinking", thinking)

        # Text content
        text = delta.get("content")
        if text:
            self.content_parts.append(text)
            await self._emit("text", text)
        # Tool calls
        tc_deltas = delta.get("tool_calls")
        if tc_deltas:
            await self._process_tool_call_deltas(tc_deltas)

    async def _process_tool_call_deltas(self, tc_deltas: list):
        for tc_delta in tc_deltas:
            idx = tc_delta.get("index", 0)
            func = tc_delta.get("function", {})

            if idx not in self.tool_calls_in_progress:
                raw_id = tc_delta.get("id", f"call_{idx}")
                self.tool_calls_in_progress[idx] = {
                    "id": _sanitize_tool_call_id(raw_id),
                    "type": "function",
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                    },
                }
                if func.get("name"):
                    await self._emit(
                        "tool_call_start",
                        {
                            "index": idx,
                            "name": func["name"],
                        },
                    )
            else:
                args_delta = func.get("arguments", "")
                if args_delta:
                    self.tool_calls_in_progress[idx]["function"]["arguments"] += args_delta
                    await self._emit(
                        "tool_call_delta",
                        {
                            "index": idx,
                            "arguments_delta": args_delta,
                        },
                    )
                name_delta = func.get("name", "")
                if name_delta:
                    self.tool_calls_in_progress[idx]["function"]["name"] += name_delta

    async def finalize(self) -> dict:
        """Build the final response dict and emit closing events."""
        tool_calls = None
        if self.tool_calls_in_progress:
            tool_calls = [self.tool_calls_in_progress[i] for i in sorted(self.tool_calls_in_progress.keys())]
            for idx, tc in enumerate(tool_calls):
                await self._emit(
                    "tool_call_end",
                    {
                        "index": idx,
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                )

        content = "".join(self.content_parts) or None
        thinking = "".join(self.thinking_parts) or None
        finish_reason = self.finish_reason
        if finish_reason is None:
            if self.saw_done:
                finish_reason = "stop"
            elif content or thinking or self.tool_calls_in_progress:
                finish_reason = "error"

        await self._emit("done", {"usage": self.usage})

        if self.usage:
            log.debug(
                "LLM streaming usage: prompt=%s, completion=%s",
                self.usage.get("prompt_tokens"),
                self.usage.get("completion_tokens"),
            )
        log.debug(
            "LLM streaming response: content=%s, tool_calls=%d, finish_reason=%s",
            bool(content),
            len(tool_calls) if tool_calls else 0,
            finish_reason,
        )

        if not content and not thinking and not self.tool_calls_in_progress and self._all_events:
            log.warning(
                "LLM returned empty — full SSE event dump (%d events):",
                len(self._all_events),
            )
            for i, evt in enumerate(self._all_events):
                log.warning("  event[%d]: %s", i, json.dumps(evt)[:500])

        return {
            "content": content,
            "thinking": thinking,
            "tool_calls": tool_calls,
            "role": "assistant",
            "usage": self.usage,
            "finish_reason": finish_reason,
        }

    async def _emit(self, chunk_type: str, data):
        if self.on_chunk:
            try:
                await self.on_chunk(chunk_type, data)
            except Exception as e:
                log.debug("Stream chunk callback error: %s", e)
