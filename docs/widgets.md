# Widgets

Tools can return rich UI widgets alongside their text output. The web UI
renders the widget inline inside the tool-result message; Mattermost and
the interactive terminal keep rendering the text as before.

Phase 1 ships a single bundled widget, `data_table`, plus the plumbing
for tools and admins to add more.

## Quick tour

A tool opts into rich rendering by attaching a `WidgetRequest` to its
`ToolResult`:

```python
from decafclaw.media import ToolResult, WidgetRequest

async def my_tool(ctx, query: str):
    rows = [{"page": "Alpha", "score": 0.91}, {"page": "Beta", "score": 0.60}]
    return ToolResult(
        text="Found 2 matches",               # LLM / Mattermost / terminal still see this
        display_short_text="2 matches",
        widget=WidgetRequest(
            widget_type="data_table",
            data={
                "caption": f'results for "{query}"',
                "columns": [
                    {"key": "page",  "label": "Page"},
                    {"key": "score", "label": "Score"},
                ],
                "rows": rows,
            },
        ),
    )
```

The `text` field is the contract with the LLM and the non-web channels.
The `widget` field is purely display — never visible to the model, the
reflection judge, or any non-web transport.

Before the widget reaches the browser, the registry validates its
payload against the widget's `data_schema`. On validation failure the
widget is stripped (warning logged), and the tool result still renders
normally as text.

## Bundled widgets

Phase 1:

- **`data_table`** — sortable columns, scrollable overflow, optional
  caption. Data shape: `{columns: [{key, label}], rows: [{...}], caption?}`.

## Adding a new widget

Widgets are extensible by admins without changing DecafClaw's core. Each
widget lives in its own directory with two files:

```
my_widget/
  widget.json   # descriptor
  widget.js     # Lit component registering <dc-widget-my-widget>
```

### Directory locations (scanned at startup, in order)

1. **Bundled** — `src/decafclaw/web/static/widgets/` (shipped with the app)
2. **Admin** — `data/{agent_id}/widgets/` (outside the agent's write path)

Admin widgets override bundled widgets with the same `name`.

Note: there is **no workspace-tier catalog** (`workspace/widgets/`) yet.
Agent-writable widget JS would be a privilege-escalation surface; that
capability is tracked separately in #358.

### `widget.json`

```json
{
  "name": "my_widget",
  "description": "Short summary of what the widget shows.",
  "modes": ["inline"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["value"],
    "properties": {
      "value": { "type": "string" }
    }
  }
}
```

`data_schema` is a JSON Schema fragment. The registry validates each
tool's `WidgetRequest.data` against it before sending to the frontend.

### `widget.js`

A Lit component. The registered element name is always
`dc-widget-<name>` with underscores in `<name>` converted to hyphens.

```js
import { LitElement, html } from 'lit';

export class MyWidget extends LitElement {
  static properties = {
    data: { type: Object },
  };

  createRenderRoot() { return this; }  // light DOM so app CSS applies

  render() {
    return html`<div class="my-widget">${this.data?.value}</div>`;
  }
}

customElements.define('dc-widget-my-widget', MyWidget);
```

The browser dynamic-imports this module the first time the widget is
needed. Use bare specifiers like `'lit'` — they resolve through the
existing importmap.

## Input widgets (Phase 2)

Some widgets collect structured input from the user. They pause the
agent turn until the user submits, then resume with the selection
injected as a synthetic user message so the next LLM iteration sees
the answer.

To mark a widget as interactive, set `"accepts_input": true` in its
`widget.json`. Tools that emit an input widget MUST also set
`end_turn=True` on the `ToolResult`.

### The `ask_user` core tool

The agent can ask the user to pick from a list via the built-in
`ask_user` tool:

```python
# Inside the LLM's tool-calling loop
await ask_user(
    prompt="Which deploy target?",
    options=["production", "staging", "dev"],
    allow_multiple=False,  # radios; set True for checkboxes
)
```

The user sees a `multiple_choice` widget inline in the tool message;
the agent pauses. When the user submits, the conversation continues
with a synthetic `role: "user"` message like
`"User selected: production"`.

### Building your own input widget

Tool-side:

```python
from decafclaw.media import ToolResult, WidgetRequest

def on_response(data: dict) -> str:
    # data is {selected: ...} (or whatever the widget sends)
    return f"The user answered: {data['selected']}"

async def my_input_tool(ctx, ...):
    return ToolResult(
        text="[awaiting user response]",
        widget=WidgetRequest(
            widget_type="my_prompt",
            data={"prompt": "pick one", "options": [...]},
            on_response=on_response,
        ),
        end_turn=True,  # REQUIRED for input widgets
    )
```

Widget-side, the Lit component gets three props:

- `data` — the widget's data payload.
- `submitted` — `true` once the user has submitted (live or reloaded).
- `response` — the response payload after submit; seed your UI to show
  the selection.

When the user submits, dispatch a `widget-response` CustomEvent that
bubbles past `dc-widget-host`:

```js
this.dispatchEvent(new CustomEvent('widget-response', {
  detail: { selected: chosenValue },
  bubbles: true,
  composed: true,
}));
```

The response payload (whatever goes in `detail`) reaches the tool's
`on_response` callback as its single argument. The callback's return
string is what gets injected as the synthetic user message.

### Restart recovery

Pending input widgets survive server restart. If the server dies
between the user seeing the widget and submitting it, the next
startup scan picks up the pending confirmation from the archive. When
the user submits after restart, the in-memory `on_response` callback
is gone, so a default handler injects `"User responded with: <data>"`
as the user message so the loop stays coherent.

Input widgets ride on the existing confirmation infra in
`src/decafclaw/confirmations.py`: a pause is a `WIDGET_RESPONSE`-typed
confirmation request + response in the JSONL archive.

## Dev workflow

`make dev` watches the bundled widgets directory for `.json` / `.js`
edits and restarts the backend so the new descriptor is picked up. Then
reload the browser — the cache-busting query param
(`?v={mtime}`) ensures the fresh `widget.js` is fetched.

Admin-tier widgets under `data/{agent_id}/widgets/` are not auto-watched
— edit or add one and restart `make dev` manually.

## Phase 3 — Canvas panel and `markdown_document`

Phase 3 ships the canvas panel UI surface and the first canvas-aware
widget, `markdown_document`. See [Canvas panel](web-ui.md#canvas-panel)
for the full UI description.

### The `target` / mode contract

A widget descriptor's `modes` field enumerates the rendering contexts it
supports: `"inline"` (inside a tool-result message bubble) and/or
`"canvas"` (in the detached canvas panel). The host (`<dc-widget-host>`)
sets `el.mode = 'inline'` or `'canvas'` on the mounted widget element.
Widgets inspect `this.mode` and render accordingly — e.g. truncated vs
full layout.

### `markdown_document` widget

Bundled at `src/decafclaw/web/static/widgets/markdown_document/`.
Supports both `"inline"` and `"canvas"` modes.

**Inline mode:** content collapsed via `max-height: 8rem` with a fade
gradient at the bottom. Two buttons appear below the fade:

- **Expand** — toggles full inline render (removes `max-height` cap).
- **Open in Canvas** — POSTs to `/api/canvas/{conv_id}/set` to push
  the widget to the canvas panel. The panel opens (or updates) on the
  right side of the layout.

**Canvas mode:** full content rendered with no truncation. Scroll
position is preserved across `canvas_update` events (clamped to current
scrollable extent so it doesn't leave the viewport).

**Data shape:** `{ content: string }` (raw markdown string).

### Canvas tools (always-loaded)

Four canvas tools are always-loaded so the agent can drive the panel
without activating a skill. See
[Context Composer](context-composer.md#canvas-tools) for descriptions.

- `canvas_set(widget_type, data, label?)` — push a widget to the canvas
- `canvas_update(data)` — replace data on the current widget in place
- `canvas_clear()` — remove the canvas widget and hide the panel
- `canvas_read()` — return current canvas state or null

## Out-of-scope

- Additional widget types: `code_block` — later phases.
- Collapsing `EndTurnConfirm` into a widget with a Mattermost-buttons
  adapter — filed as a follow-up issue.
- Agent-authored widget JS (workspace tier + iframe sandbox) — #358.
- Hot-reload of widget catalog without server restart — future.

## Related files

- `src/decafclaw/widgets.py` — registry scan + validation
- `src/decafclaw/media.py` — `WidgetRequest`, `WidgetInputPause`, `ToolResult.widget`
- `src/decafclaw/widget_input.py` — input-widget handler + callback map
- `src/decafclaw/tools/core.py` — `ask_user` tool
- `src/decafclaw/canvas.py` — canvas state operations
- `src/decafclaw/tools/canvas_tools.py` — canvas tools
- `src/decafclaw/web/static/lib/canvas-state.js` — frontend state
- `src/decafclaw/web/static/components/canvas-panel.js` — panel component
- `src/decafclaw/web/static/widgets/` — bundled widgets (data_table, multiple_choice, markdown_document)
- `src/decafclaw/web/static/widgets/markdown_document/` — markdown_document widget descriptor + Lit component
- `src/decafclaw/web/static/components/widgets/widget-host.js` — frontend host
- `src/decafclaw/web/static/lib/widget-catalog.js` — catalog client
