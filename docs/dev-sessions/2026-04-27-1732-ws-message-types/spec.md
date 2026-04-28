# Spec — Centralize WebSocket message types

Closes [#384](https://github.com/lmorchard/decafclaw/issues/384).

## Background

WebSocket message-type strings (`"chunk"`, `"tool_end"`, `"select_conv"`, …) are scattered as ~50 string literals across `src/decafclaw/web/websocket.py`, `src/decafclaw/http_server.py`, and the JS client (`web/static/lib/*.js`, `web/static/app.js`, `web/static/canvas-page.js`). Risks:

- Typos don't fail at compile time on either side.
- Renames require grepping two languages.
- New types ship without a place that documents the wire format.

The bug class to prevent: a missed call site keeps using the old literal, so messages start dropping silently.

## Goals

1. Single source of truth for every WebSocket message type, owned by a hand-edited JSON manifest.
2. Python and JS get generated, drift-checked enum / constants files. CI fails on drift.
3. Pyright/eslint catch typos before runtime; runtime warnings catch drift during the migration.
4. Wire format is documented in a single generated doc page, kept in lockstep with the manifest.

## Non-goals

- Centralizing internal `EventBus` event-type strings (`"confirmation_request"`, `"tool_status"`, etc.). Different domain (in-process pub/sub), different consumers, different lifecycle.
- Centralizing archive-message `role` values.
- Centralizing Mattermost-side message types.
- Strict per-field runtime validation. Field types in the manifest are human-readable sketches, not validators. **Future direction:** if we ever want runtime validation, the manifest schema can grow into typed entries (`{"type": "string", "optional": true, "enum": [...]}`, `{"type": "array", "items": {...}}`, etc.). Out of scope here.

## Design

### Manifest (`src/decafclaw/web/message_types.json`)

Hand-edited, source of truth. Top-level shape:

```json
{
  "$schema_version": 1,
  "_doc_header": "Prose intro that gets lifted into the top of docs/websocket-messages.md.",
  "messages": {
    "tool_end": {
      "description": "Final result of a tool call. Replaces the in-flight tool_status with terminal state.",
      "direction": "server_to_client",
      "fields": {
        "conv_id": "string",
        "tool_call_id": "string",
        "name": "string",
        "ok": "boolean",
        "result": "string | object"
      }
    },
    "select_conv": {
      "description": "Subscribe this socket to a conversation's event stream.",
      "direction": "client_to_server",
      "fields": { "conv_id": "string" }
    }
  }
}
```

Rules:

- Keys match `[a-z_][a-z0-9_]*`. Generator validates.
- `direction` ∈ `server_to_client` | `client_to_server` | `bidirectional`. We don't enforce direction at runtime, but it's used for doc grouping and generated per-direction frozensets in case future work wants it.
- `fields` is a flat dict from field name to a human-readable type sketch (`"string"`, `"boolean"`, `"string | object"`, `"array of string"`). Not a validator — see future direction above.
- Manifest entry order is **not** significant. The generator sorts deterministically (by direction, then alphabetical) on the way to every output, so contributors can append new entries wherever's convenient.
- `$schema_version` is reserved for future format evolution. Generator currently asserts it equals `1` and otherwise ignores it.

### Generator (`scripts/gen_message_types.py`)

- Single Python script, stdlib only, ~80 lines.
- Reads the manifest, validates shape, emits three files. Each output starts with a `# DO NOT EDIT — regenerate via 'make gen-message-types'` header naming the manifest path.
- Output must be byte-stable across runs (deterministic sort, fixed indentation, trailing newline). The drift check depends on this.

Generated outputs:

1. **`src/decafclaw/web/message_types.py`**
   ```python
   from enum import StrEnum

   class WSMessageType(StrEnum):
       CHUNK = "chunk"
       TOOL_END = "tool_end"
       # ...

   KNOWN_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset(WSMessageType)
   S2C_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset({WSMessageType.CHUNK, ...})
   C2S_MESSAGE_TYPES: frozenset[WSMessageType] = frozenset({WSMessageType.SELECT_CONV, ...})
   ```
   Member names are `SCREAMING_SNAKE` of the wire string.

2. **`src/decafclaw/web/static/lib/message-types.js`**
   ```js
   export const MESSAGE_TYPES = Object.freeze({
     CHUNK: 'chunk',
     TOOL_END: 'tool_end',
     // ...
   });
   export const KNOWN_MESSAGE_TYPES = new Set(Object.values(MESSAGE_TYPES));
   ```

3. **`docs/websocket-messages.md`** — see "Generated docs" below.

### Make targets

- `make gen-message-types` — runs the generator.
- `make check-message-types` — runs the generator, then `git diff --exit-code -- <three output paths>`. Fails if any output is stale.
- `check-message-types` is wired into the existing `make check` (between `lint` and `typecheck`), so CI catches drift.

### Generated docs (`docs/websocket-messages.md`)

- Header note: "This page is generated. Edit `src/decafclaw/web/message_types.json` and run `make gen-message-types`."
- Lifts `_doc_header` prose from the manifest.
- "Future direction" callout: stricter field schema as a deferred enhancement (so we don't re-debate it).
- Three sections — **Server → Client**, **Client → Server**, **Bidirectional** — alphabetical within each.
- Per type: literal in code voice, description prose, fields as a definition list.
- Linked from `docs/index.md` under the web-UI / transport section.

### Runtime safety

- **Server inbound unknown** — `web/websocket.py` already returns `{"type": "error", "message": "Unknown message type: ..."}` to the client. Harden by also `log.warning(...)`-ing it on the server, so it shows in our logs.
- **Client inbound unknown** — add to the central JS dispatcher (in `app.js`'s router) a single `if (!KNOWN_MESSAGE_TYPES.has(msg.type)) console.warn('[ws] unknown message type from server:', msg.type, msg);`.
- **Outbound validation** — *not* added. Per design discussion, the enum + lint/pyright catches typos before runtime; outbound runtime validation is overkill. Direction frozensets are exported in case a future change wants to enforce.

### Migration of call sites

**Server side** — `web/websocket.py` + `http_server.py`:

- `from decafclaw.web.message_types import WSMessageType`.
- Every `"type": "..."` literal becomes `"type": WSMessageType.X`. Works because `StrEnum` is a `str` subclass.
- `_HANDLERS` dict keys become `WSMessageType.X`. Inbound dispatch keeps the existing `msg.get("type", "")` pattern (string lookup against StrEnum keys hashes correctly).

**Client side** — `web/static/`:

- Every dispatch site (`case 'tool_end':` in `lib/conversation-store.js`, `lib/message-store.js`, `lib/tool-status-store.js`, `app.js`) becomes `case MESSAGE_TYPES.TOOL_END:`.
- Every outbound `ws.send({ type: 'select_conv', ... })` (in `conversation-store.js` and `canvas-page.js`) becomes `ws.send({ type: MESSAGE_TYPES.SELECT_CONV, ... })`.
- Add the inbound-unknown warning at the central dispatch in `app.js`.

### Testing

- **Drift test** — `make check` now runs `check-message-types`. CI catches manifest-vs-generated divergence.
- **Unit test** (`tests/test_message_types.py`):
  - Every key in `_HANDLERS` is a `WSMessageType` member.
  - The generated JS file's `MESSAGE_TYPES` values match the Python enum's values exactly. Python-side parses the JS file with a small regex over the `MESSAGE_TYPES = Object.freeze({...})` block — no Node dependency.
- **No new behavioral tests.** This is a pure rename; existing WS tests cover wire behavior.
- **Manual verification** — `make dev`, exercise chat / tool confirmation / model change / cancel turn / canvas update in the web UI, watch console for `[ws] unknown message type` warnings.

## Commit slicing (single PR)

1. **Manifest + audit** — add `web/message_types.json` populated from a complete grep of every `"type": "..."` literal on both sides. Data only; no imports yet.
2. **Generator + Make targets + first generated outputs** — `scripts/gen_message_types.py`, `make gen-message-types`, `make check-message-types`, wired into `make check`. First run emits `web/message_types.py`, `web/static/lib/message-types.js`, `docs/websocket-messages.md`. Nothing imports the generated files yet.
3. **Server flip + server-side warning hardening** — migrate `web/websocket.py` and the two sites in `http_server.py` to `WSMessageType.X`. Add `log.warning(...)` for inbound unknown types.
4. **Client flip + client-side warning** — migrate dispatch and outbound sites in `lib/conversation-store.js`, `lib/message-store.js`, `lib/tool-status-store.js`, `app.js`, `canvas-page.js`. Add the `console.warn` for unknown inbound types.
5. **Docs index + CLAUDE.md note** — link `docs/websocket-messages.md` from `docs/index.md`, add a one-line bullet to CLAUDE.md's web-UI section pointing future contributors at the manifest.

## Risk

- **Wire-protocol churn between server and client.** Mitigated by: (a) the literals don't change, only how they're written in code; (b) drift CI; (c) runtime warnings on both sides during migration. If a literal is missed, the unknown-type warning fires loudly.
- **`StrEnum` serialization.** `json.dumps(WSMessageType.CHUNK)` produces `"chunk"` — verified semantics. No custom encoder needed.
- **Generator non-determinism.** Mitigated by deterministic sort + golden test (the drift check itself).

## Out of scope (filed as follow-ups if needed)

- Stricter manifest field schema (typed entries with optional/enum/array).
- Centralizing internal `EventBus` event types.
- Centralizing archive-message `role` values.
- Centralizing Mattermost message types.
