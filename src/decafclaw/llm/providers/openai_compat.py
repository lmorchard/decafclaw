"""OpenAI-compatible provider — raw HTTP to any OpenAI-compat endpoint.

Works with LiteLLM, Ollama, vLLM, OpenRouter, and any other service
that implements the OpenAI chat completions API.
"""

import asyncio
import json
import logging

import httpx

from ..types import StreamCallback

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


class OpenAICompatProvider:
    """Provider for OpenAI-compatible endpoints (LiteLLM, Ollama, vLLM, etc.)."""

    def __init__(self, url: str, api_key: str = ""):
        self.url = url
        self.api_key = api_key

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
                model, messages, tools=tools, on_chunk=on_chunk,
                cancel_event=cancel_event, timeout=timeout,
            )
        return await self._complete_nonstreaming(
            model, messages, tools=tools, timeout=timeout,
        )

    async def _complete_nonstreaming(
        self, model, messages, *, tools=None, timeout=300,
    ) -> dict:
        url = self._completions_url()
        body: dict = {"model": model, "messages": messages}
        if tools:
            body["tools"] = tools

        log.debug("LLM request: model=%s, messages=%d, tools=%d",
                  model, len(messages), len(tools) if tools else 0)

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, json=body, headers=self._headers(), timeout=timeout,
            )
        resp.raise_for_status()
        data = resp.json()

        message = data["choices"][0]["message"]
        usage = data.get("usage")

        if usage:
            log.debug("LLM usage: prompt=%s, completion=%s",
                      usage.get("prompt_tokens"), usage.get("completion_tokens"))
        log.debug("LLM response: content=%s, tool_calls=%d",
                  bool(message.get("content")), len(message.get("tool_calls", [])))

        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                tc["id"] = _sanitize_tool_call_id(tc.get("id", ""))

        return {
            "content": message.get("content"),
            "tool_calls": tool_calls,
            "role": "assistant",
            "usage": usage,
        }

    async def _complete_streaming(
        self, model, messages, *, tools=None, on_chunk=None,
        cancel_event=None, timeout=300,
    ) -> dict:
        url = self._completions_url()
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = tools

        log.debug("LLM streaming request: model=%s, messages=%d, tools=%d",
                  model, len(messages), len(tools) if tools else 0)

        state = _StreamState(on_chunk)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                await self._stream_one_attempt(
                    url, body, timeout, cancel_event, state,
                )
                break  # Success
            except _RetryableError as e:
                if attempt >= _MAX_RETRIES:
                    raise Exception(
                        f"LLM failed after {_MAX_RETRIES} retries "
                        f"(status {e.status}): {e.body[:200]}"
                    ) from e
                delay = min(int(e.retry_after or 2 ** attempt), 30)
                log.warning(
                    "LLM %s (%d), retrying in %ds (attempt %d/%d): %s",
                    "rate limited" if e.status == 429 else "server error",
                    e.status, delay, attempt + 1, _MAX_RETRIES, e.body[:200],
                )
                await _cancellable_sleep(delay, cancel_event)
            except Exception as e:
                if not state.content_parts and not state.tool_calls_in_progress:
                    raise
                log.error("LLM streaming error (partial content kept): %s", e)
                break

        return await state.finalize()

    async def _stream_one_attempt(self, url, body, timeout, cancel_event, state):
        """Execute a single streaming attempt, raising _RetryableError on 429/5xx."""
        from httpx_sse import aconnect_sse

        async with httpx.AsyncClient() as client:
            async with aconnect_sse(
                client, "POST", url, json=body,
                headers=self._headers(), timeout=httpx.Timeout(timeout),
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
                break
            if event.data == "[DONE]":
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
                        url, json=body, headers=self._headers(), timeout=timeout,
                    )
                if resp.status_code == 429 and attempt < max_retries:
                    delay = 2 ** attempt
                    log.warning(
                        "Embedding API rate limited, retrying in %ds "
                        "(attempt %d/%d)", delay, attempt + 1, max_retries,
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
        self.tool_calls_in_progress: dict = {}
        self.usage: dict | None = None
        self._all_events: list = []

    def record_event(self, chunk: dict):
        self._all_events.append(chunk)
        if len(self._all_events) > _MAX_DIAGNOSTIC_EVENTS:
            self._all_events.pop(0)

    async def process_chunk(self, chunk: dict):
        """Process a single parsed SSE chunk."""
        chunk_usage = chunk.get("usage")
        if chunk_usage:
            self.usage = chunk_usage

        choices = chunk.get("choices", [])
        if not choices:
            if chunk.get("error") or not chunk.get("usage"):
                log.debug("LLM chunk with no choices: %s", json.dumps(chunk)[:500])
            return

        finish_reason = choices[0].get("finish_reason")
        if finish_reason and finish_reason not in ("stop", "tool_calls"):
            log.warning("LLM finish_reason: %s", finish_reason)

        delta = choices[0].get("delta", {})

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
                    await self._emit("tool_call_start", {
                        "index": idx, "name": func["name"],
                    })
            else:
                args_delta = func.get("arguments", "")
                if args_delta:
                    self.tool_calls_in_progress[idx]["function"]["arguments"] += args_delta
                    await self._emit("tool_call_delta", {
                        "index": idx, "arguments_delta": args_delta,
                    })
                name_delta = func.get("name", "")
                if name_delta:
                    self.tool_calls_in_progress[idx]["function"]["name"] += name_delta

    async def finalize(self) -> dict:
        """Build the final response dict and emit closing events."""
        tool_calls = None
        if self.tool_calls_in_progress:
            tool_calls = [
                self.tool_calls_in_progress[i]
                for i in sorted(self.tool_calls_in_progress.keys())
            ]
            for idx, tc in enumerate(tool_calls):
                await self._emit("tool_call_end", {
                    "index": idx,
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
                })

        content = "".join(self.content_parts) or None
        await self._emit("done", {"usage": self.usage})

        if self.usage:
            log.debug("LLM streaming usage: prompt=%s, completion=%s",
                      self.usage.get("prompt_tokens"),
                      self.usage.get("completion_tokens"))
        log.debug("LLM streaming response: content=%s, tool_calls=%d",
                  bool(content), len(tool_calls) if tool_calls else 0)

        if not content and not self.tool_calls_in_progress and self._all_events:
            log.warning(
                "LLM returned empty — full SSE event dump (%d events):",
                len(self._all_events),
            )
            for i, evt in enumerate(self._all_events):
                log.warning("  event[%d]: %s", i, json.dumps(evt)[:500])

        return {
            "content": content,
            "tool_calls": tool_calls,
            "role": "assistant",
            "usage": self.usage,
        }

    async def _emit(self, chunk_type: str, data):
        if self.on_chunk:
            try:
                await self.on_chunk(chunk_type, data)
            except Exception as e:
                log.debug("Stream chunk callback error: %s", e)
