"""Tests for the WS message-type manifest, ensuring the generated artifacts
stay aligned with the runtime call sites that consume them."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Literal, get_args, get_origin, get_type_hints

import pytest

import decafclaw.web.message_types as mt
from decafclaw.web.message_types import KNOWN_MESSAGE_TYPES, WSMessageType
from decafclaw.web.websocket import _HANDLERS

REPO_ROOT = Path(__file__).resolve().parent.parent
JS_PATH = REPO_ROOT / "src/decafclaw/web/static/lib/message-types.js"

_GEN_PATH = Path(__file__).resolve().parent.parent / "scripts/gen_message_types.py"


def _load_gen_module():
    spec = importlib.util.spec_from_file_location("gen_message_types", _GEN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GEN_MODULE = _load_gen_module()  # load once at import time


def _iface_name(member: WSMessageType, direction: str) -> str:
    """Mirror of gen_message_types._interface_name for test-side use."""
    parts = [w.capitalize() for w in member.value.split("_")]
    if direction == "server_to_client":
        return "Srv" + "".join(parts)
    if direction == "client_to_server":
        return "Cli" + "".join(parts)
    raise ValueError(f"unsupported direction: {direction!r}")


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


@pytest.mark.parametrize("inp,expected_py,expected_ts,expected_required", [
    ("string", "str", "string", True),
    ("number", "int", "number", True),
    ("boolean", "bool", "boolean", True),
    ("object", "dict[str, object]", "Record<string, unknown>", True),
    ("array of string", "list[str]", "string[]", True),
    ("array of object", "list[dict[str, object]]", "Array<Record<string, unknown>>", True),
    ("string | null", "str | None", "string | null", True),
    ("string | object", "str | dict[str, object]", "string | Record<string, unknown>", True),
    ("string?", "str", "string", False),
    ("object?", "dict[str, object]", "Record<string, unknown>", False),
])
def test_parse_field_type(inp, expected_py, expected_ts, expected_required):
    py, ts, req = _GEN_MODULE.parse_field_type(inp)
    assert py == expected_py
    assert ts == expected_ts
    assert req == expected_required


def test_parse_field_type_unknown_scalar_raises():
    with pytest.raises(ValueError, match="unknown"):
        _GEN_MODULE.parse_field_type("frobnicator")


def test_parse_field_type_unknown_array_element_raises():
    with pytest.raises(ValueError, match="unknown array"):
        _GEN_MODULE.parse_field_type("array of frobnicator")


def test_typeddict_exists_for_every_message_type() -> None:
    """Every WSMessageType must have a TypedDict in message_types module."""
    for member in WSMessageType:
        # Determine direction from the existing frozensets.
        if member in mt.S2C_MESSAGE_TYPES:
            iface = _iface_name(member, "server_to_client")
        elif member in mt.C2S_MESSAGE_TYPES:
            iface = _iface_name(member, "client_to_server")
        else:
            continue  # bidirectional (none currently)
        assert hasattr(mt, iface), f"missing TypedDict {iface} for {member.value}"


def test_server_message_union_covers_all_s2c() -> None:
    union_members = set(get_args(mt.ServerMessage))
    expected = set()
    for member in mt.S2C_MESSAGE_TYPES:
        expected.add(getattr(mt, _iface_name(member, "server_to_client")))
    assert union_members == expected


def test_client_message_union_covers_all_c2s() -> None:
    union_members = set(get_args(mt.ClientMessage))
    expected = set()
    for member in mt.C2S_MESSAGE_TYPES:
        expected.add(getattr(mt, _iface_name(member, "client_to_server")))
    assert union_members == expected


def test_every_typeddict_has_correct_type_literal() -> None:
    """Each TypedDict's `type` field must be Literal[WSMessageType.X] (enum-member form)."""
    for member in WSMessageType:
        if member in mt.S2C_MESSAGE_TYPES:
            iface = _iface_name(member, "server_to_client")
        elif member in mt.C2S_MESSAGE_TYPES:
            iface = _iface_name(member, "client_to_server")
        else:
            continue
        td = getattr(mt, iface)
        hints = get_type_hints(td)
        type_hint = hints["type"]
        assert get_origin(type_hint) is Literal
        args = get_args(type_hint)
        assert len(args) == 1 and args[0] is member, (
            f"expected Literal[{member!r}], got Literal[{args!r}]"
        )
