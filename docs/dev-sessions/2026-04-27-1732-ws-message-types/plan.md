# Centralize WebSocket message types — Implementation Plan

**Goal:** Replace ~50 hand-written WS message-type literals across Python and JS with a manifest-driven, drift-checked, code-generated single source of truth.

**Architecture:** A hand-edited JSON manifest (`src/decafclaw/web/message_types.json`) is the source of truth. A stdlib-only generator (`scripts/gen_message_types.py`) produces a `StrEnum` for Python, a frozen JS constants module, and a generated docs page. Make targets enforce drift detection in CI. Existing call sites in `web/websocket.py` and the JS client are migrated to the generated symbols. Runtime warnings on both sides catch any literal that slipped through during the migration.

**Tech Stack:** Python 3.12 (`StrEnum`), vanilla JS (frozen `Object` + `Set`), GNU Make, pytest.

**Branch:** `ws-message-types` (already created from `origin/main` + the two spec commits).

---

## Inventory (audit results)

**Server → Client (22):**
`background_event`, `canvas_update`, `chunk`, `command_ack`, `compaction_done`, `confirm_request`, `confirmation_response`, `conv_history`, `conv_selected`, `error`, `message_complete`, `model_changed`, `models_available`, `notification_created`, `notification_read`, `reflection_result`, `tool_end`, `tool_start`, `tool_status`, `turn_complete`, `turn_start`, `user_message`

**Client → Server (8):**
`cancel_turn`, `confirm_response`, `load_history`, `select_conv`, `send`, `set_effort` (deprecated alias for `set_model`), `set_model`, `widget_response`

**Bidirectional:** none.

`http_server.py`'s `tool_confirm_response` and `cancel_turn` are EventBus events, not WS wire messages — out of scope.

---

## Phase 1 — Manifest

### Task 1.1: Create the JSON manifest

**Files:**
- Create: `src/decafclaw/web/message_types.json`

The full manifest content (alphabetical within direction blocks for readability — generator sorts on output regardless):

```json
{
  "$schema_version": 1,
  "_doc_header": "WebSocket message types exchanged between the decafclaw server (`src/decafclaw/web/websocket.py`) and the in-browser client. This page is generated from `src/decafclaw/web/message_types.json` — edit the manifest and run `make gen-message-types` to regenerate.",
  "messages": {
    "background_event": {
      "direction": "server_to_client",
      "description": "Background-task lifecycle event surfaced into a conversation timeline (e.g. delegated task started/finished).",
      "fields": {"conv_id": "string", "event": "object"}
    },
    "canvas_update": {
      "direction": "server_to_client",
      "description": "The conversation's canvas state changed; client should re-render the canvas panel.",
      "fields": {"conv_id": "string", "state": "object"}
    },
    "chunk": {
      "direction": "server_to_client",
      "description": "Streaming text fragment of an in-flight assistant message.",
      "fields": {"conv_id": "string", "text": "string"}
    },
    "command_ack": {
      "direction": "server_to_client",
      "description": "Acknowledgement that a slash-style user command was received and dispatched.",
      "fields": {"conv_id": "string", "command": "string"}
    },
    "compaction_done": {
      "direction": "server_to_client",
      "description": "Conversation history compaction completed; client should reload history.",
      "fields": {"conv_id": "string"}
    },
    "confirm_request": {
      "direction": "server_to_client",
      "description": "Server is asking the user to approve or deny a pending action (tool call, end-of-turn gate, widget input).",
      "fields": {"conv_id": "string", "request_id": "string", "kind": "string", "payload": "object"}
    },
    "confirmation_response": {
      "direction": "server_to_client",
      "description": "Replay of a prior confirmation response, surfaced when reloading conversation history.",
      "fields": {"conv_id": "string", "request_id": "string", "decision": "string"}
    },
    "conv_history": {
      "direction": "server_to_client",
      "description": "Page of historical messages for a conversation.",
      "fields": {"conv_id": "string", "messages": "array of object", "before": "string | null"}
    },
    "conv_selected": {
      "direction": "server_to_client",
      "description": "Confirmation that a select_conv subscribed this socket to the named conversation. May include initial conversation state.",
      "fields": {"conv_id": "string", "model": "string | null"}
    },
    "error": {
      "direction": "server_to_client",
      "description": "Generic error surfaced to the client (bad request, unknown conversation, internal error).",
      "fields": {"message": "string", "conv_id": "string | null"}
    },
    "message_complete": {
      "direction": "server_to_client",
      "description": "Final form of an assistant message after streaming completed (or when replayed from history).",
      "fields": {"conv_id": "string", "message": "object"}
    },
    "model_changed": {
      "direction": "server_to_client",
      "description": "The active model for a conversation changed (echoed back to all subscribers of that conversation).",
      "fields": {"conv_id": "string", "model": "string"}
    },
    "models_available": {
      "direction": "server_to_client",
      "description": "List of model identifiers the user can select in the UI.",
      "fields": {"models": "array of string"}
    },
    "notification_created": {
      "direction": "server_to_client",
      "description": "A new notification was added to the user's inbox (push from notification subsystem).",
      "fields": {"notification": "object"}
    },
    "notification_read": {
      "direction": "server_to_client",
      "description": "A notification was marked read (push from notification subsystem).",
      "fields": {"id": "string"}
    },
    "reflection_result": {
      "direction": "server_to_client",
      "description": "Output of the post-turn reflection step for a conversation.",
      "fields": {"conv_id": "string", "result": "object"}
    },
    "tool_end": {
      "direction": "server_to_client",
      "description": "Final result of a tool call. Replaces the in-flight tool_status with terminal state.",
      "fields": {"conv_id": "string", "tool_call_id": "string", "name": "string", "ok": "boolean", "result": "string | object"}
    },
    "tool_start": {
      "direction": "server_to_client",
      "description": "Tool call has begun execution.",
      "fields": {"conv_id": "string", "tool_call_id": "string", "name": "string", "input": "object"}
    },
    "tool_status": {
      "direction": "server_to_client",
      "description": "Mid-flight progress update from a running tool.",
      "fields": {"conv_id": "string", "tool_call_id": "string", "status": "string"}
    },
    "turn_complete": {
      "direction": "server_to_client",
      "description": "An agent turn finished (success, error, or cancellation).",
      "fields": {"conv_id": "string"}
    },
    "turn_start": {
      "direction": "server_to_client",
      "description": "An agent turn has started; clients should clear any draft and show in-flight UI.",
      "fields": {"conv_id": "string"}
    },
    "user_message": {
      "direction": "server_to_client",
      "description": "Echo of a user-authored message to all subscribers of the conversation (used for multi-tab sync).",
      "fields": {"conv_id": "string", "message": "object"}
    },

    "cancel_turn": {
      "direction": "client_to_server",
      "description": "Request cancellation of the conversation's in-flight agent turn.",
      "fields": {"conv_id": "string"}
    },
    "confirm_response": {
      "direction": "client_to_server",
      "description": "User's decision on a pending confirm_request.",
      "fields": {"conv_id": "string", "request_id": "string", "decision": "string", "extras": "object"}
    },
    "load_history": {
      "direction": "client_to_server",
      "description": "Request a page of historical messages for a conversation.",
      "fields": {"conv_id": "string", "limit": "number", "before": "string | null"}
    },
    "select_conv": {
      "direction": "client_to_server",
      "description": "Subscribe this socket to a conversation's event stream.",
      "fields": {"conv_id": "string"}
    },
    "send": {
      "direction": "client_to_server",
      "description": "Send a user message (and/or attachments) to the conversation.",
      "fields": {"conv_id": "string", "text": "string", "attachments": "array of object"}
    },
    "set_effort": {
      "direction": "client_to_server",
      "description": "Deprecated backward-compat alias for set_model used by older web clients.",
      "fields": {"conv_id": "string", "model": "string"}
    },
    "set_model": {
      "direction": "client_to_server",
      "description": "Change the active model for a conversation.",
      "fields": {"conv_id": "string", "model": "string"}
    },
    "widget_response": {
      "direction": "client_to_server",
      "description": "Submission of an interactive widget input.",
      "fields": {"conv_id": "string", "request_id": "string", "value": "object"}
    }
  }
}
```

- [ ] **Step 1.1.1 — Write the manifest.** Verbatim above.
- [ ] **Step 1.1.2 — Sanity-check.** `python -c "import json; json.load(open('src/decafclaw/web/message_types.json'))"` — must exit 0.
- [ ] **Step 1.1.3 — Coverage diff.** Re-run the audit greps from the spec and assert every literal appears as a manifest key:

  ```bash
  python - <<'PY'
  import json, re, pathlib
  manifest = json.loads(pathlib.Path("src/decafclaw/web/message_types.json").read_text())
  keys = set(manifest["messages"])
  py_lits = set(re.findall(r'"type":\s*"([a-z_][a-z0-9_]*)"', pathlib.Path("src/decafclaw/web/websocket.py").read_text()))
  print("py only:", py_lits - keys)
  print("missing in py (ok if c2s):", keys - py_lits)
  PY
  ```

  Expected: `py only` set is empty. `missing in py` is the c2s-only set (those don't appear in `"type": "..."` outbound literals).

- [ ] **Step 1.1.4 — Commit.**
  ```bash
  git add src/decafclaw/web/message_types.json
  git commit -m "feat(ws): add message-type manifest (#384)"
  ```

---

## Phase 2 — Generator + Make targets + first generated outputs

### Task 2.1: Write the generator script

**Files:**
- Create: `scripts/gen_message_types.py`

- [ ] **Step 2.1.1 — Write the generator.**

```python
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
```

### Task 2.2: Wire Make targets

**Files:**
- Modify: `Makefile`

- [ ] **Step 2.2.1 — Add targets.** Add near the existing `vendor:`/`reindex:` group:

  ```makefile
  gen-message-types:
  	python scripts/gen_message_types.py

  check-message-types:
  	python scripts/gen_message_types.py
  	git diff --exit-code -- src/decafclaw/web/message_types.py src/decafclaw/web/static/lib/message-types.js docs/websocket-messages.md
  ```

- [ ] **Step 2.2.2 — Wire into `check`.** Modify the `check:` recipe to call `check-message-types` before `lint` and `typecheck`. Existing `check:` looks like:
  ```makefile
  check: install-js
  	# existing lint + typecheck commands
  ```
  Update to invoke `check-message-types` as the first step inside `check` (or list it as a prerequisite alongside `install-js`). Inspect the actual `check:` body before editing — preserve all current commands.

### Task 2.3: First generation pass

- [ ] **Step 2.3.1 — Run generator twice and assert idempotence.**
  ```bash
  make gen-message-types
  git status --short  # 3 new files
  make gen-message-types
  git diff --exit-code -- src/decafclaw/web/message_types.py src/decafclaw/web/static/lib/message-types.js docs/websocket-messages.md
  ```
  Expected: second run is a no-op.

- [ ] **Step 2.3.2 — Skim generated outputs.**
  - `src/decafclaw/web/message_types.py` — class `WSMessageType(StrEnum)` with all 30 members; three frozensets.
  - `src/decafclaw/web/static/lib/message-types.js` — frozen object + Set.
  - `docs/websocket-messages.md` — three sections, alphabetical entries.

- [ ] **Step 2.3.3 — Type-check the new Python file.** `make typecheck` — must pass (no errors anywhere in the project).

### Task 2.4: Unit test for handler/JS coverage

**Files:**
- Create: `tests/test_message_types.py`

- [ ] **Step 2.4.1 — Write the test.**

```python
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
    assert js_values == py_values
```

- [ ] **Step 2.4.2 — Run the test.**
  ```bash
  pytest tests/test_message_types.py -v
  ```
  Expected: 2 passing.

### Task 2.5: Commit

- [ ] **Step 2.5.1 — Lint + check-js + full test suite.**
  ```bash
  make lint && make check-js && make typecheck && make test
  ```
  All must pass. Address any failure before committing.

- [ ] **Step 2.5.2 — Commit.**
  ```bash
  git add scripts/gen_message_types.py Makefile \
          src/decafclaw/web/message_types.py \
          src/decafclaw/web/static/lib/message-types.js \
          docs/websocket-messages.md \
          tests/test_message_types.py
  git commit -m "feat(ws): generator + drift check for message types (#384)"
  ```

---

## Phase 3 — Server flip + warning hardening

### Task 3.1: Migrate `web/websocket.py` literals

**Files:**
- Modify: `src/decafclaw/web/websocket.py`

- [ ] **Step 3.1.1 — Add the import.** Near the existing `from decafclaw.web.…` imports at the top of the file:
  ```python
  from decafclaw.web.message_types import WSMessageType
  ```

- [ ] **Step 3.1.2 — Replace outbound literals.** For each occurrence of `"type": "<wire>"` in the file (~40 sites — see grep below), replace with `"type": WSMessageType.<UPPER>`. Use repeat search-and-replace, one wire string at a time, to keep diffs reviewable.

  Search-replace pairs (all server-to-client):
  ```
  "type": "background_event"      → "type": WSMessageType.BACKGROUND_EVENT
  "type": "canvas_update"         → "type": WSMessageType.CANVAS_UPDATE
  "type": "chunk"                 → "type": WSMessageType.CHUNK
  "type": "command_ack"           → "type": WSMessageType.COMMAND_ACK
  "type": "compaction_done"       → "type": WSMessageType.COMPACTION_DONE
  "type": "confirm_request"       → "type": WSMessageType.CONFIRM_REQUEST
  "type": "confirmation_response" → "type": WSMessageType.CONFIRMATION_RESPONSE
  "type": "conv_history"          → "type": WSMessageType.CONV_HISTORY
  "type": "conv_selected"         → "type": WSMessageType.CONV_SELECTED
  "type": "error"                 → "type": WSMessageType.ERROR
  "type": "message_complete"      → "type": WSMessageType.MESSAGE_COMPLETE
  "type": "model_changed"         → "type": WSMessageType.MODEL_CHANGED
  "type": "models_available"      → "type": WSMessageType.MODELS_AVAILABLE
  "type": "notification_created"  → "type": WSMessageType.NOTIFICATION_CREATED
  "type": "notification_read"     → "type": WSMessageType.NOTIFICATION_READ
  "type": "reflection_result"     → "type": WSMessageType.REFLECTION_RESULT
  "type": "tool_end"              → "type": WSMessageType.TOOL_END
  "type": "tool_start"            → "type": WSMessageType.TOOL_START
  "type": "tool_status"           → "type": WSMessageType.TOOL_STATUS
  "type": "turn_complete"         → "type": WSMessageType.TURN_COMPLETE
  "type": "turn_start"            → "type": WSMessageType.TURN_START
  "type": "user_message"          → "type": WSMessageType.USER_MESSAGE
  ```

- [ ] **Step 3.1.3 — Migrate `_HANDLERS` keys.** Convert dict keys to enum members:
  ```python
  _HANDLERS = {
      WSMessageType.SELECT_CONV: _handle_select_conv,
      WSMessageType.LOAD_HISTORY: _handle_load_history,
      WSMessageType.SEND: _handle_send,
      WSMessageType.CANCEL_TURN: _handle_cancel_turn,
      WSMessageType.SET_EFFORT: _handle_set_model,  # backward compat for old web UI
      WSMessageType.SET_MODEL: _handle_set_model,
      WSMessageType.CONFIRM_RESPONSE: _handle_confirm_response,
      WSMessageType.WIDGET_RESPONSE: _handle_widget_response,
  }
  ```
  String dispatch (`_HANDLERS.get(msg.get("type", ""))`) keeps working — `StrEnum` keys hash equal to their underlying string.

- [ ] **Step 3.1.4 — Verify zero remaining `"type": "<wire>"` literals.**
  ```bash
  grep -nE '"type":\s*"[a-z_][a-z0-9_]*"' src/decafclaw/web/websocket.py
  ```
  Expected output: empty. (The error-message string `"Unknown message type: ..."` does not match this regex.)

### Task 3.2: Server-side warning on inbound unknown types

**Files:**
- Modify: `src/decafclaw/web/websocket.py` around line 785–790

- [ ] **Step 3.2.1 — Add `log.warning`.** Locate the inbound dispatch:
  ```python
  msg_type = msg.get("type", "")
  handler = _HANDLERS.get(msg_type)
  if handler:
      await handler(ws_send, index, username, msg, state)
  else:
      await ws_send({"type": WSMessageType.ERROR, "message": f"Unknown message type: {msg_type}"})
  ```
  Add a `log.warning(...)` line in the `else` branch *before* the `ws_send`:
  ```python
  else:
      log.warning("ws: unknown inbound message type from %s: %r", username, msg_type)
      await ws_send({"type": WSMessageType.ERROR, "message": f"Unknown message type: {msg_type}"})
  ```

### Task 3.3: Verify and commit

- [ ] **Step 3.3.1 — Lint + typecheck + tests.**
  ```bash
  make lint && make typecheck && make test
  ```
  All must pass.

- [ ] **Step 3.3.2 — Commit.**
  ```bash
  git add src/decafclaw/web/websocket.py
  git commit -m "refactor(ws): server uses WSMessageType enum (#384)"
  ```

---

## Phase 4 — Client flip + warning

### Task 4.1: Replace dispatch sites

**Files:**
- Modify: `src/decafclaw/web/static/lib/message-store.js`
- Modify: `src/decafclaw/web/static/lib/conversation-store.js`
- Modify: `src/decafclaw/web/static/lib/tool-status-store.js`
- Modify: `src/decafclaw/web/static/app.js`
- Modify: `src/decafclaw/web/static/canvas-page.js`

- [ ] **Step 4.1.1 — Add imports.**

  Top of each file (existing imports). Use the relative path appropriate to the file:
  - `lib/*.js` files → `import { MESSAGE_TYPES } from './message-types.js';`
  - `app.js`, `canvas-page.js` → `import { MESSAGE_TYPES, KNOWN_MESSAGE_TYPES } from './lib/message-types.js';`
  - In `app.js` we'll also use `KNOWN_MESSAGE_TYPES` for the warning. Other files don't need the Set.

- [ ] **Step 4.1.2 — `lib/message-store.js`.** Replace the `case '<wire>':` lines (around 135–230) with `case MESSAGE_TYPES.<UPPER>:`. Wire strings used: `conv_history`, `chunk`, `message_complete`, `user_message`, `command_ack`, `compaction_done`, `background_event`.

- [ ] **Step 4.1.3 — `lib/conversation-store.js`.** Replace:
  - Two `msg.type === 'conv_history'` and `msg.type === 'message_complete'` checks (~lines 534, 551) with `msg.type === MESSAGE_TYPES.CONV_HISTORY` / `MESSAGE_TYPES.MESSAGE_COMPLETE`.
  - `case 'conv_selected':` / `'turn_start'` / `'model_changed'` / `'models_available'` / `'error'` (~lines 571–602) with the enum form.
  - Outbound `ws.send({ type: 'select_conv', ... })` (line 430) with `MESSAGE_TYPES.SELECT_CONV`. Same for `'load_history'` (lines 431, 504), `'set_model'` (490), `'cancel_turn'` (497).

- [ ] **Step 4.1.4 — `lib/tool-status-store.js`.** Replace:
  - `case 'tool_start'` / `'tool_status'` / `'tool_end'` / `'confirm_request'` / `'confirmation_response'` / `'reflection_result'` (lines 130–227) with the enum form.
  - Outbound `ws.send({ type: 'confirm_response', ... })` (line 71) and `'widget_response'` (line 109) with the enum form.

- [ ] **Step 4.1.5 — `app.js`.** Replace the four `msg?.type === 'error' | 'turn_complete' | 'notification_created' | 'notification_read' | 'canvas_update'` checks (around lines 416–435).

- [ ] **Step 4.1.6 — `canvas-page.js`.** Replace `ws.send(JSON.stringify({ type: 'select_conv', conv_id: convId }))` (line 74) with `MESSAGE_TYPES.SELECT_CONV`.

- [ ] **Step 4.1.7 — Verify no remaining bare wire literals.**
  ```bash
  grep -rnE "(\\.type === ['\"][a-z_]+['\"]|case ['\"][a-z_]+['\"])" \
    src/decafclaw/web/static/app.js \
    src/decafclaw/web/static/canvas-page.js \
    src/decafclaw/web/static/lib/conversation-store.js \
    src/decafclaw/web/static/lib/message-store.js \
    src/decafclaw/web/static/lib/tool-status-store.js
  ```
  Expected: empty (every remaining `.type ===` and `case` should reference `MESSAGE_TYPES.X`).

### Task 4.2: Client-side warning on inbound unknown types

**Files:**
- Modify: `src/decafclaw/web/static/app.js`

- [ ] **Step 4.2.1 — Locate the central WS message router.** Look in `app.js` for the `ws.addEventListener('message', ...)` or equivalent dispatcher. Add at the top of that handler, after JSON parse:
  ```js
  if (msg && typeof msg.type === 'string' && !KNOWN_MESSAGE_TYPES.has(msg.type)) {
    console.warn('[ws] unknown message type from server:', msg.type, msg);
  }
  ```
  Place the warning *before* the existing per-type dispatch, so it fires regardless of whether any branch handles the message. The warning is informational — do not return/skip on it.

  If the central dispatch in `app.js` only sees a subset (because stores subscribe directly), put the warning at the WS-client subscribe site that sees every message. The grep that follows confirms.

- [ ] **Step 4.2.2 — Verify the warning gets every message.** Skim `app.js`'s WS setup. If it forwards messages to multiple stores via separate listeners, the warning must hook in at the single point all messages flow through. Otherwise add a top-level forwarder.

### Task 4.3: Verify and commit

- [ ] **Step 4.3.1 — `make check-js`.** Must pass with no new errors.

- [ ] **Step 4.3.2 — `make check && make test`.** All green.

- [ ] **Step 4.3.3 — Manual smoke test (web UI).** Les will need to confirm `make dev` is OK to bounce, or just describe the steps. If we can't restart the running dev server, we describe the smoke-test steps in the PR body and ask Les to verify locally before merge.

  Smoke checklist (when run): open the web UI, send a chat message that triggers a tool call, change models mid-conversation, cancel a turn, trigger a compaction, watch the browser console for any `[ws] unknown message type` warnings. None expected.

- [ ] **Step 4.3.4 — Commit.**
  ```bash
  git add src/decafclaw/web/static/lib/message-store.js \
          src/decafclaw/web/static/lib/conversation-store.js \
          src/decafclaw/web/static/lib/tool-status-store.js \
          src/decafclaw/web/static/app.js \
          src/decafclaw/web/static/canvas-page.js
  git commit -m "refactor(ws): client uses MESSAGE_TYPES constants (#384)"
  ```

---

## Phase 5 — Docs index + CLAUDE.md note

### Task 5.1: Link the generated docs page

**Files:**
- Modify: `docs/index.md`

- [ ] **Step 5.1.1 — Add a link.** Locate the section that lists web-UI / transport docs (read the file first to find the right anchor). Add a bullet:
  ```markdown
  - [WebSocket message types](websocket-messages.md) — generated wire-protocol reference.
  ```

### Task 5.2: CLAUDE.md note

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 5.2.1 — Add a one-liner.** In the "Web-UI" / "Mattermost-specific" / "Conventions" section (read the file first), add a single bullet pointing future contributors at the manifest as the place to add new types:
  ```markdown
  - **WebSocket message types** are centralized — add new wire types in `src/decafclaw/web/message_types.json` and run `make gen-message-types`. The generated `WSMessageType` enum / `MESSAGE_TYPES` JS object are the only call-site references.
  ```

### Task 5.3: Verify and commit

- [ ] **Step 5.3.1 — `make check && make test`.** Must pass.

- [ ] **Step 5.3.2 — Commit.**
  ```bash
  git add docs/index.md CLAUDE.md
  git commit -m "docs(ws): link generated reference + note manifest in CLAUDE.md (#384)"
  ```

---

## Phase 6 — PR

- [ ] **Step 6.1 — Final pre-PR checks.**
  ```bash
  make check && make test
  git log --oneline origin/main..HEAD
  ```
  Expect 5 implementation commits + 2 spec commits = 7 total.

- [ ] **Step 6.2 — Push the branch.**
  ```bash
  git push -u origin ws-message-types
  ```

- [ ] **Step 6.3 — Open the PR.** Title: `Centralize WebSocket message types (#384)`. Body should:
  - Link to issue #384.
  - Summarize the manifest → generator → drift-check architecture.
  - List the call-site flips (server-side count, client-side files).
  - Call out the smoke-test steps (the manual checklist from Step 4.3.3) for Les to verify.
  - Note the `set_effort` deprecated alias is preserved (no client-facing change).
  - Note the deferred future direction (stricter field schema).

- [ ] **Step 6.4 — Add Copilot reviewer.**
  ```bash
  gh pr edit <PR#> --add-reviewer copilot-pull-request-reviewer
  ```
  Verify via:
  ```bash
  gh api repos/lmorchard/decafclaw/pulls/<PR#>/requested_reviewers
  ```

---

## Spec Coverage Self-Review

- ✅ Manifest source of truth (`message_types.json`) — Phase 1.
- ✅ `StrEnum` Python output — Phase 2.1 (`render_python`).
- ✅ Frozen-object JS output + `KNOWN_MESSAGE_TYPES` Set — Phase 2.1 (`render_js`).
- ✅ Generated docs page with three sections + future-direction callout — Phase 2.1 (`render_doc`).
- ✅ Make targets + drift check wired into `make check` — Phase 2.2.
- ✅ Server-side migration of all literals + `_HANDLERS` keys — Phase 3.1.
- ✅ Server-side `log.warning` on inbound unknown — Phase 3.2.
- ✅ Client-side dispatch + outbound migration — Phase 4.1.
- ✅ Client-side `console.warn` on inbound unknown — Phase 4.2.
- ✅ Unit tests (handler coverage + JS↔Python value match) — Phase 2.4.
- ✅ Drift CI test — Phase 2.5 via `make check`.
- ✅ Docs index link + CLAUDE.md note — Phase 5.
- ✅ Manual smoke test — Phase 4.3 (called out in PR body).
- ✅ `http_server.py` correctly excluded — `_HANDLERS` migration in Phase 3.1 covers all server-side wire sites; `http_server.py` is documented as out of scope.
