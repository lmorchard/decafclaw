#!/usr/bin/env python3
"""Generate Python enum, JS constants, and Markdown docs for WebSocket message types.

Source of truth: src/decafclaw/web/message_types.json
Run via: make gen-message-types
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "src/decafclaw/web/message_types.json"
PY_OUT = REPO_ROOT / "src/decafclaw/web/message_types.py"
JS_OUT = REPO_ROOT / "src/decafclaw/web/static/lib/message-types.js"
DOC_OUT = REPO_ROOT / "docs/websocket-messages.md"
TS_OUT = REPO_ROOT / "tui/src/types.generated.ts"

GEN_HEADER = "DO NOT EDIT — regenerate via 'make gen-message-types'"
SOURCE_REL = MANIFEST_PATH.relative_to(REPO_ROOT).as_posix()

VALID_DIRECTIONS = ("server_to_client", "client_to_server", "bidirectional")
NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def load_manifest() -> dict:
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if data.get("$schema_version") != 1:
        sys.exit(f"unsupported $schema_version in {MANIFEST_PATH}")
    msgs = data.get("messages")
    if not isinstance(msgs, dict):
        sys.exit("manifest 'messages' must be an object")
    for name, entry in msgs.items():
        if not NAME_RE.match(name):
            sys.exit(f"invalid message name: {name!r}")
        direction = entry.get("direction")
        if direction not in VALID_DIRECTIONS:
            sys.exit(f"{name}: invalid direction {direction!r}")
        if not isinstance(entry.get("description"), str):
            sys.exit(f"{name}: missing/non-string description")
        if not isinstance(entry.get("fields"), dict):
            sys.exit(f"{name}: 'fields' must be an object")
    return data


def sorted_messages(data: dict) -> list[tuple[str, dict]]:
    direction_order = {d: i for i, d in enumerate(VALID_DIRECTIONS)}
    return sorted(
        data["messages"].items(),
        key=lambda kv: (direction_order[kv[1]["direction"]], kv[0]),
    )


_SCALAR_TYPES = {
    "string":  ("str", "string"),
    "number":  ("int", "number"),
    "boolean": ("bool", "boolean"),
    "object":  ("dict[str, object]", "Record<string, unknown>"),
    "null":    ("None", "null"),
}


def _scalar_pair(s: str) -> tuple[str, str]:
    """Map a single scalar type name to (python, typescript) pair."""
    if s not in _SCALAR_TYPES:
        raise ValueError(f"unknown scalar type: {s!r}")
    return _SCALAR_TYPES[s]


def _interface_name(message_name: str, direction: str) -> str:
    """tool_call_start, server_to_client -> SrvToolCallStart.

    Currently only server_to_client and client_to_server are supported.
    Bidirectional messages don't exist in the manifest yet and would need
    their own naming convention (probably `Bi` prefix) plus a third
    union in render_python_typed / render_typescript.
    """
    parts = [w.capitalize() for w in message_name.split("_")]
    if direction == "server_to_client":
        prefix = "Srv"
    elif direction == "client_to_server":
        prefix = "Cli"
    else:
        raise ValueError(
            f"_interface_name: unsupported direction {direction!r} for "
            f"message {message_name!r}"
        )
    return prefix + "".join(parts)


def parse_field_type(s: str) -> tuple[str, str, bool]:
    """Parse a manifest field-type string.

    Returns ``(python_base_type, ts_base_type, required)``. Renderers
    wrap the base type with ``NotRequired[]`` (Python) or a ``?:`` field
    suffix (TS) when ``required`` is False.

    Grammar:
      * scalar: "string" | "number" | "boolean" | "object" | "null"
      * array:  "array of <scalar>"
      * union:  "<scalar> | <scalar>" (two members; e.g. "string | null")
      * optional: any of the above with a trailing "?"
    """
    optional = s.endswith("?")
    if optional:
        s = s[:-1]

    if s.startswith("array of "):
        elem = s[len("array of "):]
        # Array element types are explicit (not derived from _SCALAR_TYPES) —
        # current manifest only uses arrays of string and object. Add new
        # element types here AND to _SCALAR_TYPES if scalar support is needed.
        if elem == "string":
            return ("list[str]", "string[]", not optional)
        if elem == "object":
            return ("list[dict[str, object]]", "Array<Record<string, unknown>>", not optional)
        raise ValueError(f"unknown array element type: {elem!r}")

    if " | " in s:
        py_parts: list[str] = []
        ts_parts: list[str] = []
        for part in s.split(" | "):
            py, ts = _scalar_pair(part)
            py_parts.append(py)
            ts_parts.append(ts)
        return (" | ".join(py_parts), " | ".join(ts_parts), not optional)

    py, ts = _scalar_pair(s)
    return (py, ts, not optional)


def render_python(data: dict) -> str:
    items = sorted_messages(data)
    out: list[str] = []
    out.append(f'"""{GEN_HEADER}\n\nSource: {SOURCE_REL}\n"""')
    out.append("")
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from collections.abc import Awaitable, Callable")
    out.append("from enum import StrEnum")
    out.append("from typing import Literal, NotRequired, TypedDict")
    out.append("")
    out.append("")
    out.append("class WSMessageType(StrEnum):")
    out.append('    """WebSocket wire message types."""')
    out.append("")
    for name, _ in items:
        out.append(f'    {name.upper()} = "{name}"')
    out.append("")
    out.append("")
    out.append("KNOWN_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset(WSMessageType)")
    out.append("")

    def _emit_subset(var: str, names: list[str]) -> None:
        if not names:
            out.append(f"{var}: frozenset[WSMessageType] = frozenset()")
            out.append("")
            return
        out.append(f"{var}: frozenset[WSMessageType] = frozenset({{")
        for n in names:
            out.append(f"    WSMessageType.{n.upper()},")
        out.append("})")
        out.append("")

    s2c = [n for n, e in items if e["direction"] == "server_to_client"]
    c2s = [n for n, e in items if e["direction"] == "client_to_server"]
    bid = [n for n, e in items if e["direction"] == "bidirectional"]
    _emit_subset("S2C_MESSAGE_TYPES", s2c)
    _emit_subset("C2S_MESSAGE_TYPES", c2s)
    _emit_subset("BIDIRECTIONAL_MESSAGE_TYPES", bid)
    return "\n".join(out).rstrip() + "\n"


def render_python_typed(data: dict) -> str:
    """Render TypedDicts, discriminated unions, and WSSendCallable.

    Appended after `render_python`'s output in the same file.
    """
    items = sorted_messages(data)
    out: list[str] = ["", "", "# -- TypedDicts (one per wire message) --", ""]

    s2c_ifaces: list[str] = []
    c2s_ifaces: list[str] = []

    for name, entry in items:
        iface = _interface_name(name, entry["direction"])
        if entry["direction"] == "server_to_client":
            s2c_ifaces.append(iface)
        elif entry["direction"] == "client_to_server":
            c2s_ifaces.append(iface)
        # bidirectional (none currently) would need a separate bucket

        out.append(f"class {iface}(TypedDict):")
        # Use Literal[WSMessageType.X] (not Literal["x"]) so existing emit
        # sites that pass WSMessageType.X enum values continue to narrow
        # correctly. Pyright treats StrEnum members as enum-member literals,
        # not as `str` literals, so the latter would force every call site
        # to be rewritten with bare strings.
        out.append(f'    type: Literal[WSMessageType.{name.upper()}]')
        for fname, ftype in entry["fields"].items():
            py_base, _, required = parse_field_type(ftype)
            if required:
                out.append(f"    {fname}: {py_base}")
            else:
                out.append(f"    {fname}: NotRequired[{py_base}]")
        out.append("")

    out.append("")
    out.append("# -- Discriminated unions --")
    out.append("")
    if s2c_ifaces:
        out.append("ServerMessage = " + " | ".join(s2c_ifaces))
        out.append("")
    if c2s_ifaces:
        out.append("ClientMessage = " + " | ".join(c2s_ifaces))
        out.append("")

    out.append("")
    out.append("# -- Callable alias for ws_send and friends --")
    out.append("")
    out.append("WSSendCallable = Callable[[ServerMessage], Awaitable[None]]")
    out.append("")

    return "\n".join(out)


def render_typescript(data: dict) -> str:
    """Render TS interfaces + discriminated unions for the TUI."""
    items = sorted_messages(data)
    out: list[str] = []
    out.append(f"// {GEN_HEADER}")
    out.append(f"// Source: {SOURCE_REL}")
    out.append("")

    s2c_ifaces: list[str] = []
    c2s_ifaces: list[str] = []

    for name, entry in items:
        iface = _interface_name(name, entry["direction"])
        if entry["direction"] == "server_to_client":
            s2c_ifaces.append(iface)
        elif entry["direction"] == "client_to_server":
            c2s_ifaces.append(iface)

        out.append(f"export interface {iface} {{")
        out.append(f'  type: "{name}";')
        for fname, ftype in entry["fields"].items():
            _, ts_base, required = parse_field_type(ftype)
            suffix = "" if required else "?"
            out.append(f"  {fname}{suffix}: {ts_base};")
        out.append("}")
        out.append("")

    def _emit_union(union_name: str, ifaces: list[str]) -> None:
        if not ifaces:
            return
        # Build member lines locally so the trailing-semicolon mutation
        # is on a small list, not on the running `out` buffer.
        lines = [f"  | {iface}" for iface in ifaces]
        lines[-1] += ";"
        out.append(f"export type {union_name} =")
        out.extend(lines)
        out.append("")

    _emit_union("ServerMessage", s2c_ifaces)
    _emit_union("ClientMessage", c2s_ifaces)

    return "\n".join(out).rstrip() + "\n"


def render_js(data: dict) -> str:
    items = sorted_messages(data)
    out: list[str] = []
    out.append(f"// {GEN_HEADER}")
    out.append(f"// Source: {SOURCE_REL}")
    out.append("")
    out.append("export const MESSAGE_TYPES = Object.freeze({")
    for name, _ in items:
        out.append(f"  {name.upper()}: '{name}',")
    out.append("});")
    out.append("")
    out.append("export const KNOWN_MESSAGE_TYPES = new Set(Object.values(MESSAGE_TYPES));")
    return "\n".join(out) + "\n"


def render_doc(data: dict) -> str:
    out: list[str] = []
    out.append(f"<!-- {GEN_HEADER} -->")
    out.append(f"<!-- Source: {SOURCE_REL} -->")
    out.append("")
    out.append("# WebSocket Message Types")
    out.append("")
    header = (data.get("_doc_header") or "").strip()
    if header:
        out.append(header)
        out.append("")
    out.append(
        "> **Field types are enforced.** The codegen at `scripts/gen_message_types.py` "
        "parses these field-type strings and emits matching TypedDicts (Python) "
        "and TypeScript interfaces (`tui/src/types.generated.ts`). Pyright validates "
        "every `ws_send` call site against the Python TypedDicts; tsc validates "
        "every TUI consumer against the TS interfaces. Drift between this manifest "
        "and either typed surface fails `make check-message-types`. "
        "Type-string grammar: `string`, `number`, `boolean`, `object`, "
        "`array of string`, `array of object`, `X | Y` unions; trailing `?` "
        "marks optional fields."
    )
    out.append("")
    sections = (
        ("Server → Client", "server_to_client"),
        ("Client → Server", "client_to_server"),
        ("Bidirectional", "bidirectional"),
    )
    for heading, direction in sections:
        in_dir = sorted(
            (n, e) for n, e in data["messages"].items() if e["direction"] == direction
        )
        if not in_dir:
            continue
        out.append(f"## {heading}")
        out.append("")
        for name, entry in in_dir:
            out.append(f"### `{name}`")
            out.append("")
            out.append(entry["description"])
            out.append("")
            fields = entry.get("fields") or {}
            if fields:
                out.append("**Fields:**")
                out.append("")
                for fname, ftype in fields.items():
                    out.append(f"- `{fname}` — {ftype}")
                out.append("")
            else:
                out.append("(No payload fields.)")
                out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    data = load_manifest()
    PY_OUT.write_text(render_python(data) + render_python_typed(data), encoding="utf-8")
    JS_OUT.write_text(render_js(data), encoding="utf-8")
    DOC_OUT.write_text(render_doc(data), encoding="utf-8")
    TS_OUT.write_text(render_typescript(data), encoding="utf-8")
    for p in (PY_OUT, JS_OUT, DOC_OUT, TS_OUT):
        print(f"wrote {p.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
