"""Vertex AI Gemini provider — native REST API with ADC auth."""

import asyncio
import json
import logging
import uuid

import httpx

from ..types import StreamCallback
from .openai_compat import _cancellable_sleep, _RetryableError

log = logging.getLogger(__name__)

_MAX_DIAGNOSTIC_EVENTS = 50
_MAX_RETRIES = 3


class VertexProvider:
    """Provider for Google Vertex AI Gemini models.

    Uses native Gemini REST API (not OpenAI-compat). ADC auth via google-auth.
    """

    def __init__(self, project: str, region: str = "us-central1",
                 service_account_file: str = ""):
        self.project = project
        self.region = region
        self._service_account_file = service_account_file
        self._credentials = None

    async def _get_token(self) -> str:
        """Get a valid access token, refreshing if needed.

        Auth priority:
        1. Service account JSON key file (if configured)
        2. ADC (gcloud auth application-default login, GOOGLE_APPLICATION_CREDENTIALS, etc.)

        Token refresh is run in a thread to avoid blocking the event loop.
        """
        import google.auth
        import google.auth.transport.requests

        if self._credentials is None:
            if self._service_account_file:
                from google.oauth2 import service_account
                self._credentials = service_account.Credentials.from_service_account_file(
                    self._service_account_file,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
            else:
                self._credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
        if not self._credentials.valid:
            await asyncio.to_thread(
                self._credentials.refresh,
                google.auth.transport.requests.Request(),
            )
        return self._credentials.token

    def _base_url(self, model: str) -> str:
        return (
            f"https://{self.region}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project}/locations/{self.region}/"
            f"publishers/google/models/{model}"
        )

    async def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {await self._get_token()}",
        }

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
        url = f"{self._base_url(model)}:generateContent"
        body = _build_request_body(messages, tools)

        log.debug("Vertex request: model=%s, messages=%d, tools=%d",
                  model, len(messages), len(tools) if tools else 0)
        log.debug("Vertex request body: %s", json.dumps(body)[:2000])

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, json=body, headers=await self._headers(), timeout=timeout,
            )
        resp.raise_for_status()
        data = resp.json()

        log.debug("Vertex response: %s", json.dumps(data)[:2000])
        return _parse_response(data)

    async def _complete_streaming(
        self, model, messages, *, tools=None, on_chunk=None,
        cancel_event=None, timeout=300,
    ) -> dict:
        url = f"{self._base_url(model)}:streamGenerateContent?alt=sse"
        body = _build_request_body(messages, tools)

        log.debug("Vertex streaming request: model=%s, messages=%d, tools=%d",
                  model, len(messages), len(tools) if tools else 0)
        log.debug("Vertex request body: %s", json.dumps(body)[:2000])

        state = _VertexStreamState(on_chunk)

        for attempt in range(_MAX_RETRIES + 1):
            try:
                await self._stream_one_attempt(
                    url, body, timeout, cancel_event, state,
                )
                break  # Success
            except _RetryableError as e:
                if attempt >= _MAX_RETRIES:
                    raise Exception(
                        f"Vertex failed after {_MAX_RETRIES} retries "
                        f"(status {e.status}): {e.body[:200]}"
                    ) from e
                delay = min(int(e.retry_after or 2 ** attempt), 30)
                log.warning(
                    "Vertex %s (%d), retrying in %ds (attempt %d/%d): %s",
                    "rate limited" if e.status == 429 else "server error",
                    e.status, delay, attempt + 1, _MAX_RETRIES, e.body[:200],
                )
                await _cancellable_sleep(delay, cancel_event)
            except Exception as e:
                if not state.all_text_parts and state.tool_calls_seen == 0:
                    raise
                log.error("Vertex streaming error (partial content kept): %s", e)
                break

        return await state.finalize()

    async def _stream_one_attempt(self, url, body, timeout, cancel_event, state):
        """Execute a single streaming attempt."""
        from httpx_sse import aconnect_sse

        async with httpx.AsyncClient() as client:
            async with aconnect_sse(
                client, "POST", url, json=body,
                headers=await self._headers(),
                timeout=httpx.Timeout(timeout),
            ) as event_source:
                status = event_source.response.status_code
                if 400 <= status < 500 and status != 429:
                    error_body = (await event_source.response.aread()).decode(errors="replace")[:500]
                    raise Exception(f"Vertex API error ({status}): {error_body}")
                if status == 429 or status >= 500:
                    error_body = (await event_source.response.aread()).decode(errors="replace")[:500]
                    retry_after = event_source.response.headers.get("retry-after")
                    raise _RetryableError(status, error_body, retry_after)

                await self._consume_sse_events(event_source, cancel_event, state)

    async def _consume_sse_events(self, event_source, cancel_event, state):
        """Read SSE events and update streaming state."""
        async for event in event_source.aiter_sse():
            if cancel_event and cancel_event.is_set():
                log.info("Vertex streaming cancelled")
                break
            if event.data == "[DONE]":
                break

            try:
                chunk = json.loads(event.data)
            except json.JSONDecodeError:
                log.debug("Vertex non-JSON SSE: %s", event.data[:200])
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
        url = f"{self._base_url(model)}:predict"
        body = {"instances": [{"content": text}]}

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url, json=body, headers=await self._headers(),
                        timeout=timeout,
                    )
                if resp.status_code == 429 and attempt < max_retries:
                    delay = 2 ** attempt
                    log.warning(
                        "Vertex embedding rate limited, retrying in %ds "
                        "(attempt %d/%d)", delay, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["predictions"][0]["embeddings"]["values"]
            except httpx.HTTPStatusError as e:
                log.error("Vertex embedding failed: %s", e)
                return None
            except Exception as e:
                log.error("Vertex embedding failed: %s", e)
                return None
        return None


# ---------------------------------------------------------------------------
# Streaming state
# ---------------------------------------------------------------------------


class _VertexStreamState:
    """Accumulates Gemini streaming state across SSE events."""

    def __init__(self, on_chunk: StreamCallback | None = None):
        self.on_chunk = on_chunk
        self.all_text_parts: list[str] = []
        self.all_tool_calls: list[dict] = []
        self.tool_calls_seen = 0
        self.usage: dict | None = None
        self._all_events: list = []

    def record_event(self, chunk: dict):
        self._all_events.append(chunk)
        if len(self._all_events) > _MAX_DIAGNOSTIC_EVENTS:
            self._all_events.pop(0)

    async def process_chunk(self, chunk: dict):
        """Process a single Gemini streaming chunk."""
        log.debug("Vertex chunk: %s", json.dumps(chunk)[:1000])

        # Extract usage
        chunk_usage = chunk.get("usageMetadata")
        if chunk_usage:
            self.usage = {
                "prompt_tokens": chunk_usage.get("promptTokenCount", 0),
                "completion_tokens": chunk_usage.get("candidatesTokenCount", 0),
                "total_tokens": chunk_usage.get("totalTokenCount", 0),
            }

        candidates = chunk.get("candidates", [])
        if not candidates:
            return

        parts = candidates[0].get("content", {}).get("parts", [])

        # Gemini sends incremental text per chunk — emit directly
        chunk_text = ""
        chunk_tool_calls = []
        for part in parts:
            if "text" in part:
                chunk_text += part["text"]
            elif "functionCall" in part:
                chunk_tool_calls.append(part["functionCall"])

        if chunk_text:
            self.all_text_parts.append(chunk_text)
            await self._emit("text", chunk_text)

        # Tool calls arrive as complete calls (not deltas like OpenAI)
        for i, fc in enumerate(chunk_tool_calls):
            idx = self.tool_calls_seen + i
            args_json = json.dumps(fc.get("args", {}))
            await self._emit("tool_call_start", {"index": idx, "name": fc["name"]})
            await self._emit("tool_call_delta", {"index": idx, "arguments_delta": args_json})
            await self._emit("tool_call_end", {"index": idx, "name": fc["name"], "arguments": args_json})
        self.all_tool_calls.extend(chunk_tool_calls)
        self.tool_calls_seen += len(chunk_tool_calls)

    async def finalize(self) -> dict:
        """Build the final response dict and emit closing events."""
        content = "".join(self.all_text_parts) or None
        tool_calls = None
        if self.all_tool_calls:
            tool_calls = [
                {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                }
                for fc in self.all_tool_calls
            ]

        await self._emit("done", {"usage": self.usage})

        if not content and not tool_calls and self._all_events:
            log.warning(
                "Vertex returned empty — SSE dump (%d events):",
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


# ---------------------------------------------------------------------------
# Message format translation
# ---------------------------------------------------------------------------

_VERTEX_UNSUPPORTED_KEYS = frozenset({
    # Schema metadata Vertex doesn't process.
    "$schema", "$id", "$defs", "definitions", "$ref",
    # JSON Schema keywords for property-name / pattern-shape constraints —
    # not in the OpenAPI 3 subset Vertex accepts.
    "propertyNames", "patternProperties", "dependencies", "dependentRequired",
    "dependentSchemas",
    # Conditional schema branches.
    "if", "then", "else",
    # Numeric constraint Vertex rejects on some schema versions.
    "multipleOf",
})


def _clean_schema(schema: dict) -> dict:
    """Strip JSON-Schema keywords Vertex/Gemini doesn't accept.

    Vertex's tool-parameter schema is a restricted subset of OpenAPI 3.
    MCP-provided tool schemas (e.g. Playwright) frequently use full
    JSON-Schema features like ``propertyNames`` or ``patternProperties``
    that the Vertex API rejects with HTTP 400 ``Unknown name "X"``.

    The set of stripped keys is a denylist (see ``_VERTEX_UNSUPPORTED_KEYS``)
    rather than an allowlist so we don't accidentally drop legitimate
    constraints (``minLength``, ``enum``, etc.) that Vertex accepts. Add
    keys here as new rejection cases surface.
    """
    cleaned = {k: v for k, v in schema.items()
               if k not in _VERTEX_UNSUPPORTED_KEYS}
    # Recurse into nested schemas (properties, items, etc.)
    if "properties" in cleaned:
        cleaned["properties"] = {
            k: _clean_schema(v) if isinstance(v, dict) else v
            for k, v in cleaned["properties"].items()
        }
    for key in ("items", "additionalProperties"):
        if key in cleaned and isinstance(cleaned[key], dict):
            cleaned[key] = _clean_schema(cleaned[key])
    # Recurse into combinator branches if present (Vertex supports oneOf/
    # anyOf/allOf in some versions, so we keep them but still scrub their
    # contents).
    for key in ("oneOf", "anyOf", "allOf"):
        if key in cleaned and isinstance(cleaned[key], list):
            cleaned[key] = [
                _clean_schema(s) if isinstance(s, dict) else s
                for s in cleaned[key]
            ]
    return cleaned


def _content_to_parts(content) -> list[dict]:
    """Convert message content (string or multimodal list) to Vertex parts.

    Handles both plain string content and the OpenAI multimodal format
    produced by _resolve_attachments: [{"type": "text", ...}, {"type": "image_url", ...}].
    """
    if not content:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    # Multimodal list (from _resolve_attachments)
    parts = []
    for item in content:
        item_type = item.get("type", "")
        if item_type == "text":
            text = item.get("text", "")
            if text:
                parts.append({"text": text})
        elif item_type == "image_url":
            data_url = item.get("image_url", {}).get("url", "")
            # Parse data:mime;base64,payload
            if data_url.startswith("data:") and ";base64," in data_url:
                header, payload = data_url.split(";base64,", 1)
                mime_type = header[len("data:"):]
                parts.append({
                    "inlineData": {"mimeType": mime_type, "data": payload},
                })
            else:
                log.warning("Unsupported image_url format (not a data URI), skipping")
    return parts


def _build_request_body(
    messages: list[dict], tools: list[dict] | None = None,
) -> dict:
    """Translate OpenAI-format messages to Gemini request body."""
    contents = []
    system_parts = []

    # Build a lookup from tool_call_id → function name.
    tc_id_to_name: dict[str, str] = {}
    for msg in messages:
        for tc in msg.get("tool_calls", []):
            tc_id = tc.get("id", "")
            name = tc.get("function", {}).get("name", "")
            if tc_id and name:
                tc_id_to_name[tc_id] = name

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            if content:
                system_parts.append({"text": content})

        elif role == "user":
            parts = _content_to_parts(content)
            if parts:
                contents.append({"role": "user", "parts": parts})

        elif role == "assistant":
            parts = []
            if content:
                parts.append({"text": content})
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                name = func.get("name", "")
                try:
                    args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                parts.append({"functionCall": {"name": name, "args": args}})
            if parts:
                contents.append({"role": "model", "parts": parts})

        elif role == "tool":
            # Gemini requires ALL function responses for a multi-call turn
            # to be in a single user message.
            tool_call_id = msg.get("tool_call_id", "")
            tool_name = msg.get("name", "") or tc_id_to_name.get(tool_call_id, "")
            try:
                response_obj = json.loads(content)
                # Vertex requires response to be a Struct (JSON object).
                # Wrap bare values (int, str, list, etc.) in an object.
                if not isinstance(response_obj, dict):
                    response_obj = {"result": response_obj}
            except (json.JSONDecodeError, TypeError):
                response_obj = {"result": content}

            fr_part = {
                "functionResponse": {
                    "name": tool_name,
                    "response": response_obj,
                },
            }

            # Merge into previous user message if it has functionResponse parts
            if (contents and contents[-1].get("role") == "user"
                    and contents[-1]["parts"]
                    and "functionResponse" in contents[-1]["parts"][0]):
                contents[-1]["parts"].append(fr_part)
            else:
                contents.append({"role": "user", "parts": [fr_part]})

    body: dict = {"contents": contents}

    if system_parts:
        body["systemInstruction"] = {"parts": system_parts}

    if tools:
        func_decls = []
        for tool_def in tools:
            func = tool_def.get("function", tool_def)
            decl: dict = {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
            }
            params = func.get("parameters")
            if params:
                decl["parameters"] = _clean_schema(params)
            func_decls.append(decl)
        body["tools"] = [{"functionDeclarations": func_decls}]

    return body


def _parse_response(data: dict) -> dict:
    """Translate Gemini response to internal format."""
    candidates = data.get("candidates", [])
    if not candidates:
        return {
            "content": None,
            "tool_calls": None,
            "role": "assistant",
            "usage": _parse_usage(data),
        }

    parts = candidates[0].get("content", {}).get("parts", [])

    text_parts = []
    tool_calls = []

    for part in parts:
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {})),
                },
            })

    return {
        "content": "".join(text_parts) or None,
        "tool_calls": tool_calls or None,
        "role": "assistant",
        "usage": _parse_usage(data),
    }


def _parse_usage(data: dict) -> dict | None:
    """Extract usage metadata from Gemini response."""
    meta = data.get("usageMetadata")
    if not meta:
        return None
    return {
        "prompt_tokens": meta.get("promptTokenCount", 0),
        "completion_tokens": meta.get("candidatesTokenCount", 0),
        "total_tokens": meta.get("totalTokenCount", 0),
    }
