"""Assistant history construction and private provider replay metadata."""

from copy import deepcopy
from typing import Any


def assistant_message(response: dict[str, Any], content: str | None = None) -> dict[str, Any]:
    """Keep replay state only when history still represents this response.

    Reflection can append an escalation notice to the visible text. In that
    case replaying the original output would silently undo that edit.
    """
    original = response.get("content")
    message = {"role": "assistant", "content": original if content is None else content}
    if response.get("tool_calls"):
        message["tool_calls"] = deepcopy(response["tool_calls"])
    if response.get("provider_data") and (message["content"] or "") == (original or ""):
        message["provider_data"] = deepcopy(response["provider_data"])
    return message


def without_provider_data(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Never send opaque replay metadata to another API's message schema."""
    return [{key: value for key, value in message.items() if key != "provider_data"} for message in messages]
