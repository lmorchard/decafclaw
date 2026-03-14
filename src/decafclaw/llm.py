"""LLM client — raw HTTP to an OpenAI-compatible endpoint (LiteLLM)."""

import httpx
import json
import logging

log = logging.getLogger(__name__)


async def call_llm(config, messages, tools=None):
    """Call the LLM and return the response message.

    Returns a dict with:
      - "content": str or None (the text response)
      - "tool_calls": list or None (tool calls the LLM wants to make)
    """
    body = {
        "model": config.llm_model,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.llm_api_key}",
    }

    log.debug(f"LLM request: model={config.llm_model}, messages={len(messages)}, tools={len(tools) if tools else 0}")

    async with httpx.AsyncClient() as client:
        resp = await client.post(config.llm_url, json=body, headers=headers, timeout=120)
    resp.raise_for_status()
    data = resp.json()

    message = data["choices"][0]["message"]

    log.debug(f"LLM response: content={bool(message.get('content'))}, tool_calls={len(message.get('tool_calls', []))}")

    return {
        "content": message.get("content"),
        "tool_calls": message.get("tool_calls"),
        "role": "assistant",
    }
