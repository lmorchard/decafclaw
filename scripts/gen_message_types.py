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


def render_python(data: dict) -> str:
    items = sorted_messages(data)
    out: list[str] = []
    out.append(f'"""{GEN_HEADER}\n\nSource: {SOURCE_REL}\n"""')
    out.append("")
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from enum import StrEnum")
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
        "> **Future direction:** Field types are human-readable sketches today, not validators. "
        "Future work could grow them into typed entries (`{type, optional, enum}`, "
        "`{type: \"array\", items: ...}`) for runtime validation. Out of scope at present."
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
    PY_OUT.write_text(render_python(data), encoding="utf-8")
    JS_OUT.write_text(render_js(data), encoding="utf-8")
    DOC_OUT.write_text(render_doc(data), encoding="utf-8")
    for p in (PY_OUT, JS_OUT, DOC_OUT):
        print(f"wrote {p.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
