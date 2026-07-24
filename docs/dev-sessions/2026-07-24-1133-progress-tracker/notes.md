# Notes — progress-tracker (#414)

## Session summary

Built a display-only `progress_tracker` widget and wired two producers to
auto-emit it into the sticky slot (#419) without any new agent-facing
tools:

- **Task 1 — widget.** `src/decafclaw/web/static/widgets/progress_tracker/`
  (`widget.json` + `widget.js`, Lit element). Snapshot-rendered (each
  update replaces the full step list). Modes `["inline", "canvas",
  "sticky"]`, `accepts_input: false`. `data_schema`: `steps[]` (each
  `{label, status, note?}`, `status ∈ pending | in_progress | done | failed
  | skipped`), plus optional `title` and `summary` (the summary drives the
  sticky slot's collapsed header line). Five distinct glyphs (○ ◐ ● ✗ ⊘).
- **Task 2 — checklist auto-emit.** `checklist_create` / `_step_done` /
  `_abort` (now async) mirror checklist state into the sticky slot as a
  `progress_tracker` via `sticky.set_sticky` / `clear_sticky`, fail-open.
  The slot clears once all steps are done or on abort. `checklist_status`
  stays sync (read-only, no emit).
- **Task 3 — project skill auto-emit.** While a project is in the
  `executing` phase, `project_next_task` / `project_update_step` /
  `project_add_steps` mirror the plan's leaf steps into the sticky slot.
  Clears on `project_task_done` finalizing the project (DONE) and on
  `project_advance` moving the project out of `executing`. No emit outside
  `executing`. Fail-open.
- **Task 4 — docs + integration gate** (this task). Documented the widget
  and both auto-emit producers, fixed stale sticky-slot doc text, updated
  CLAUDE.md, ran `make check` / `make test` clean.

## Key decisions

- **Five statuses including `skipped`.** Matches the project skill's plan
  step vocabulary (`- [-] N. Skipped step`) so the project producer can
  pass plan-step status straight through without a lossy mapping.
- **Project auto-emit is EXECUTING-only.** Brainstorm/spec/plan-review
  phases don't have a step list yet; emitting outside `executing` would
  show a stale or empty tracker. The slot only starts tracking once the
  agent enters execution and clears the moment it leaves (either by
  finishing or by replanning).
- **Fail-open everywhere.** Both producers wrap the sticky mirror in a
  bare `try/except Exception: log.warning(...)` — a sticky-slot failure
  must never break the underlying checklist or project tool call. This
  matches the project's existing "producers fail-open" convention (see
  notifications, memory retrieval).
- **No new tools, no eval cases.** The auto-emit is a side effect of
  existing tools (`checklist_*`, `project_*`); nothing new is exposed to
  the LLM, so there's no new tool-choice surface to guard with an eval.
  `widget_pin_sticky` remains the manual path for pinning a
  `progress_tracker` directly.
- **Both producers call `sticky.set_sticky`/`clear_sticky` directly**,
  not the `widget_pin_sticky`/`widget_unpin_sticky` tools — those tools
  exist for agent-driven pinning; the auto-emit is a mechanical mirror the
  agent doesn't decide about.

## Commits

Full feature range (Tasks 1–4, docs/spec/plan commits, oldest first):

```
8015f9f docs(414): spec for progress_tracker widget + checklist/project auto-emit
1e4822e docs(414): implementation plan for progress_tracker widget + auto-emit
bc4ceac feat(414): progress_tracker widget (meta + Lit element + styles)
b345081 feat(414): checklist auto-emits progress_tracker to sticky slot
e2f52d0 feat(414): project skill auto-emits progress_tracker during executing
b253774 fix(414): clear sticky when project leaves EXECUTING via project_advance; test planning-phase guard
```

(`git log --oneline b345081^..HEAD` — the command named in the Task 4
brief — resolves to `b345081`, `e2f52d0`, `b253774`; it excludes `bc4ceac`,
the Task 1 widget commit, since `b345081^` *is* `bc4ceac`. Listed the full
`bc4ceac^..HEAD` range above instead so the widget commit isn't dropped
from the record.)

## Test results (Step 3 — integration gate)

`make check` — **PASS**. gen-message-types drift check no-op (no wire-type
changes this session), `ruff check` clean, `pyright` 0 errors/0
warnings/0 informations, `tsc --noEmit` clean.

`make test` — **PASS**. `3248 passed, 2 skipped` in ~47.5s (8 xdist
workers). Two `DeprecationWarning: forkpty() may lead to deadlocks`
warnings from `tests/test_terminals.py` / `tests/web/test_terminal_ws.py`
— pre-existing, from Python's own `pty` module on macOS, unrelated to this
session's changes (no terminal code touched).

## Manual QA

Verified live in the web UI (local web-only server, `MATTERMOST_ENABLED=false`,
`HTTP_PORT=18992`, Playwright-driven):

- Asked the agent to `widget_pin_sticky` a `progress_tracker` with all five
  statuses. Agent made the single tool call → `sticky_set` → the slot rendered
  above the chat input on the first attempt (vertex-gemini-flash, reflection
  PASS, no console errors).
- Rendering confirmed quiet and correct: collapsed summary line `1/5 · Run
  migrations` with a ▾ toggle; title "Deploy pipeline"; step rows —
  ● done (green, note "cached"), ◐ in_progress (blue), ○ pending (muted),
  ✗ failed (red, note "registry 500"), ⊘ skipped (muted, strikethrough).
  Notes render dim inline.
- Look approved as-is by Les (no glyph/color/spacing changes requested).

Auto-emit wiring (checklist / project → sticky) is covered by the unit tests
in Tasks 2–3; the live pin above exercises the identical widget + sticky
render path.
