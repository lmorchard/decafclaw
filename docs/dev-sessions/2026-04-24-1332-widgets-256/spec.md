# Spec — Widget catalog, Phase 1 (#256)

Tracking issue: https://github.com/lmorchard/decafclaw/issues/256
Follow-up: https://github.com/lmorchard/decafclaw/issues/358 (agent-authored
web UI content — workspace tier + iframe sandbox)
Related, held open: https://github.com/lmorchard/decafclaw/issues/151
(per-tool renderers — reassess after Phase 1 lands)

## Session scope

This session ships **Phase 1 only** of the widget feature: widget plumbing,
the first widget type (`data_table`), and a retrofit of `vault_search` to
prove the end-to-end flow. Phases 2-4 (input widgets, canvas panel,
`code_block`) are deliberately out of scope and will be separate sessions.

## Problem

Tool results in the web UI render as a uniform collapsible `<pre>` block.
`ToolResult.data` exists on the backend but isn't transmitted to the
frontend. The result:

- Tabular output (search hits, counts, scores) reads as a wall of text
- Tools with structured output can't influence how they're displayed
- There's no extension point for admins to add rich renderings

Phase 1 solves the display problem for tool output. Later phases will
generalize to input widgets and a persistent canvas.

## Goals (Phase 1)

- Tools can opt into rich visual output by returning a `WidgetRequest`
- An admin can drop a `widget.json` + `widget.js` pair under
  `data/{agent_id}/widgets/` and have it available without code changes
- `vault_search` renders as a sortable table in the web UI
- Graceful fallback: Mattermost / terminal / unknown widget types render as
  text (existing behavior)
- Reload an old conversation and the widget re-renders as it did live

## Non-goals (Phase 1)

- Input widgets (`multiple_choice`, `widget_response` WebSocket message,
  agent-loop pause on widget input) — **Phase 2**
- Canvas panel, canvas tools, `canvas.json` persistence, `canvas_update`
  event — **Phase 3**
- `code_block`, `markdown_document` widgets — **Phase 3/4**
- Widget hot-reload — **future**
- Workspace-tier widgets (`workspace/widgets/`) and agent-authored iframe
  sandbox — **deferred to #358**
- Refactoring `EndTurnConfirm` to use widgets — leave as-is
- Touching Mattermost or terminal output

## Acceptance criteria

1. Running `vault_search` from the web UI surfaces a sortable
   `data_table` widget with columns for page title, similarity, source,
   and a snippet preview. The widget sits inside the existing tool-message
   container.
2. The same tool call, shown in Mattermost or the terminal, renders the
   unchanged text output (fallback path).
3. Reloading an archived conversation shows the same widget rendering that
   was shown live, without having to re-run the tool.
4. An admin drops a new widget descriptor + component under
   `data/{agent_id}/widgets/`, restarts the bot, and the new widget is
   usable by any tool that returns
   `WidgetRequest(widget_type="<name>", data=...)`.
5. If a tool returns `WidgetRequest` with `data` that fails schema
   validation, the tool message still renders — the widget is dropped, a
   warning is logged, and the text fallback is shown.
6. If the frontend encounters a `widget_type` it doesn't have a component
   for (unknown or failed dynamic import), it renders the raw text inside
   a `<pre>` with a small note and logs to the console.
7. The dev inner loop for editing a bundled widget works: `make dev`
   auto-restarts on `.json` / `.js` edits under the bundled widgets
   directory, and a page reload picks up the new `widget.js`.
8. Lint (`make lint`, `make typecheck`, `make check-js`) and tests
   (`make test`) are clean.

## Architecture

### Backend: `WidgetRequest` dataclass

New dataclass in `src/decafclaw/media.py` (alongside `ToolResult` and
`EndTurnConfirm`):

```python
@dataclass
class WidgetRequest:
    widget_type: str                      # must match a registered widget name
    data: dict                            # must conform to widget's data_schema
    target: str = "inline"                # "inline" only in Phase 1; "canvas" in Phase 3
    on_response: Callable | None = None   # Phase 2 only — unused in Phase 1
    response_message: str | None = None   # Phase 2 only — unused in Phase 1
```

`on_response` and `response_message` are declared now with `None` defaults
so Phase 2 doesn't require a dataclass revision. Phase 1 only exercises
`widget_type`, `data`, and `target="inline"`.

### Backend: `ToolResult` integration

Add a `widget: WidgetRequest | None = None` field to `ToolResult`. No other
changes to `ToolResult`. The `text` field remains the LLM/judge contract
and the Mattermost/terminal fallback.

### Backend: widget registry

New module `src/decafclaw/widgets.py` (or similar). Responsibilities:

- At startup, scan two catalog directories in order:
  1. `src/decafclaw/web/static/widgets/` (bundled, tier="bundled")
  2. `data/{agent_id}/widgets/` (admin, tier="admin")
- For each subdirectory, parse `widget.json` and confirm `widget.js`
  exists. Missing / malformed entries are logged and skipped.
- Admin tier overrides bundled on name collision (admin wins).
- Validation pass at load time: `widget.json` itself is checked against a
  meta-schema to ensure required fields are present (`name`,
  `description`, `modes`, `data_schema`). A widget that fails meta-schema
  validation is skipped with a warning.
- Registry exposes:
  - `get(name)` → descriptor (or None)
  - `list()` → all descriptors
  - `validate(name, data)` → True/False + error message; uses
    `jsonschema.Draft7Validator` (or latest default)
  - `resolve_path(name)` → filesystem path to `widget.js` for the serving
    route
  - `tier(name)` → "bundled" or "admin"
- Module-level singleton initialized during app startup, accessible via
  `get_widget_registry()` or similar. Tools don't typically touch the
  registry — they just emit `WidgetRequest`. The agent loop is what calls
  `validate` before serializing to the WebSocket.

**Dependency:** Add `jsonschema` as a direct dep in `pyproject.toml`.
Already a transitive dep (version 4.26), so this costs zero new install
footprint.

### Backend: agent loop changes

In the tool-execution path (`agent.py` / wherever `ToolResult` is
serialized into a `tool_end` event):

1. After `execute_tool` returns a result with a `widget` field, call
   `registry.validate(widget.widget_type, widget.data)`.
2. If validation fails: log a warning with tool name, widget type, and
   validator error; set `widget = None` so the event carries only text.
3. If the widget is valid: serialize `widget_type`, `target`, and `data`
   into the `tool_end` event.
4. Phase 1 does NOT emit `canvas_update` events and does NOT pause on
   input widgets (those are Phase 2/3).

### Backend: widget serving route

New route on the web server, auth-gated like other web routes:

```
GET /widgets/{tier}/{name}/widget.js
```

- `tier` ∈ `{"bundled", "admin"}`.
- Handler asks the registry for the widget, confirms tier matches, reads
  the resolved filesystem path, and serves the file with
  `Content-Type: application/javascript`.
- Path traversal: the handler computes the path strictly via
  `registry.resolve_path(name)`; the route params are not concatenated
  into a filesystem path directly.
- Response includes an appropriate `Cache-Control`. We'll rely on
  `?v={mtime}` query-param cache-busting (see below) rather than
  long-max-age immutable caching, since widget files can change in dev.

Bundled widget JS is thus served through the same route as admin. The
existing static-files mount does not carry the `/widgets/` prefix for
this purpose — using a dedicated route gives us a single validation path.

### Backend: `GET /api/widgets` endpoint

Returns a JSON list of widget descriptors for frontend use:

```json
[
  {
    "name": "data_table",
    "tier": "bundled",
    "description": "...",
    "modes": ["inline", "canvas"],
    "accepts_input": false,
    "data_schema": { ... },
    "js_url": "/widgets/bundled/data_table/widget.js?v=1714000000"
  }
]
```

`js_url` includes a `?v={mtime}` query param (Unix timestamp of
`widget.js`) so browsers re-fetch when the file changes. The frontend
uses `js_url` directly for dynamic import.

### WebSocket protocol: `tool_end` event

The existing `tool_end` event gains a `widget` field (null when the tool
returned no widget):

```json
{
  "type": "tool_end",
  "conv_id": "...",
  "tool": "vault_search",
  "tool_call_id": "...",
  "result_text": "Found 12 matching pages ...",
  "display_short_text": "12 results",
  "widget": {
    "widget_type": "data_table",
    "target": "inline",
    "data": { "columns": [...], "rows": [...], "caption": "..." }
  }
}
```

All other `tool_end` fields are unchanged. No new event types in Phase 1.

### Archive persistence

The conversation archive (JSONL per conversation) stores tool results as
records with `role: "tool"` (not `"tool_result"`). Phase 1 adds a `widget`
field to those records when the tool emitted one:

```json
{
  "role": "tool",
  "tool_call_id": "...",
  "content": "Found 12 matching pages ...\n\n```json\n...\n```",
  "display_short_text": "12 results",
  "widget": {
    "widget_type": "data_table",
    "target": "inline",
    "data": { ... }
  }
}
```

Notes on the write path:

- There are **multiple `"role": "tool"` write sites**: the normal-path
  archive in `agent.py` after `execute_tool`, an error/exception-path
  archive in the same file, and a context_composer path. Phase 1 touches
  the normal path (only successful tool calls get widgets); error paths
  never emit widgets, so no change needed there.
- Only `widget_type`, `target`, and `data` are serialized — `on_response`
  is a callable and not round-trippable.

On conversation reload, the web UI loads history via
`load_history` → `_handle_load_history`. Whatever transformation currently
surfaces `content` / `display_short_text` to the frontend needs to also
pass through the `widget` field. Phase 1 adds this passthrough.

If the widget type referenced in an archived record is no longer in the
registry at reload time, the frontend's unknown-type fallback kicks in
(pre-block with a note). No backend revalidation on reload — trust the
archived payload.

### Frontend: `dc-widget-host` Lit component

New component in `src/decafclaw/web/static/components/widgets/` (new
directory). Behavior:

- Props: `widgetType`, `data`, `jsUrl` (supplied by parent from `tool_end`
  payload + `/api/widgets` catalog lookup).
- Fetches `/api/widgets` once at app init, caches the descriptors
  (name → descriptor with `js_url`) on the window or a service.
- On first use of a given widget type: dynamic `import(jsUrl)`. Memoizes
  the import promise per URL so we don't reload on every render.
- Instantiates `<dc-widget-{type}>` with the `data` property set.
- Forwards `widget-response` CustomEvents up to its parent (Phase 2 use,
  but the plumbing lands now so Phase 2 doesn't need to touch this
  component).
- Unknown widget type OR failed import: renders a `<pre>` with the raw
  text (passed as a `fallbackText` prop) and a small muted
  "(widget '{type}' unavailable)" note. Logs to `console.warn`.

### Frontend: `dc-widget-data-table` Lit component

New component in `src/decafclaw/web/static/widgets/data_table/widget.js`.
This is a bundled widget and sits under the bundled tier directory.

- Props: `data` = `{ columns: [{key, label}], rows: [{...}], caption?: string }`.
- Renders an HTML `<table>` inside a scrollable container.
- Columns are sortable: clicking a header cycles unsorted → ascending →
  descending. Sort is client-side, value-type-aware (numeric vs string).
- Caption renders above the table when present.
- No external deps — uses Lit and native DOM only.
- Accessibility basics: sortable headers are `<button>` elements inside
  `<th>`, `aria-sort` attribute reflects current state.

### Frontend: `ToolMessage` changes

The existing `tool-message.js` component (or wherever the `<pre>`
rendering lives) gets one addition:

- When the message payload has a `widget` field AND the widget validates:
  - Expanded view: render `<dc-widget-host>` with `widgetType`, `data`,
    `jsUrl` (looked up from the catalog), and `fallbackText=result_text`.
  - Below the widget: a `<details>` element labeled "Show raw result",
    collapsed by default, containing the existing `<pre>` of
    `result_text`.
- When no widget: unchanged behavior (current `<pre>` rendering).

The collapsed / short header of the tool message is unchanged in both
cases; only the expanded body changes.

### `vault_search` retrofit

Modify `tool_vault_search` (and its `_substring_search` helper) in
`src/decafclaw/skills/vault/tools.py`:

- Text output is unchanged — same format as today, still consumed by
  Mattermost, terminal, and the LLM/judge.
- `ToolResult.data` is untouched (vault_search doesn't currently use it;
  Phase 1 doesn't introduce it).
- Additionally, build a `WidgetRequest` with `widget_type="data_table"`,
  `target="inline"`, and `data` describing the result set.

**Semantic search path** (primary) emits a table with:

- `caption`: e.g. `"vault_search: \"<query>\" — <N> result(s)"`
- Columns: `page` (label: "Page"), `similarity` (label: "Similarity"),
  `source` (label: "Source"), `snippet` (label: "Snippet")
- Rows: one per hit; `similarity` rounded to 3 decimals, `snippet` first
  ~160 chars of `entry_text`, `source` from `source_type` field

**Substring fallback path** emits a thinner table:

- Same caption format
- Columns: `page` ("Page"), `excerpt` ("Excerpt")
- Rows: one per hit

**Empty results** (both paths): `widget` stays `None`. No empty table.
Text fallback ("No results matching ...") is already what renders today.

The `data_table` component must tolerate different column sets across
calls — it reads `columns` from `data` and renders whatever's declared.

### Dev ergonomics

Update `make dev` to also watch `.json` and `.js` file changes under the
bundled widgets directory:

```make
dev:
    uv run --extra dev watchfiles \
      --filter python \
      --filter default \
      --sigint-timeout 10 --sigkill-timeout 15 \
      "decafclaw.main" src/
```

(Exact filter syntax depends on what `watchfiles` supports; may require a
custom filter function in a small shim script. The acceptance criterion
is: editing a `widget.json` or `widget.js` under `src/decafclaw/web/static/widgets/`
triggers a dev restart.)

Admin-tier widgets under `data/{agent_id}/widgets/` are not watched;
editing them requires a manual `make dev` restart. That's acceptable —
admin widgets are rarely edited in place.

Browser-side cache-bust is the `?v={mtime}` query param on `js_url` from
`/api/widgets`. A page reload after a backend restart will re-fetch the
catalog, see a new `v`, and dynamically import the fresh file.

## Error-handling / edge cases

- **Widget registry empty on startup** (e.g., admin forgets to put
  `widget.js` next to `widget.json`): registry just has fewer entries;
  `/api/widgets` returns what's there; the log has warnings for each
  skipped directory. Tools that reference unknown widget types get the
  validation-failure path (widget stripped, text still shown).
- **Tool returns a widget with a type the registry doesn't know** (e.g.,
  typo): validation call looks up the descriptor, doesn't find one, logs
  `warning: unknown widget type 'foo' from tool 'x'`, strips the widget,
  sends text-only `tool_end`. No crash.
- **Tool returns a widget whose `data` is malformed**: `jsonschema`
  validation fails; warning logged; widget stripped; text-only `tool_end`.
- **Browser fails to dynamic-import `widget.js`** (404, syntax error):
  `dc-widget-host` catches the import rejection, falls back to the
  unknown-type renderer (pre + note + console.warn).
- **Archive contains widget data but its type was deleted / renamed**
  between the live turn and reload: frontend shows the unknown-type
  fallback. The raw text is still rendered underneath (via the "Show raw
  result" details). No data loss from the user's POV.
- **Widget schema change** after an archive was written: not revalidated
  on reload; trust archived payload. Frontend component must tolerate
  missing / extra keys gracefully (Lit components already do).
- **Widget data is very large** (e.g., 10k-row table): no hard cap in
  Phase 1. Tool authors responsible for sensible limits. If this bites
  us, we revisit.
- **Mattermost and terminal** receive `ToolResult.text` only — they never
  see `ToolResult.widget`. No channel-specific code changes needed.
- **Reflection / judge** sees `ToolResult.text` only — unchanged from
  today.

## Out-of-scope / deferred risks tracked elsewhere

- **Workspace-tier widgets** (`workspace/widgets/`) — agent-writable code
  running in the browser. Deferred to **#358**; Phase 1 drops the third
  catalog tier entirely.
- **Agent-authored iframe sandbox** — also **#358**.
- **Widget hot-reload** — future.
- **Tool-name renderer registry** (`#151`) — held open; not superseded
  until Phase 1 actually ships and we see how widgets feel.

## File inventory (expected)

New:
- `src/decafclaw/widgets.py` (registry, validator, path resolution)
- `src/decafclaw/web/static/widgets/data_table/widget.json`
- `src/decafclaw/web/static/widgets/data_table/widget.js`
- `src/decafclaw/web/static/components/widgets/widget-host.js`
- `docs/widgets.md` (admin-facing guide: how to add a widget)
- `tests/test_widgets.py` (registry + validator)
- `tests/test_widgets_vault_search.py` (retrofit happy path / fallback)

Modified:
- `src/decafclaw/media.py` — add `WidgetRequest`, extend `ToolResult`
- `src/decafclaw/agent.py` (or equivalent) — validate + serialize widget in `tool_end`
- `src/decafclaw/archive.py` — round-trip `widget` field on tool records
- `src/decafclaw/web/` — new route + `/api/widgets` endpoint
- `src/decafclaw/skills/vault/tools.py` — emit widget on `vault_search`
- `src/decafclaw/web/static/components/messages/tool-message.js` — widget render + raw-result details
- `pyproject.toml` — `jsonschema` as direct dep
- `Makefile` — broaden `make dev` watcher
- `CLAUDE.md` — key files entry for widgets module
- `docs/index.md` — link to new widgets doc
- `README.md` — short mention of widget support (one line + link to doc)
