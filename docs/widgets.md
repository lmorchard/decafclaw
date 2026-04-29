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

Phase 2:

- **`multiple_choice`** — radio/checkbox prompt; `accepts_input: true`. Data shape: `{prompt, options, allow_multiple?}`.

Phase 3:

- **`markdown_document`** — rendered markdown; inline (collapsed) + canvas modes. Data shape: `{content: string}`.

Phase 4:

- **`code_block`** — syntax-highlighted code; inline (collapsed) + canvas modes. Data shape: `{code: string, language?, filename?}`. See [Phase 4](#phase-4--code_block-and-canvas-tabs).

Post-Phase 4 (#358 part B):

- **`iframe_sandbox`** — agent-authored HTML/CSS/JS rendered in a CSP-locked sandboxed iframe; inline + canvas modes. Data shape (input): `{body: string, title?: string}`. See [iframe_sandbox](#iframe_sandbox-widget).

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
- **Open in Canvas** — POSTs to `/api/canvas/{conv_id}/new_tab` to push
  the widget into a new canvas tab. The panel opens on the right side of
  the layout.

**Canvas mode:** full content rendered with no truncation. Scroll
position is preserved across `canvas_update` events (clamped to current
scrollable extent so it doesn't leave the viewport).

**Data shape:** `{ content: string }` (raw markdown string).

## Phase 4 — `code_block` and canvas tabs

Phase 4 surfaces the tab-aware data model from Phase 3 as actual tab UI,
adds the `code_block` widget with syntax highlighting via highlight.js,
and adds explicit tab-ID addressing to the canvas tools API.

### `code_block` widget

Bundled at `src/decafclaw/web/static/widgets/code_block/`.
Supports both `"inline"` and `"canvas"` modes.

**Inline mode:** code collapsed via `max-height: 12rem` with a fade
gradient. Two-button footer (always visible): **Expand** / **Collapse**
+ **Open in Canvas**. Open in Canvas POSTs to
`/api/canvas/{conv_id}/new_tab` with `{widget_type: "code_block", data,
label}` (label: filename → `"{language} snippet"` → `"Code"`).

**Canvas mode:** full code rendered, no truncation. Scroll position
preserved across `canvas_update` events.

**Data shape:** `{code: string, language?, filename?}`.

### highlight.js integration

hljs is bundled at `vendor/bundle/highlight.js` (~20 common languages:
Python, JS/TS, JSON, YAML, TOML, Markdown, Bash, Dockerfile, HTML/XML,
CSS/SCSS, SQL, Go, Rust, Ruby, Java, Kotlin, C/C++, plaintext). Two
themes — `atom-one-dark` and `atom-one-light` — are scoped under
`:root[data-theme="dark"]` / `:root[data-theme="light"]` so they follow
the app's theme toggle.

The same hljs highlighting applies to existing chat fenced code blocks
(via `assistant-message.js`) — not just the `code_block` widget.

### Canvas tools (always-loaded)

Five canvas tools are always-loaded so the agent can drive the panel
without activating a skill. Tab IDs (`canvas_1`, `canvas_2`, …) are
returned by `canvas_new_tab` and required as arguments by the mutating
tools — the implicit active-tab model from Phase 3 is gone. See
[Context Composer](context-composer.md#canvas-tools) for descriptions.

- `canvas_new_tab(widget_type, data, label?)` — append a new tab; set
  it active; return `tab_id` in `ToolResult.data["tab_id"]`.
- `canvas_update(tab_id, data)` — replace data on the identified tab
  (errors if `tab_id` not found).
- `canvas_close_tab(tab_id)` — remove the identified tab; activate left
  neighbor (or right; or clear active if last). Last tab → panel hides.
- `canvas_clear()` — empty all tabs; hide the panel.
- `canvas_read()` — return `{active_tab, tabs: [{id, label,
  widget_type, data}, ...]}` via `ToolResult.data`.

## `iframe_sandbox` widget

Bundled at `src/decafclaw/web/static/widgets/iframe_sandbox/`. Renders
agent-authored HTML/CSS/JS in a sandboxed iframe with **no network
access**. Use for interactive demos, charts, small visualizations, or
anywhere text alone won't do.

### Trust model

Two layers of defense, applied independently:

**1. iframe `sandbox` attribute** (set by `widget.js`):
`sandbox="allow-scripts"`. Critically NOT `allow-same-origin` — that
combination would defeat the sandbox by letting iframe scripts touch
the parent's storage and origin. Without it, the iframe runs in the
"null" origin: scripts can run, but they cannot reach parent cookies,
localStorage, or same-origin XHR. We also omit `allow-forms`,
`allow-top-navigation`, `allow-popups`, `allow-modals`.

**2. CSP `<meta>` tag** (injected backend-side during normalization):

```
default-src 'none';
style-src 'unsafe-inline';
script-src 'unsafe-inline';
img-src data:;
font-src data:;
```

Blocks all network — no `fetch`, no remote scripts, no remote images
or fonts. Permits inline `<style>` and `<script>` so demos can be
self-contained. Permits data-URI images and fonts so demos can embed
small assets.

The CSP is injected in Python (`widgets.py::_normalize_iframe_sandbox`)
before the wrapped HTML reaches the browser. The frontend just sets
the wrapped string as `iframe.srcdoc`. Even a buggy or malicious
admin/workspace-tier `widget.js` cannot drop the CSP, because by the
time wrapped HTML reaches the iframe the meta is already in the
document.

### Data shape

**Input** (what tools and the agent provide):

- `body` (required, string, max 256 KB) — HTML body content. Inline
  `<style>` and `<script>` allowed.
- `title` (optional, string, max 200 chars) — populates `<title>`;
  HTML-escaped before injection.

**Stored / transmitted** (post-normalization):

- `body`, `title` preserved (so `canvas_read` round-trips meaningfully).
- `html` — server-generated wrapped document with CSP meta + base
  styles + body content. The Lit widget consumes only `html` for the
  iframe's `srcdoc`.

Normalization is idempotent: a stale `html` field in the input is
overwritten by re-wrapping from `body`, so a `canvas_read` →
`canvas_update` round-trip can't compound or replay a poisoned
document.

### Usage

Any tool can emit one:

```python
return ToolResult(
    text="Rendered demo on canvas",
    widget=WidgetRequest(
        widget_type="iframe_sandbox",
        data={
            "body": "<h1>Live demo</h1><script>document.body.style.background='lime'</script>",
            "title": "Lime demo",
        },
        target="canvas",
    ),
)
```

Or the agent can use `canvas_new_tab(widget_type="iframe_sandbox", data={"body": "..."})`
to drop a demo straight into the canvas with no tool plumbing.

### Future: input widgets

`accepts_input: false` for v1. To support widget-response from inside
the iframe, add a `postMessage` handler in `widget.js` that validates
`event.source === iframe.contentWindow` (origin will be `null` for
sandboxed-without-same-origin iframes, so source-equality is the trust
check). Filed as a follow-up if/when needed.

## Out-of-scope

- Diff view, line numbers/highlighting for `code_block` — follow-on issues.
- Drag-to-reorder tabs — follow-on.
- Tab limit / cap — currently unbounded; horizontal scroll handles overflow.
- Multi-tab in standalone view — current standalone is single-tab focus by design.
- Collapsing `EndTurnConfirm` into a widget with a Mattermost-buttons
  adapter — filed as a follow-up issue.
- Workspace-tier widget catalog (`workspace/widgets/` with first-use
  approval) — #358 part A, still deferred. Agent-writable widget JS is
  a privilege-escalation surface; iframe_sandbox covers the
  agent-authored-content case without that risk.
- postMessage→widget-response from inside iframe_sandbox — future.
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
- `src/decafclaw/web/static/widgets/` — bundled widgets (data_table, multiple_choice, markdown_document, code_block)
- `src/decafclaw/web/static/widgets/markdown_document/` — markdown_document widget descriptor + Lit component
- `src/decafclaw/web/static/widgets/code_block/` — code_block widget descriptor + Lit component
- `src/decafclaw/web/static/widgets/iframe_sandbox/` — iframe_sandbox widget descriptor + Lit component
- `vendor/bundle/highlight.js` — bundled hljs core + ~20 languages + dual themes
- `src/decafclaw/web/static/components/widgets/widget-host.js` — frontend host
- `src/decafclaw/web/static/lib/widget-catalog.js` — catalog client
