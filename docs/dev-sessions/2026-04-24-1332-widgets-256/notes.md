# Notes — Widget catalog & canvas panel (#256)

## Session setup

- Worktree: `.claude/worktrees/widgets-256/` (branch `widgets-256`)
- Baseline: lint clean, 1785 tests pass, synced with origin/main at `d3ebc61`
- Issue: https://github.com/lmorchard/decafclaw/issues/256
- Related (maybe in scope): https://github.com/lmorchard/decafclaw/issues/151
- Follow-up filed: **#358** — agent-authored web UI content (workspace
  tier + iframe sandbox), blocks future Phase 2/3/4 deferrals

## Session scope locked during brainstorm

Phase 1 only. Phases 2 (input widgets), 3 (canvas), 4 (code_block +
polish) are out of scope and will be separate sessions.

Decisions worth flagging in hindsight:

- **#256 vs #151.** #151 (per-tool renderers) held open; reassess after
  Phase 1 widgets land. Didn't close it.
- **Archive format.** Full round-trip of widget payloads on tool
  records (`role: "tool"`, adding a `widget: {widget_type, target, data}`
  sibling). `on_response` callables are not serialized — phase 2 problem.
- **vault_search first.** `conversation_search` was the original pick
  but Les said it's not used much anymore. `vault_search` retrofit
  covers both semantic and substring paths.
- **Schema validation via `jsonschema`.** Already a transitive dep at
  4.26.0, so promotion to direct cost zero install footprint.
- **Workspace tier dropped for v1.** Agent-writable widget JS is a real
  privilege-escalation surface (session-token exfil, DOM spoofing,
  arbitrary `/api/workspace/*` access). Catalog has two tiers: bundled
  + admin, admin overrides bundled on name collision.
- **Reflection/judge: text-only.** Widget is display-only; the judge
  prompt stays unchanged.
- **Inline widget UX:** widget replaces `<pre>` in the expanded body,
  with a `<details>` "Show raw result" below for debuggability.

## Running notes

### Surprises encountered during execution

- **Multiple `"role": "tool"` write sites.** Spec called it out, and
  only the normal-path archive (after successful `execute_tool`)
  needed widget support. The error-path archive is a fresh
  `ToolResult(text="[error...]")` with no widget — nothing to do there.
- **`jsonschema` module attribute warnings.** Pyright doesn't know
  about `jsonschema.validators` / `jsonschema.protocols` via the root
  package; fixed by importing `validator_for` from the submodule and
  typing the cached validator as `Any`.
- **`watchfiles --filter` accepts a dotted path.** Used this to add
  `decafclaw._dev_filter.DevFilter` — subclass of `PythonFilter` that
  also allows `.json`/`.js` under `web/static/widgets/`. Smoke-tested
  with `watchfiles.Change` fixtures. Cleaner than running two
  watchfiles in parallel.
- **Conv_history passes archive dicts through verbatim.** No backend
  transformation step needed for widget to survive reload — the
  `widget` field rides along on the archived `role: "tool"` record and
  lands in `chat-view` as `m.widget`.
- **Frontend cache wrinkle.** `?v={mtime}` cache-bust on widget.js
  URLs makes page reload sufficient to pick up edits. Browser's ES
  module cache would otherwise stick around per session.

### Testing notes

- Agent loop tests: the `ctx`/`config` fixtures from `tests/conftest.py`
  cover what we needed. Used `monkeypatch` on
  `decafclaw.agent.execute_tool` to inject fake tools returning
  widgets with valid / invalid / no-widget payloads.
- Web route tests: used the existing `ASGITransport` + `AsyncClient`
  pattern from `tests/test_web_conversations.py`. Test registry
  fixtures build a minimal on-disk catalog and monkeypatch the global
  `_registry`.
- Ran into one ruff import-order nit each time a new test file landed;
  `ruff check --fix` handled it.

### Unable to live-test the UI

`make dev` is running on Les's end. I can't start a second instance
per `CLAUDE.md` (MM websocket is single-connection), and I don't have
a way to hit his authenticated web session from here. The full stack
is covered by unit + integration tests; a live smoke (load the web
UI, run `vault_search`, confirm the table renders sortable with a
"Show raw result" toggle) is a Les task post-merge.

### Final shape

12 commits, ~1850 tests passing, clean lint + typecheck across Python
and JS. PR opens against `main`.
