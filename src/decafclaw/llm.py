"""LLM client — raw HTTP to an OpenAI-compatible endpoint (LiteLLM)."""

import httpx
import json
import logging

log = logging.getLogger(__name__)


async def call_llm(config, messages, tools=None,
                   llm_url=None, llm_model=None, llm_api_key=None):
    """Call the LLM and return the response message.

    Optional overrides (llm_url, llm_model, llm_api_key) allow calling
    a different LLM (e.g., for compaction) without building a temp config.

    Returns a dict with:
      - "content": str or None (the text response)
      - "tool_calls": list or None (tool calls the LLM wants to make)
      - "usage": dict or None (token usage from the API)
    """
    url = llm_url or config.llm_url
    model = llm_model or config.llm_model
    api_key = llm_api_key or config.llm_api_key

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
        resp = await client.post(url, json=body, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    message = data["choices"][0]["message"]
    usage = data.get("usage")

    if usage:
        log.debug(f"LLM usage: prompt={usage.get('prompt_tokens')}, "
                  f"completion={usage.get('completion_tokens')}")
    log.debug(f"LLM response: content={bool(message.get('content'))}, tool_calls={len(message.get('tool_calls', []))}")

    return {
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls"),
        "role": "assistant",
        "usage": usage,
    }


async def call_llm_streaming(config, messages, tools=None,
                              on_chunk=None,
                              llm_url=None, llm_model=None, llm_api_key=None):
    """Call the LLM with streaming, invoking on_chunk for each token.

    Args:
        on_chunk: async callback(chunk_type, data) called for each chunk.
            chunk_type: "text" | "tool_call_start" | "tool_call_delta" | "tool_call_end" | "done"
            data: varies by type.

    Returns: same dict as call_llm (content, tool_calls, role, usage).
    """
    from httpx_sse import aconnect_sse

    url = llm_url or config.llm_url
    model = llm_model or config.llm_model
    api_key = llm_api_key or config.llm_api_key

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

    async def _emit(chunk_type, data):
        if on_chunk:
            try:
                await on_chunk(chunk_type, data)
            except Exception as e:
                log.debug(f"Stream chunk callback error: {e}")

    try:
        async with httpx.AsyncClient() as client:
            async with aconnect_sse(client, "POST", url, json=body,
                                     headers=headers, timeout=httpx.Timeout(120.0)) as event_source:
                async for event in event_source.aiter_sse():
                    if event.data == "[DONE]":
                        break

                    try:
                        chunk = json.loads(event.data)
                    except json.JSONDecodeError:
                        continue

                    # Check for usage in final chunk
                    chunk_usage = chunk.get("usage")
                    if chunk_usage:
                        usage = chunk_usage

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

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
                                tool_calls_in_progress[idx] = {
                                    "id": tc_delta.get("id", f"call_{idx}"),
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

    except Exception as e:
        log.error(f"LLM streaming error: {e}")

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

    return {
        "content": content,
        "tool_calls": tool_calls,
        "role": "assistant",
        "usage": usage,
    }
