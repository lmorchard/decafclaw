"""Forced-tool structured-output helper for workflow llm_call.

Exposes exactly one tool the model MUST call, parses its args, and retries
once with a stricter nudge on narrate-stall. Provider-agnostic; proven on
vertex-gemini-flash by the #255 spike.
"""
import json
import logging

from decafclaw.llm import call_llm

log = logging.getLogger(__name__)


async def call_structured(ctx, *, system: str, user_msg: str, schema: dict,
                          tool_name: str, model: str, description: str = "",
                          retries: int = 1) -> dict:
    """Force a structured response. Returns parsed tool args, or raises."""
    base_user = user_msg
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": base_user},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description or (
                "Submit the structured result for this step. "
                "You MUST call this — do not respond with prose."),
            "parameters": schema,
        },
    }]
    last_error: str | None = None
    for _ in range(retries + 1):
        result = await call_llm(ctx.config, messages, tools=tools,
                                model_name=model)
        tool_calls = result.get("tool_calls") or []
        if tool_calls:
            args_raw = tool_calls[0].get("function", {}).get("arguments") or "{}"
            try:
                return json.loads(args_raw)
            except json.JSONDecodeError as e:
                last_error = f"invalid JSON in tool args: {e}; raw={args_raw[:200]!r}"
        else:
            last_error = (
                f"model emitted text instead of calling {tool_name!r}: "
                f"{(result.get('content') or '')[:200]!r}")
        log.debug("call_structured(%s) attempt failed: %s", tool_name, last_error)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                base_user + f"\n\nIMPORTANT: You MUST call the tool "
                f"`{tool_name}` now. Do not narrate. Emit only the call.")},
        ]
    raise RuntimeError(
        f"structured call to {tool_name} failed after {retries + 1} "
        f"attempts: {last_error}")
