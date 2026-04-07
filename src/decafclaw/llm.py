"""LLM client — raw HTTP to an OpenAI-compatible endpoint (LiteLLM)."""

import asyncio
import json
import logging

import httpx

log = logging.getLogger(__name__)

# Max SSE events kept for diagnostic dump on empty LLM responses
_MAX_DIAGNOSTIC_EVENTS = 50


def _sanitize_tool_call_id(tc_id: str) -> str:
    """Strip LiteLLM's embedded thinking data from tool call IDs.

    LiteLLM packs Gemini thinking tokens into tool_call_id fields as base64
    after a ``__thought__`` delimiter (e.g. ``call_abc123__thought__CiUB...``).
    These can be ~3KB+ each and bloat conversation history. The actual ID is
    just the part before ``__thought__``.
    """
    return tc_id.split("__thought__")[0]


async def call_llm(config, messages, tools=None,
                   llm_url=None, llm_model=None, llm_api_key=None) -> dict:
    """Call the LLM and return the response message.

    Optional overrides (llm_url, llm_model, llm_api_key) allow calling
    a different LLM (e.g., for compaction) without building a temp config.

    Returns a dict with:
      - "content": str or None (the text response)
      - "tool_calls": list or None (tool calls the LLM wants to make)
      - "usage": dict or None (token usage from the API)
    """
    url = llm_url or config.llm.url
    model = llm_model or config.llm.model
    api_key = llm_api_key or config.llm.api_key

    body = {
        "model": model,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    log.debug(f"LLM request: model={model}, messages={len(messages)}, tools={len(tools) if tools else 0}")

    async with httpx.AsyncClient() as client:
        timeout = getattr(config.llm, "timeout", 300)
        resp = await client.post(url, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    message = data["choices"][0]["message"]
    usage = data.get("usage")

    if usage:
        log.debug(f"LLM usage: prompt={usage.get('prompt_tokens')}, "
                  f"completion={usage.get('completion_tokens')}")
    log.debug(f"LLM response: content={bool(message.get('content'))}, tool_calls={len(message.get('tool_calls', []))}")

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


async def call_llm_streaming(config, messages, tools=None,
                              on_chunk=None,
                              cancel_event=None,
                              llm_url=None, llm_model=None, llm_api_key=None) -> dict:
    """Call the LLM with streaming, invoking on_chunk for each token.

    Args:
        on_chunk: async callback(chunk_type, data) called for each chunk.
            chunk_type: "text" | "tool_call_start" | "tool_call_delta" | "tool_call_end" | "done"
            data: varies by type.

    Returns: same dict as call_llm (content, tool_calls, role, usage).
    """
    from httpx_sse import aconnect_sse

    url = llm_url or config.llm.url
    model = llm_model or config.llm.model
    api_key = llm_api_key or config.llm.api_key
    timeout = getattr(config.llm, "timeout", 300)

    body = {
        "model": model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    log.debug(f"LLM streaming request: model={model}, messages={len(messages)}, "
              f"tools={len(tools) if tools else 0}")

    # Accumulated state
    content_parts = []
    tool_calls_in_progress = {}  # index -> {"id", "function": {"name", "arguments"}}
    usage = None
    _stream_error = None

    async def _emit(chunk_type, data):
        if on_chunk:
            try:
                await on_chunk(chunk_type, data)
            except Exception as e:
                log.debug(f"Stream chunk callback error: {e}")

    _all_events = []  # collect for diagnostic on empty response
    _max_retries = 3
    try:
        # Retry loop for transient errors (429 rate limits, 5xx server errors).
        # On success, the loop breaks after streaming completes.
        for _attempt in range(_max_retries + 1):
            async with httpx.AsyncClient() as client:
                async with aconnect_sse(client, "POST", url, json=body,
                                         headers=headers, timeout=httpx.Timeout(timeout)) as event_source:
                    # Check HTTP status before entering SSE parsing.
                    # On 429/5xx the response is JSON, not SSE — aiter_sse()
                    # would blow up on the content-type mismatch.
                    status = event_source.response.status_code
                    if status == 429 or status >= 500:
                        error_body = (await event_source.response.aread()).decode(errors="replace")[:500]
                        if _attempt < _max_retries:
                            delay = min(int(event_source.response.headers.get(
                                "retry-after", 2 ** _attempt)), 30)
                            log.warning(f"LLM {'rate limited' if status == 429 else 'server error'} "
                                        f"({status}), retrying in {delay}s "
                                        f"(attempt {_attempt + 1}/{_max_retries}): {error_body[:200]}")
                            await asyncio.sleep(delay)
                            continue
                        raise Exception(f"LLM failed after {_max_retries} retries "
                                        f"(status {status}): {error_body[:200]}")

                    async for event in event_source.aiter_sse():
                        if cancel_event and cancel_event.is_set():
                            log.info("LLM streaming cancelled by user")
                            break
                        if event.data == "[DONE]":
                            break

                        # Log non-default SSE event types (e.g. "error")
                        event_type = getattr(event, "event", None)
                        if event_type and event_type != "message":
                            log.warning(f"LLM SSE event type={event_type}: {event.data[:500]}")

                        try:
                            chunk = json.loads(event.data)
                        except json.JSONDecodeError:
                            log.debug(f"LLM non-JSON SSE data: {event.data[:200]}")
                            continue

                        _all_events.append(chunk)
                        if len(_all_events) > _MAX_DIAGNOSTIC_EVENTS:
                            _all_events.pop(0)

                        # Check for usage in final chunk
                        chunk_usage = chunk.get("usage")
                        if chunk_usage:
                            usage = chunk_usage

                        choices = chunk.get("choices", [])
                        if not choices:
                            # Log chunks with no choices (may contain error info)
                            if chunk.get("error") or not chunk.get("usage"):
                                log.debug(f"LLM chunk with no choices: {json.dumps(chunk)[:500]}")
                            continue

                        # Log non-normal finish reasons
                        finish_reason = choices[0].get("finish_reason")
                        if finish_reason and finish_reason not in ("stop", "tool_calls"):
                            log.warning(f"LLM finish_reason: {finish_reason}")

                        delta = choices[0].get("delta", {})

                        # Text content
                        text = delta.get("content")
                        if text:
                            content_parts.append(text)
                            await _emit("text", text)

                        # Tool calls
                        tc_deltas = delta.get("tool_calls")
                        if tc_deltas:
                            for tc_delta in tc_deltas:
                                idx = tc_delta.get("index", 0)
                                func = tc_delta.get("function", {})

                                if idx not in tool_calls_in_progress:
                                    # New tool call
                                    raw_id = tc_delta.get("id", f"call_{idx}")
                                    tool_calls_in_progress[idx] = {
                                        "id": _sanitize_tool_call_id(raw_id),
                                        "type": "function",
                                        "function": {
                                            "name": func.get("name", ""),
                                            "arguments": func.get("arguments", ""),
                                        },
                                    }
                                    if func.get("name"):
                                        await _emit("tool_call_start", {
                                            "index": idx,
                                            "name": func["name"],
                                        })
                                else:
                                    # Append to existing tool call
                                    args_delta = func.get("arguments", "")
                                    if args_delta:
                                        tool_calls_in_progress[idx]["function"]["arguments"] += args_delta
                                        await _emit("tool_call_delta", {
                                            "index": idx,
                                            "arguments_delta": args_delta,
                                        })
                                    # Update name if provided (rare but possible)
                                    name_delta = func.get("name", "")
                                    if name_delta:
                                        tool_calls_in_progress[idx]["function"]["name"] += name_delta

            break  # Success — exit retry loop

    except Exception as e:
        log.error(f"LLM streaming error: {e}")
        _stream_error = e

    # If nothing was accumulated and an error occurred, re-raise so the caller
    # knows the LLM call failed (instead of silently returning empty).
    if _stream_error and not content_parts and not tool_calls_in_progress:
        raise _stream_error

    # Finalize tool calls
    tool_calls = None
    if tool_calls_in_progress:
        tool_calls = [tool_calls_in_progress[i] for i in sorted(tool_calls_in_progress.keys())]
        for idx, tc in enumerate(tool_calls):
            await _emit("tool_call_end", {
                "index": idx,
                "name": tc["function"]["name"],
                "arguments": tc["function"]["arguments"],
            })

    content = "".join(content_parts) or None
    await _emit("done", {"usage": usage})

    if usage:
        log.debug(f"LLM streaming usage: prompt={usage.get('prompt_tokens')}, "
                  f"completion={usage.get('completion_tokens')}")
    log.debug(f"LLM streaming response: content={bool(content)}, "
              f"tool_calls={len(tool_calls) if tool_calls else 0}")

    # Dump full event stream on empty response for diagnostics
    if not content and not tool_calls_in_progress and _all_events:
        log.warning(f"LLM returned empty — full SSE event dump ({len(_all_events)} events):")
        for i, evt in enumerate(_all_events):
            log.warning(f"  event[{i}]: {json.dumps(evt)[:500]}")

    return {
        "content": content,
        "tool_calls": tool_calls,
        "role": "assistant",
        "usage": usage,
    }
