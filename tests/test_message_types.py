"""Tests for the WS message-type manifest, ensuring the generated artifacts
stay aligned with the runtime call sites that consume them."""
from __future__ import annotations

import re
from pathlib import Path

from decafclaw.web.message_types import KNOWN_MESSAGE_TYPES, WSMessageType
from decafclaw.web.websocket import _HANDLERS

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_PATH = REPO_ROOT / "src/decafclaw/web/static/lib/message-types.js"


def test_all_handler_keys_are_known_types() -> None:
    for key in _HANDLERS:
        assert key in KNOWN_MESSAGE_TYPES, f"unknown handler key: {key!r}"


def test_js_constants_match_python_enum() -> None:
    text = JS_PATH.read_text(encoding="utf-8")
    block = re.search(
        r"export const MESSAGE_TYPES = Object\.freeze\(\{(.*?)\}\);",
        text,
        re.DOTALL,
    )
    assert block, "MESSAGE_TYPES literal block not found in generated JS"
    js_values = set(re.findall(r"'([a-z_][a-z0-9_]*)'", block.group(1)))
    py_values = {t.value for t in WSMessageType}
    assert js_values == py_values, (
        f"JS↔Python mismatch — only in JS: {js_values - py_values}, "
        f"only in Python: {py_values - js_values}"
    )
