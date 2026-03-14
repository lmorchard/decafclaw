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
