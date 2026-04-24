# Plan — Widget catalog Phase 1 (#256)

Goal: implement everything described in `spec.md` for Phase 1. Each step
ends in a `make check && make test` pass and a commit. Branch is
`widgets-256`; PR at the end.

## Ordering rationale

Backend-first so the agent can emit widgets before the frontend can show
them (tests-only at first). Then frontend in two bites (widget-host
container, then bundled `data_table`). Then the retrofit. Then dev
ergonomics. Then docs and CLAUDE.md entries. This order gives us working
tests at each step and a fully-live end-to-end feature at the last
implementation step.

Rough blast-radius: ~15 files touched across ~10 commits.

## Steps

### Step 1 — Bones: `WidgetRequest` + `ToolResult.widget` + `jsonschema` dep

**Files:**
- `src/decafclaw/media.py` — add `WidgetRequest` dataclass, add
  `widget: WidgetRequest | None = None` field on `ToolResult`.
- `pyproject.toml` — add `jsonschema>=4.0` to the main `dependencies`
  list (already transitive, just needs to be explicit).
- `tests/test_media.py` (new or extended) — construction tests for
  `WidgetRequest` and the new `ToolResult.widget` field.

**Verification:**
- `uv sync` to confirm the explicit dep resolves the same version.
- `make lint && make typecheck && make test`.

**Commit:** `feat(widgets): add WidgetRequest dataclass and ToolResult.widget field`

### Step 2 — Widget registry module

**Files:**
- `src/decafclaw/widgets.py` (new) — the registry. Exports:
  - `WidgetDescriptor` dataclass (name, tier, description, modes,
    accepts_input, data_schema, js_path, mtime).
  - `WidgetRegistry` class (scan, `get`, `list`, `validate`,
    `resolve_path`, `tier`).
  - `load_widget_registry(config) -> WidgetRegistry` — builds the
    registry by scanning bundled + admin catalog dirs.
  - `get_widget_registry()` — module-level accessor for code that can't
    easily thread the registry through `ctx`.
  - Internal meta-schema for validating `widget.json` itself.
- `tests/test_widgets.py` (new) — registry tests:
  - Scans bundled + admin, admin overrides bundled on name collision.
  - Missing `widget.js` → entry skipped with log.
  - Malformed `widget.json` → entry skipped with log.
  - `validate(name, data)` returns ok/error for happy + sad payloads.
  - `validate` on unknown type returns error, not exception.
  - `resolve_path` returns existing path; raises for unknown.
- Use `tmp_path` fixtures to build synthetic catalog dirs; no real
  widgets yet.

**Verification:** `make check && make test`. Widget registry tests cover
scan, validate, resolve.

**Commit:** `feat(widgets): widget registry with scan + jsonschema validation`

### Step 3 — Registry wiring + startup

**Files:**
- `src/decafclaw/runner.py` (or `__init__.py` — whichever orchestrates
  startup) — call `load_widget_registry(config)` once at startup and
  stash the singleton. Copy the pattern from `mcp_client.py`'s
  module-level registry (init-once, accessed via a getter). Don't
  invent a new pattern.
- No new config options in v1 — catalog paths are hardcoded
  (`src/decafclaw/web/static/widgets/` + `data/{agent_id}/widgets/`).
- Lightweight test in `tests/test_widgets.py` (or extend step 2's
  tests) that `load_widget_registry` succeeds on an empty-catalog
  config without raising.

**Verification:** `make check && make test`. Do **not** start a real bot
instance — Les has `make dev` running, a second connection breaks MM
websocket subscription per CLAUDE.md. Tests alone suffice.

**Commit:** `feat(widgets): load widget registry at startup`

### Step 4 — Agent loop: validate + serialize widget in `tool_end`

**Files:**
- `src/decafclaw/agent.py` — in the tool-execution path, after
  `execute_tool`:
  1. If `result.widget is not None`, call
     `registry.validate(widget.widget_type, widget.data)`.
  2. On failure, log warning, set `widget = None` on the result (so it
     doesn't make it into the event or archive).
  3. On success, include `widget_type`, `target`, `data` as a dict in
     the `tool_end` event publish.
- Archive write: include `widget` field on the `{"role": "tool", ...}`
  record when present. Only the normal-path archive write is touched —
  the error-path does not emit widgets.
- `tests/test_agent.py` or new `tests/test_agent_widgets.py` — exercise:
  - Tool returns `ToolResult(text, widget=valid)` → `tool_end` event
    carries widget, archive record has widget.
  - Tool returns `ToolResult(text, widget=invalid_data)` → `tool_end`
    event has no widget, warning is logged, archive record has no widget.
  - Tool returns `ToolResult(text, widget=unknown_type)` → same as
    invalid.
  - Tool returns `ToolResult(text)` (no widget) → no `widget` key in
    event, archive record has no widget key.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): validate and serialize widget on tool_end`

### Step 5 — Web routes: `/widgets/{tier}/{name}/widget.js` + `GET /api/widgets`

**Files:**
- `src/decafclaw/http_server.py` (or wherever web routes are declared
  — may be `src/decafclaw/web/...`). Add two routes:
  1. `GET /widgets/{tier}/{name}/widget.js` — handler looks up the
     widget, confirms tier matches, reads the resolved path from
     `registry.resolve_path`, returns file with
     `Content-Type: application/javascript`. 404 on unknown widget /
     tier mismatch.
  2. `GET /api/widgets` — returns JSON list of descriptors with
     `js_url` including `?v={mtime}` cache-bust.
- Both routes go behind the existing auth/session middleware.
- `tests/test_web_widgets.py` — async test client to hit both endpoints:
  - 200 with valid widget: returns JS file contents.
  - 404 on unknown widget name.
  - 404 on tier mismatch (admin widget requested via bundled URL).
  - `GET /api/widgets` returns expected shape, `js_url` includes `?v=`.
  - Unauthenticated request is rejected.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): /api/widgets endpoint and widget.js serving route`

### Step 6 — WebSocket `tool_end` projection passes through `widget`

**Files:**
- `src/decafclaw/web/websocket.py` — the `tool_end` case projects a
  specific set of fields into the outgoing message. Add `widget` to
  the projected payload (only when present, to keep noise down).
- `tests/test_web_websocket.py` (or existing WS test file) — verify a
  `tool_end` event with `widget` produces a WebSocket message with the
  same `widget` payload.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): forward widget field in tool_end WS event`

### Step 7 — `dc-widget-host` Lit component

**Files:**
- `src/decafclaw/web/static/components/widgets/widget-host.js` (new) —
  `<dc-widget-host>`:
  - Properties: `widgetType`, `data`, `jsUrl`, `fallbackText`.
  - On `updated()` / first use: fetch descriptor if not cached, then
    dynamic `import(jsUrl)`. Memoize per-URL.
  - On success: render `<dc-widget-{type}>` with `.data=${this.data}`.
  - On failure / unknown type: render `<pre>${fallbackText}</pre>` +
    a small muted "(widget '{type}' unavailable)" note; `console.warn`.
  - Forwards `widget-response` CustomEvents up (Phase 2 plumbing).
- A small widget-catalog service in
  `src/decafclaw/web/static/lib/widget-catalog.js` (new) — `async getCatalog()`
  hits `/api/widgets` once, memoizes, exposes `get(name) → descriptor`.
- Wire widget-host into the vendor bundle entry if needed (probably
  just imported lazily, no bundle change).

**Verification:** `make check-js`. No Python impact.

**Commit:** `feat(web): dc-widget-host component with dynamic import + fallback`

### Step 8 — Bundled `dc-widget-data-table`

**Files:**
- `src/decafclaw/web/static/widgets/data_table/widget.json` (new) —
  descriptor from the spec.
- `src/decafclaw/web/static/widgets/data_table/widget.js` (new) —
  `<dc-widget-data-table>` Lit component:
  - Property `data` = `{columns, rows, caption?}`.
  - Renders `<figure>` → optional `<figcaption>` → scrollable
    `<div><table>...</table></div>`.
  - Sortable columns: `<button>` inside `<th>`, cycles
    none → asc → desc; `aria-sort` reflects state.
  - Sort comparator: numeric-aware (if both values in column coerce to
    number, numeric sort; else string).
  - No external dependencies.

**Verification:**
- `make check-js`.
- Add a Python test (extend `tests/test_widgets.py`) that a fresh
  registry scan against the real
  `src/decafclaw/web/static/widgets/` directory finds `data_table` and
  its `data_schema` shape matches expectations. This catches malformed
  `widget.json` before it causes confusing frontend behavior.
- `make check && make test`.

**Commit:** `feat(widgets): bundled data_table widget`

### Step 9 — `tool-message` + `chat-message` widget wiring

**Files:**
- `src/decafclaw/web/static/components/messages/tool-message.js` —
  - Add `widget` property.
  - In `render()`, when `widget` is present: render `<dc-widget-host>`
    with `widgetType`, `data`, `jsUrl` (from catalog lookup), and
    `fallbackText=this.content`. Below the host: a `<details>` element
    labeled "Show raw result", collapsed by default, containing the
    existing `<pre>` of `content`.
  - When no widget: existing behavior unchanged.
  - Import `widget-host.js` and `widget-catalog.js`.
- `src/decafclaw/web/static/components/chat-message.js` — on the
  `role === 'tool'` branch, forward `.widget=${this.widget || null}` to
  `<tool-message>`. Add `widget` to declared properties.
- `src/decafclaw/web/static/components/chat-view.js` — pass
  `.widget=${m.widget || null}` when rendering each `<chat-message>`.
- `src/decafclaw/web/static/lib/tool-status-store.js` — in the
  `tool_end` case, include `widget: msg.widget || null` in the
  `replaceToolCall` payload.

**Verification:**
- `make check-js`.
- Manual smoke: reload the web UI, call a tool that doesn't yet emit a
  widget → no visible change, details-toggle not present. (All
  green-field until step 10.)

**Commit:** `feat(web): tool-message renders widget when present, with raw-result details`

### Step 10 — Retrofit `vault_search` to emit `data_table` widget

**Files:**
- `src/decafclaw/skills/vault/tools.py`:
  - `tool_vault_search` (semantic path): when `results` non-empty, build
    `WidgetRequest(widget_type="data_table", target="inline", data={...})`
    with columns `page`, `similarity`, `source`, `snippet` and rows
    derived from `results`. Return `ToolResult(text=..., display_short_text=..., widget=...)`.
  - `_substring_search`: same treatment, thinner columns (`page`,
    `excerpt`).
- `tests/test_widgets_vault_search.py` (new):
  - Monkeypatch `search_similar` to return canned hits, call
    `tool_vault_search`, verify widget is present, columns/rows shape
    matches.
  - Same for substring fallback with a real tmp vault dir.
  - Empty-results case: widget is `None`.

**Verification:** `make check && make test`. Then:
- **Live test** (ideally Les, since he has `make dev` running and a
  browser session authenticated): run `vault_search` via web UI chat,
  confirm a table renders with sortable headers and a "Show raw
  result" details toggle underneath. Confirm Mattermost/terminal (if
  tested) still render text-only.
- Alternative if Les isn't around: attempt a Playwright MCP session
  against the running dev instance (requires auth cookie or token —
  may not be easy). If Playwright can't authenticate, explicitly say
  "unable to live-test the UI" per CLAUDE.md rather than claiming
  success.

**Commit:** `feat(widgets): vault_search emits data_table widget`

### Step 11 — Dev watcher + docs

**Files:**
- `Makefile` — broaden `make dev` watcher to include `.json` and `.js`
  edits under the bundled widgets path. Easiest implementation: a
  second parallel `watchfiles` invocation, or a shell loop. Target:
  editing `src/decafclaw/web/static/widgets/data_table/widget.json`
  triggers a restart.
- `docs/widgets.md` (new) — admin guide:
  - What a widget is.
  - Two tiers (bundled + admin), admin overrides bundled.
  - Minimal example: `widget.json` + `widget.js` stub.
  - How to write a `WidgetRequest` from a tool.
  - Caveats: no workspace tier yet (pointer to #358), no input widgets
    yet (pointer to Phase 2), no canvas yet (Phase 3).
- `docs/index.md` — link to `widgets.md`.
- `README.md` — one-line mention + link to doc.
- `CLAUDE.md` — key files entry for `src/decafclaw/widgets.py`.

**Verification:** `make check && make test`. Visual check of
`docs/widgets.md` rendering.

**Commit:** `docs(widgets): admin guide + make dev watcher for bundled widgets`

### Step 12 — Final review + PR prep

- Rebase `widgets-256` onto latest `origin/main`
  (`git fetch origin && git rebase origin/main`) — re-run lint + tests
  after.
- Update `notes.md` with observations from execution (surprises,
  decisions that emerged).
- Push the branch: `git push -u origin widgets-256`.
- Open PR:
  - Title: `feat(widgets): phase 1 — widget catalog, data_table, vault_search retrofit (#256)`
  - Body: summary, test plan (lint/test/live), link to #256, note that
    it's phase 1 of 4, mention that #358 was filed for deferred scope.
- Request Copilot review:
  `gh pr edit <N> --add-reviewer copilot-pull-request-reviewer`.
- Move #256 to **In review** on the project board (manual if gh
  project commands don't cover it).

## Verification gates (per step)

Each implementation step ends with:

1. `make lint` — ruff clean.
2. `make typecheck` — pyright clean.
3. `make check-js` — tsc clean (if any JS was touched).
4. `make test` — pytest clean.
5. `git add {specific paths} && git commit` — no `git add -A`, per the
   commit rules. Every commit must include the
   `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
   trailer per the system git rules.

## Risk log

- **Startup wiring may be trickier than expected.** Where exactly the
  registry singleton lives, how it's threaded to the agent loop and
  web handler. If it gets messy, put it on `ctx` (forkable) or stash
  on `config`-adjacent runtime state. Don't invent a new singleton
  pattern — copy from MCP.
- **`make dev` watcher broadening.** `watchfiles --filter python` is a
  fixed filter name. Adding a custom filter might need a Python shim.
  Fallback: run two watchfiles in parallel via `&` with proper signal
  handling, OR tolerate restart-on-widget-edit being manual and
  document it. Don't spend >30min on this; ship the manual-restart
  fallback and open a follow-up issue if needed.
- **`vault_search` has zero hits often** — the test pool is
  substantial, so retrofit tests need to stub the embedding search
  function rather than relying on real vault content.
- **Archive size** — widget payloads add bytes to the archive. For
  `data_table` with ~10 rows, it's a few KB. Not a concern. Flag if
  anything emits a 100-row table routinely.
- **Web UI cache** after adding a new widget — covered by `?v={mtime}`
  on `widget.js` URLs, but the dynamic-import module cache is
  in-memory for a session. A page reload suffices to bust both.

## What's deliberately not here

- No Phase 2/3/4 work (input widgets, canvas, code_block,
  markdown_document).
- No workspace-tier widget scan (→ #358).
- No hot-reload.
- No widget registry exposed as an LLM tool (agents don't
  `list_widgets`; they just emit `WidgetRequest` with a known type).
- No migration tool for old archives — existing archives have no
  `widget` field and render as today.
