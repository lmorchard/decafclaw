# Research: Widget — text_input

Documentarian findings on the existing widget infrastructure. Dense file:line refs over prose.

## 1. Widget catalog and registration

**On-disk layout** — bundled widgets at `src/decafclaw/web/static/widgets/{name}/widget.json` + `widget.js`. Existing widgets:

| Widget | Purpose | accepts_input | modes |
|---|---|---|---|
| `data_table` | Sortable scrollable table | no | inline, canvas |
| `multiple_choice` | Radio/checkbox; pauses agent | **yes** | inline |
| `markdown_document` | Rendered markdown; canvas-capable | no | inline, canvas |
| `code_block` | Syntax-highlighted code; canvas-capable | no | inline, canvas |
| `iframe_sandbox` | Agent-authored HTML in CSP-locked sandbox | no | inline, canvas |

**Python side** — `src/decafclaw/widgets.py`:
- `_META_SCHEMA` (lines 23–37) — validates `widget.json`: `name`, `description`, `modes` (≥1 of `inline`/`canvas`), optional `accepts_input` (bool), `data_schema` (JSON Schema).
- `WidgetDescriptor` dataclass (lines 165–177): `name`, `tier`, `description`, `modes`, `accepts_input`, `data_schema`, `js_path`, `tier_root`, `mtime`.
- `WidgetRegistry` (lines 56–126): `.get`, `.list`, `.tier`, `.validate(name, data)`, `.normalize(name, data)`. `validate` runs `jsonschema` against widget's `data_schema` (lines 104–126).
- `_scan_tier()` (lines 128–177), `load_widget_registry(config)` (lines 180–202): scans `_BUNDLED_DIR` (line 20: `Path(__file__).parent / "web" / "static" / "widgets"`) then `config.agent_path / "widgets"` (admin tier overrides bundled on collision, line 196).
- `init_widgets(config)` (lines 214–218): startup singleton.

**HTTP endpoints** — `src/decafclaw/http_server.py`:
- `GET /api/widgets` (lines 1570–1588) — catalog with cache-busted `js_url` (mtime query param).
- `GET /widgets/{tier}/{name}/widget.js` (lines 1591–1620) — serves widget JS with path/symlink validation.

**No workspace tier yet** — agent-writable widget JS would be privilege escalation (#358).

## 2. Widget protocol / schema

**Tool returns `WidgetRequest`** — `src/decafclaw/media.py:37–51`:
```
WidgetRequest(widget_type, data, target="inline"|"canvas", on_response=callable, response_message=...)
```
Attached to `ToolResult.widget` alongside `text` (LLM-visible) and `end_turn` (lines 69–83).

**Agent validates & resolves** — `src/decafclaw/agent.py:652–738` `_resolve_widget()`:
- `registry.validate(widget_type, data)` (line 679); strips widget on fail (lines 681–684).
- Target check: in `("inline", "canvas")` and in `descriptor.modes` (lines 687–700).
- `registry.normalize(widget_type, data)` (lines 704–705) — idempotent, regenerates derived fields.
- For `accepts_input=true`: registers `on_response` callback in `pending_callbacks[tool_call_id]` (lines 729–731), promotes `end_turn` to `WidgetInputPause(tool_call_id, widget_payload)` (lines 733–736).
- Rule: input widget must have `end_turn=True` (or `EndTurnConfirm`, downgraded). Else widget stripped (lines 716–722).

**Archive & dispatch** — payload `{widget_type, target, data}` archived in tool message; emitted via WebSocket (line 618).

**Frontend rendering**:
- `lib/widget-catalog.js:24–44` `getCatalog()` — fetches `/api/widgets` once, memoizes.
- `components/widgets/widget-host.js:89–126` `<dc-widget-host>` — dynamic-imports `desc.js_url`, creates `<dc-widget-{type}>`, sets props `.data`, `.submitted`, `.response`, `.mode`.
- All widgets use light DOM (`createRenderRoot() { return this; }`, line 24) so Pico CSS applies naturally.
- Widgets dispatch `widget-response` CustomEvent on submit (bubbles + composed).

## 3. `multiple_choice` end-to-end (the canonical input widget)

**Schema** — `src/decafclaw/web/static/widgets/multiple_choice/widget.json`:
- `prompt` (required), `options` (array of `{value, label, description?}`), optional `allow_multiple`.

**Tool** — `src/decafclaw/tools/core.py:195–225` `tool_ask_user(ctx, prompt, options, allow_multiple=False)`:
- Normalizes options (line 202; helper at lines 138–165) — bare strings/dicts → `{value, label, description?}`.
- Builds default `on_response` (lines 215–216; helper at lines 168–192) returning string `"User selected: <label>"` (line 190) or comma-joined for multiple (line 186).
- Returns `ToolResult(text="[awaiting user response: ...]", widget=WidgetRequest(...), end_turn=True)` (lines 220–225).

**Render** — `multiple_choice/widget.js`:
- Lit component, props `data`, `submitted`, `response`.
- Render (lines 124–147): prompt + options (radios if `!allow_multiple`, checkboxes else, lines 99–100). Submit button disabled until selection (line 141–142).
- Winner styling on `submitted + selected` (CSS lines 190–206).
- On submit (lines 79–92): `widget-response` CustomEvent, `detail: {selected: string | string[]}`.

**Response data shape**: `{selected: string | string[]}` — keyed string per `value`.

## 4. `WidgetInputPause` & input flow

**Pause initiation** — `agent.py:712–736` (covered in §2 above).

**Pause detection** — `agent.py:1156–1171` after `_execute_tool_calls()`:
- `isinstance(end_turn_signal, WidgetInputPause)` → `_handle_widget_input_pause(ctx, signal)` returns inject string or None.
- If string: append synthetic `{"role": "user", "source": "widget_response", "content": inject}` to history+archive (lines 1163–1170), `_Continue()` loop.
- If None (cancelled): set `end_turn_signal = True`, fall through to final no-tools LLM call.

**Pause mechanics** — `agent.py:213–310` `_handle_widget_input_pause`:
1. Build `ConfirmationRequest(action_type=WIDGET_RESPONSE, action_data=widget_payload, tool_call_id=signal.tool_call_id, timeout=None)` (lines 241–246).
2. Race `ctx.request_confirmation(request)` against `cancel_event` (lines 257–292).
3. Pop callback from `pending_callbacks[tool_call_id]` (line 297). Call `callback(response.data)` (line 304); fallback `default_inject_message(response.data)` (line 310; `widget_input.py:46`).

**Persistence & recovery**:
- Archived as `role: "confirmation_request"` / `"confirmation_response"` (`confirmations.py:40–97`).
- On restart: `ConversationManager.recover_confirmation()` → `WidgetResponseHandler.on_approve()` (`widget_input.py:49–102`) writes synthetic user message directly to archive (lines 96–100). Falls back to default message if in-memory callback gone (lines 81–91).

**What the LLM sees** — the inject string the callback returns. For `multiple_choice`: `"User selected: <label>"` or `"User selected: label1, label2, ..."`.

## 5. Web UI form/input components

**Foundation** — Pico CSS v2 + custom `--pico-*` variables in `styles/variables.css`. All widgets use light DOM so Pico applies.

**Existing input components**:
| Component | File | Notes |
|---|---|---|
| `chat-input` | `web/static/components/chat-input.js` | Textarea + attachments. Auto-resize (lines 71–75). File picker (129–131). Drag-drop (134–150). Dispatches `send` event (60–64). |
| `multiple_choice` widget | `widgets/multiple_choice/widget.js` | Radios/checkboxes in 2-col grid (CSS `widgets.css:156–188`). |

**Styling conventions**:
- BEM-ish: `.widget-<name>__<part>--<state>` (e.g., `.widget-multiple-choice__option--winner`).
- Pico var fallbacks: `var(--pico-muted-border-color, #ccc)` (widgets.css:64).
- Compact submit button pattern: `padding: 0.35rem 0.9rem; font-size: 0.85rem` (widgets.css:212–215).
- `--vh` tracking for mobile soft keyboard (variables.css:9–16).
- No shared form-primitive library — each widget defines own markup. Validation is server-side via `jsonschema`.

## Pico v2 gotcha

From memory: Pico v2 re-scopes `--pico-color`/`--pico-background-color` inside `<button>`. For text/textarea inside the widget, Pico defaults are fine; for any custom button styling, tag-qualify rules (`button.foo`, not `.foo`).
