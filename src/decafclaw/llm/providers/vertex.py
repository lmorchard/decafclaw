"""Vertex AI Gemini provider — native REST API with ADC auth."""

import asyncio
import json
import logging
import uuid
from typing import Any

import httpx

from ..types import DiscoveredModel, LLMRetryExhaustedError, StreamCallback
from .openai_compat import _cancellable_sleep, _RetryableError

log = logging.getLogger(__name__)

_MAX_DIAGNOSTIC_EVENTS = 50
_MAX_RETRIES = 3

# Curated static fallback for model discovery (#1211). Vertex has no
# practical REST endpoint that lists Gemini models, so discovery returns
# this list instead of nothing. Chat + tools + context are reported for
# every entry; the third field marks whether the id is live-confirmed to
# be served — unconfirmed ids land DISABLED in the catalog (the user opts
# in) so an unserved id never surfaces enabled and fails on first use.
# Update as the Gemini lineup changes; flip confirmed=True only with live
# evidence the project serves the id. tests/test_model_catalog.py pins
# this confirmed/unconfirmed partition.
_VERTEX_STATIC_MODELS: list[tuple[str, str, bool]] = [
    # (model id, owned_by label, live-confirmed)
    ("gemini-2.5-flash", "Google", True),  # config default model; served here
    ("gemini-2.5-flash-lite", "Google", False),  # no live confirmation
    ("gemini-2.5-pro", "Google", True),  # `make run-pro` default; served here
    ("gemini-3.8-flash", "Google", False),  # no live confirmation
    ("gemini-4-argon", "Google", False),  # no live confirmation
]
_GEMINI_CONTEXT_WINDOW = 1_048_576


class VertexProvider:
    """Provider for Google Vertex AI Gemini models.

    Uses native Gemini REST API (not OpenAI-compat). ADC auth via google-auth.
    """

    def __init__(self, project: str, region: str = "us-central1", service_account_file: str = ""):
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
        # "global" is a location, not a regional host prefix — there is no
        # global-aiplatform.googleapis.com. Google serves it from the bare host.
        host = "aiplatform.googleapis.com" if self.region == "global" else f"{self.region}-aiplatform.googleapis.com"
        return f"https://{host}/v1/projects/{self.project}/locations/{self.region}/publishers/google/models/{model}"

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
        url = f"{self._base_url(model)}:generateContent"
        body = _build_request_body(messages, tools)

        log.debug("Vertex request: model=%s, messages=%d, tools=%d", model, len(messages), len(tools) if tools else 0)
        log.debug("Vertex request body: %s", json.dumps(body)[:2000])

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=body,
                headers=await self._headers(),
                timeout=timeout,
            )
        resp.raise_for_status()
        data = resp.json()

        log.debug("Vertex response: %s", json.dumps(data)[:2000])
        return _parse_response(data)

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
        url = f"{self._base_url(model)}:streamGenerateContent?alt=sse"
        body = _build_request_body(messages, tools)

        log.debug(
            "Vertex streaming request: model=%s, messages=%d, tools=%d",
            model,
            len(messages),
            len(tools) if tools else 0,
        )
        log.debug("Vertex request body: %s", json.dumps(body)[:2000])

        state = _VertexStreamState(on_chunk)

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
                        f"Vertex failed after {_MAX_RETRIES} retries (status {e.status}): {e.body[:200]}",
                        status=e.status,
                        body=e.body,
                        retry_after=e.retry_after,
                    ) from e
                delay = min(int(e.retry_after or 2**attempt), 30)
                log.warning(
                    "Vertex %s (%d), retrying in %ds (attempt %d/%d): %s",
                    "rate limited" if e.status == 429 else "server error",
                    e.status,
                    delay,
                    attempt + 1,
                    _MAX_RETRIES,
                    e.body[:200],
                )
                await _cancellable_sleep(delay, cancel_event)
            except Exception as e:
                if not state.all_text_parts and not state.all_thinking_parts and state.tool_calls_seen == 0:
                    raise
                log.error("Vertex streaming error (partial content kept): %s", e)
                break

        return await state.finalize()

    async def _stream_one_attempt(self, url, body, timeout, cancel_event, state):
        """Execute a single streaming attempt."""
        from httpx_sse import aconnect_sse

        async with httpx.AsyncClient() as client:
            async with aconnect_sse(
                client,
                "POST",
                url,
                json=body,
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
                        url,
                        json=body,
                        headers=await self._headers(),
                        timeout=timeout,
                    )
                if resp.status_code == 429 and attempt < max_retries:
                    delay = 2**attempt
                    log.warning(
                        "Vertex embedding rate limited, retrying in %ds (attempt %d/%d)",
                        delay,
                        attempt + 1,
                        max_retries,
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

    async def get_model_info(
        self,
        model: str,
        *,
        timeout: int = 30,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        url = self._base_url(model)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    url,
                    headers=await self._headers(),
                    timeout=timeout,
                )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            log.debug("Failed to get model info for %s from Vertex: %s", model, e)
        return None

    async def discover_models(self, *, timeout: int = 30) -> list[DiscoveredModel]:
        """Return the curated static Gemini list (#1211).

        No practical Vertex REST endpoint lists Gemini models, so discovery
        reports a small curated fallback (see ``_VERTEX_STATIC_MODELS``)
        rather than failing or returning an untyped catalog.
        """
        return [
            DiscoveredModel(
                id=model_id,
                chat=True,
                tools=True,
                context_window_size=_GEMINI_CONTEXT_WINDOW,
                owned_by=owned_by,
                confirmed=confirmed,
            )
            for model_id, owned_by, confirmed in _VERTEX_STATIC_MODELS
        ]


# ---------------------------------------------------------------------------
# Streaming state
# ---------------------------------------------------------------------------


class _VertexStreamState:
    """Accumulates Gemini streaming state across SSE events."""

    def __init__(self, on_chunk: StreamCallback | None = None):
        self.on_chunk = on_chunk
        self.all_text_parts: list[str] = []
        self.all_thinking_parts: list[str] = []
        self.all_tool_calls: list[dict] = []
        self.tool_calls_seen = 0
        self.usage: dict | None = None
        self.finish_reason: str | None = None
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
                "cached_tokens": chunk_usage.get("cachedContentTokenCount", 0),
            }

        candidates = chunk.get("candidates", [])
        if not candidates:
            return

        raw_reason = candidates[0].get("finishReason")
        if raw_reason == "MAX_TOKENS":
            self.finish_reason = "length"
        elif raw_reason == "STOP":
            self.finish_reason = "stop"
        elif raw_reason:
            self.finish_reason = raw_reason.lower()

        parts = candidates[0].get("content", {}).get("parts", [])

        # Gemini sends incremental text per chunk — emit directly
        chunk_text = ""
        chunk_thinking = ""
        chunk_tool_calls = []
        for part in parts:
            if part.get("thought") is True:
                chunk_thinking += part.get("text", "")
            elif "text" in part:
                chunk_text += part["text"]
            elif "functionCall" in part:
                fc = dict(part["functionCall"])
                sig = (
                    part.get("thoughtSignature")
                    or part.get("thought_signature")
                    or fc.get("thoughtSignature")
                    or fc.get("thought_signature")
                )
                if sig:
                    fc["thought_signature"] = sig
                chunk_tool_calls.append(fc)

        if chunk_thinking:
            self.all_thinking_parts.append(chunk_thinking)
            await self._emit("thinking", chunk_thinking)

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
        thinking = "".join(self.all_thinking_parts) or None
        tool_calls = None
        if self.all_tool_calls:
            tool_calls = []
            for fc in self.all_tool_calls:
                tc = {
                    "id": f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {})),
                    },
                }
                sig = fc.get("thought_signature") or fc.get("thoughtSignature")
                if sig:
                    tc["thought_signature"] = sig
                tool_calls.append(tc)

        finish_reason = self.finish_reason
        if finish_reason is None and (content or thinking or tool_calls):
            finish_reason = "stop"

        await self._emit("done", {"usage": self.usage})

        if not content and not thinking and not tool_calls and self._all_events:
            log.warning(
                "Vertex returned empty — SSE dump (%d events):",
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


# ---------------------------------------------------------------------------
# Message format translation
# ---------------------------------------------------------------------------

_VERTEX_UNSUPPORTED_KEYS = frozenset(
    {
        # Schema metadata Vertex doesn't process.
        "$schema",
        "$id",
        "$defs",
        "definitions",
        "$ref",
        # JSON Schema keywords for property-name / pattern-shape constraints —
        # not in the OpenAPI 3 subset Vertex accepts.
        "propertyNames",
        "patternProperties",
        "dependencies",
        "dependentRequired",
        "dependentSchemas",
        # Conditional schema branches.
        "if",
        "then",
        "else",
        # Numeric constraint Vertex rejects on some schema versions.
        "multipleOf",
    }
)


_SEMANTIC_CONSTRAINT_NOTES: dict[str, str] = {
    # JSON-Schema keyword -> human-readable constraint text; ``{value}`` is interpolated
    # for scalar constraints such as a numeric ``multipleOf``. Extend this table to
    # preserve more stripped keys as prose (#1147).
    "multipleOf": "must be a multiple of {value}",
}

# Stripped constructs that carry meaning but cannot be rendered as prose.
# Dropping one changes what the model sees with no description to show for it,
# so each occurrence is reported in diagnostics (#1147).
_STRUCTURAL_CONSTRUCTS = frozenset({"patternProperties", "dependentSchemas", "if", "then", "else"})


def _semantic_notes(schema: dict) -> list[str]:
    """Render this node's stripped scalar constraints as readable notes.

    Reads the pre-strip schema: every key named here is in
    ``_VERTEX_UNSUPPORTED_KEYS`` and will not survive cleaning, so its meaning
    must be re-expressed as description text on the same node. Notes attach to
    the node where the constraint was found — never hoisted to a parent — so a
    multi-property schema keeps each constraint bound to its property (#1147).
    """
    notes: list[str] = []
    for key, template in _SEMANTIC_CONSTRAINT_NOTES.items():
        value = schema.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            notes.append(template.format(value=value))
    property_names = schema.get("propertyNames")
    if isinstance(property_names, dict) and isinstance(property_names.get("pattern"), str):
        notes.append(f"property names must match pattern {property_names['pattern']}")
    for key in ("dependencies", "dependentRequired"):
        deps = schema.get(key)
        if isinstance(deps, dict):
            for prop, required in deps.items():
                if isinstance(required, list) and all(isinstance(r, str) for r in required):
                    verb = "is" if len(required) == 1 else "are"
                    notes.append(f"if '{prop}' is present, {', '.join(required)} {verb} required")
    return notes


def _structural_diagnostics(schema: dict) -> list[str]:
    """Name stripped constructs whose meaning cannot be rendered as prose."""
    diags: list[str] = []
    for key in _STRUCTURAL_CONSTRUCTS:
        if key in schema:
            diags.append(f"stripped structural construct '{key}' (not renderable as prose)")
    for key in ("dependencies", "dependentRequired"):
        deps = schema.get(key)
        if isinstance(deps, dict):
            for prop, required in deps.items():
                if not (isinstance(required, list) and all(isinstance(r, str) for r in required)):
                    diags.append(
                        f"stripped '{key}' entry for {prop!r} (schema-form dependency, not renderable as prose)"
                    )
    return diags


def _resolve_local_ref(schema: dict, root: dict, seen: set[str]) -> tuple[dict | None, str | None]:
    """Resolve a local JSON-pointer ``$ref`` (e.g. ``#/$defs/Foo``) against the root schema.

    Returns ``(resolved, None)`` on success or ``(None, diagnostic)`` when the
    reference is external, unresolvable, or circular. Sibling keys of ``$ref``
    are preserved on top of the resolved content by the caller.
    """
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None, f"unresolvable $ref {str(ref)!r}; parameter treated as unconstrained"
    if ref in seen:
        return None, f"circular $ref {ref!r}; parameter treated as unconstrained"
    node: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return None, f"unresolvable $ref {ref!r}; parameter treated as unconstrained"
    if not isinstance(node, dict):
        return None, f"unresolvable $ref {ref!r} (non-object target); parameter treated as unconstrained"
    return node, None


def _clean_schema_preserving(
    schema: dict, root: dict | None = None, seen_refs: set[str] | None = None
) -> tuple[dict, list[str]]:
    """Clean a tool-parameter schema for Vertex while preserving stripped semantic constraints.

    Vertex's tool-parameter schema is a restricted subset of OpenAPI 3; MCP-provided
    schemas (e.g. Playwright) frequently use full JSON-Schema features that the API
    rejects with HTTP 400 ``Unknown name "X"``. The stripped keys are a denylist
    (``_VERTEX_UNSUPPORTED_KEYS``) rather than an allowlist so legitimate constraints
    (``minLength``, ``enum``, ...) survive. On top of that strip this pass:

    - renders stripped scalar constraints (``multipleOf``, ``propertyNames.pattern``,
      required-form dependencies) as readable description text **on the node where
      they were found**, so a multi-property schema keeps each constraint bound to
      its property (#1147);
    - resolves local ``$ref``s (``#/$defs/...``, ``#/definitions/...``) so a
      referenced shape is not silently dropped; unresolvable or circular refs are
      reported in diagnostics and annotated in the description rather than left as
      a silent unconstrained object;
    - reports stripped structural constructs (``patternProperties``, ``if/then/else``,
      ...) in diagnostics, since their meaning cannot be rendered as prose.

    Returns ``(cleaned_schema, diagnostics)``. The canonical registry schema is
    never mutated: every cleaned value is derived from the incoming schema, and
    sibling keys of a resolved ``$ref`` are preserved on top of the referenced content.
    """
    if root is None:
        root = schema
    if seen_refs is None:
        seen_refs = set()

    diags: list[str] = []
    source = schema

    # Follow $ref chains until the node no longer references, or a ref is
    # unresolvable/circular. ``seen_refs`` tracks the resolution path (shared
    # with nested recursion) so a cycle is reported instead of followed forever.
    resolved_path: list[str] = []
    while True:
        ref = source.get("$ref") if isinstance(source, dict) else None
        if not isinstance(ref, str):
            break
        resolved, diag = _resolve_local_ref(source, root, seen_refs)
        if resolved is None:
            diags.append(diag or f"unresolvable $ref {str(ref)!r}")
            # Keep sibling keys; the referenced shape is lost — say so in prose.
            source = {k: v for k, v in source.items() if k != "$ref"}
            note = "referenced schema could not be inlined; this parameter is unconstrained"
            base = source.get("description") or ""
            source["description"] = f"{base}; {note}" if base else note
            break
        seen_refs.add(ref)
        resolved_path.append(ref)
        source = {**resolved, **{k: v for k, v in source.items() if k != "$ref"}}

    cleaned = {k: v for k, v in source.items() if k not in _VERTEX_UNSUPPORTED_KEYS}
    diags.extend(_structural_diagnostics(source))

    notes = _semantic_notes(source)
    if notes:
        text = "; ".join(notes)
        base = cleaned.get("description") or ""
        cleaned["description"] = f"{base}; {text}" if base else text

    # Recurse with the preserving pass so nested nodes keep their own notes.
    if "properties" in cleaned:
        nested: dict = {}
        for k, v in cleaned["properties"].items():
            if isinstance(v, dict):
                child, child_diags = _clean_schema_preserving(v, root, seen_refs)
                nested[k] = child
                diags.extend(f"properties.{k}: {d}" for d in child_diags)
            else:
                nested[k] = v
        cleaned["properties"] = nested
    for key in ("items", "additionalProperties"):
        if key in cleaned and isinstance(cleaned[key], dict):
            child, child_diags = _clean_schema_preserving(cleaned[key], root, seen_refs)
            cleaned[key] = child
            diags.extend(f"{key}: {d}" for d in child_diags)
    # Recurse into combinator branches if present (Vertex supports oneOf/
    # anyOf/allOf in some versions, so we keep them but still scrub their
    # contents).
    for key in ("oneOf", "anyOf", "allOf"):
        if key in cleaned and isinstance(cleaned[key], list):
            branches: list = []
            for i, s in enumerate(cleaned[key]):
                if isinstance(s, dict):
                    child, child_diags = _clean_schema_preserving(s, root, seen_refs)
                    branches.append(child)
                    diags.extend(f"{key}[{i}]: {d}" for d in child_diags)
                else:
                    branches.append(s)
            cleaned[key] = branches

    # Release this node's resolution path so sibling nodes that reference the
    # same def are not mistaken for a cycle.
    for ref in resolved_path:
        seen_refs.discard(ref)

    return cleaned, diags


# Tool names whose schema adaptations have already been warned about. Bounded by
# the number of distinct tool names; keeps per-request re-cleaning from spamming
# the log with the same material transformation (#1147).
_warned_adaptations: set[str] = set()


def _log_schema_diagnostics(tool_name: str, diags: list[str]) -> None:
    """Report material schema transformations once per tool (then at debug)."""
    for diag in diags:
        if tool_name not in _warned_adaptations:
            log.warning("Vertex schema adaptation, tool %s: %s", tool_name, diag)
        else:
            log.debug("Vertex schema adaptation, tool %s: %s", tool_name, diag)
    if diags:
        _warned_adaptations.add(tool_name)


def _content_to_parts(content) -> list[dict]:
    """Convert message content (string or multimodal list) to Vertex parts.

    Handles both plain string content and the OpenAI multimodal format
    produced by resolve_attachments: [{"type": "text", ...}, {"type": "image_url", ...}].
    """
    if not content:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    # Multimodal list (from resolve_attachments)
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
                mime_type = header[len("data:") :]
                parts.append(
                    {
                        "inlineData": {"mimeType": mime_type, "data": payload},
                    }
                )
            else:
                log.warning("Unsupported image_url format (not a data URI), skipping")
    return parts


def _build_request_body(
    messages: list[dict],
    tools: list[dict] | None = None,
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
                part = {"functionCall": {"name": name, "args": args}}
                sig = (
                    tc.get("thought_signature")
                    or tc.get("thoughtSignature")
                    or func.get("thought_signature")
                    or func.get("thoughtSignature")
                )
                if sig:
                    part["thoughtSignature"] = sig
                parts.append(part)
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
            if (
                contents
                and contents[-1].get("role") == "user"
                and contents[-1]["parts"]
                and "functionResponse" in contents[-1]["parts"][0]
            ):
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
                cleaned, diags = _clean_schema_preserving(params)
                decl["parameters"] = cleaned
                _log_schema_diagnostics(str(func.get("name") or "?"), diags)
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
            "finish_reason": "error",
        }

    raw_reason = candidates[0].get("finishReason")
    if raw_reason == "MAX_TOKENS":
        finish_reason = "length"
    elif raw_reason == "STOP":
        finish_reason = "stop"
    elif raw_reason:
        finish_reason = raw_reason.lower()
    else:
        finish_reason = None

    parts = candidates[0].get("content", {}).get("parts", [])

    text_parts = []
    thinking_parts = []
    tool_calls = []

    for part in parts:
        if part.get("thought") is True:
            thinking_parts.append(part.get("text", ""))
        elif "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            sig = (
                part.get("thoughtSignature")
                or part.get("thought_signature")
                or fc.get("thoughtSignature")
                or fc.get("thought_signature")
            )
            tc = {
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {})),
                },
            }
            if sig:
                tc["thought_signature"] = sig
            tool_calls.append(tc)

    return {
        "content": "".join(text_parts) or None,
        "thinking": "".join(thinking_parts) or None,
        "tool_calls": tool_calls or None,
        "role": "assistant",
        "usage": _parse_usage(data),
        "finish_reason": finish_reason,
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
        "cached_tokens": meta.get("cachedContentTokenCount", 0),
    }
