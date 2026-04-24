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

## Dev workflow

`make dev` watches the bundled widgets directory for `.json` / `.js`
edits and restarts the backend so the new descriptor is picked up. Then
reload the browser — the cache-busting query param
(`?v={mtime}`) ensures the fresh `widget.js` is fetched.

Admin-tier widgets under `data/{agent_id}/widgets/` are not auto-watched
— edit or add one and restart `make dev` manually.

## Out-of-scope for Phase 1

- Input widgets (pause-the-turn + user-submits widgets) — tracked in
  #256 Phase 2.
- Canvas panel (persistent sidebar surface with widgets that update
  across turns) — tracked in #256 Phase 3.
- Additional widget types: `multiple_choice`, `code_block`,
  `markdown_document` — later phases of #256.
- Agent-authored widget JS (workspace tier + iframe sandbox) — #358.
- Hot-reload of widget catalog without server restart — future.

## Related files

- `src/decafclaw/widgets.py` — registry scan + validation
- `src/decafclaw/media.py` — `WidgetRequest` + `ToolResult.widget`
- `src/decafclaw/web/static/widgets/` — bundled widgets
- `src/decafclaw/web/static/components/widgets/widget-host.js` — frontend host
- `src/decafclaw/web/static/lib/widget-catalog.js` — catalog client
