# Spec — `progress_tracker` widget + checklist/project auto-emit (#414)

## Goal

Add a display-only `progress_tracker` widget — a multi-step status list
(pending / in_progress / done / failed / skipped) that shows the agent's
in-flight progress at a glance without the user reading prose. Then wire the
two existing workflow primitives — the **checklist tool** and the **project
skill** — to auto-emit it into the sticky slot (#419) as a side effect, so the
user gets a live progress view *for free*.

Builds directly on the sticky widget slot (#419, merged as PR #642): sticky
infrastructure (`sticky.py`, `set_sticky`/`clear_sticky`, `sticky_set`/
`sticky_clear` WS events, `<sticky-slot>` component) already exists.

## Background / current architecture

- **Widgets** are declared by `widget.json` (meta-schema in `widgets.py`) +
  `widget.js` (a Lit `dc-widget-<name>` custom element), one per directory under
  `web/static/widgets/`, auto-registered by directory scan (no central registry
  edit). `<dc-widget-host>` mounts `dc-widget-<type>` by naming convention.
  `markdown_document` already declares `["inline", "canvas", "sticky"]` — a
  close template.
- **Sticky slot** (#419): single-slot, display-only surface above the chat
  input. `sticky.set_sticky(config, conv_id, widget_type, data, emit=None)` /
  `clear_sticky(config, conv_id, emit=None)` write a `sticky.json` sidecar and
  emit `sticky_set`/`sticky_clear`. Collapsed line renders `data.summary`
  (fallback `title` → humanized type). `set_sticky` validates the widget
  declares `sticky` mode and passes schema validation; it is fail-open on emit.
- **Checklist tool** (`tools/checklist_tools.py`): always-loaded, **currently
  sync**, emits no events. Backed by `checklist.py`, which stores items as
  markdown checkboxes at `workspace/todos/{conv_id}.md`. Item shape:
  `{text, done, note}` — no `in_progress`/`failed` concept.
- **Project skill** (`skills/project/`): tools are **already async**. Its task
  list lives in `plan.md`, parsed by `plan_parser.py` into steps with status
  `pending | in_progress | done | skipped`. Meaningful step statuses exist in
  the EXECUTING phase.
- Both canvas and sticky tools reach the event emitter via
  `_emit_for_ctx(ctx)` → `ctx.manager.emit` (None when no manager).

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Widget **+ checklist auto-emit + project-skill auto-emit** | The headline is "live progress for free"; widget-only is near dead-code. |
| Widget statuses | `pending / in_progress / done / failed / skipped` (5) | `skipped` added so the project skill maps losslessly; widget is display-only so an extra status is cheap. |
| Project surface / lifecycle | Sticky slot, **EXECUTING phase only**; clear on DONE | Step statuses are only live/meaningful during execution; keeps brainstorm/plan phases uncluttered. |
| Widget visual | Build a quiet v1, tune live in QA | Matches the feel-tuning preference (human drives live controls). |
| New agent tools | **None** | Widget is emitted (checklist/project) or pinned via the existing `widget_pin_sticky`; no new invocation surface. |
| Evals | **None** | No tool description changes; auto-emit is deterministic side-effect wiring (unit-tested), not an LLM decision. |
| Config toggle for auto-emit | None (YAGNI) | Fail-open covers safety; sticky in non-web surfaces is a harmless no-op. |

## Scope

### In scope

1. **`progress_tracker` widget** — `widget.json` + `widget.js`, modes
   `["inline", "canvas", "sticky"]`, `accepts_input: false`, snapshot-rendered.
2. **Checklist auto-emit** — make `checklist_create` / `checklist_step_done` /
   `checklist_abort` tools async; emit a mapped `progress_tracker` to the sticky
   slot on each mutation; clear on all-done and on abort.
3. **Project-skill auto-emit** — emit `progress_tracker` to sticky during
   EXECUTING (on step/plan mutations and on entry to executing); clear on DONE.
4. Unit tests for all three; docs; `CLAUDE.md` key-files.

### Out of scope (future)

- `delegate_task` progress, scheduled-task in-flight status, multi-turn
  approval occupants.
- Canvas promotion for long-running projects (issue floats it; not now).
- Patching/incremental updates (v1 is snapshot-only).
- A config flag to disable auto-emit.

## Data shapes

### Widget `data_schema`

```json
{
  "type": "object",
  "required": ["steps"],
  "properties": {
    "steps": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["label", "status"],
        "properties": {
          "label":  { "type": "string" },
          "status": { "type": "string",
                      "enum": ["pending", "in_progress", "done", "failed", "skipped"] },
          "note":   { "type": "string" }
        }
      }
    },
    "title":   { "type": "string" },
    "summary": { "type": "string" }
  }
}
```

### Checklist → progress_tracker mapping

Given checklist items `{text, done, note}` in file order:
- every completed item → `status: "done"` (carry `note`);
- the **first** not-done item → `status: "in_progress"`;
- remaining not-done items → `status: "pending"`.
- `label` = item `text`.
- `title` = `"Checklist"`.
- `summary` = `"{done}/{total} · {current label}"` (in-progress present), else
  `"{done}/{total}"`.

Emit points: after `checklist_create` and after `checklist_step_done` (while
steps remain) → `set_sticky`. On all-steps-done and on `checklist_abort` →
`clear_sticky`.

### Project → progress_tracker mapping (EXECUTING only)

From `plan_parser` steps (status already `pending|in_progress|done|skipped`):
- pass status straight through (`failed` never occurs from a plan);
- `label` = step description (with number prefix, e.g. `"1. Foo"`);
- `note` = step note if present;
- `title` = project description;
- `summary` = `"{done}/{total} · {next actionable step}"`, else `"{done}/{total}"`.

Emit points (all already-async tool calls — avoids the synchronous
`_on_approve` review closures): `project_next_task` while status == EXECUTING
(fires right after entering execution, when the agent fetches its first step),
`project_update_step`, and `project_add_steps` → `set_sticky`. On transition into
DONE (in `project_task_done`) → `clear_sticky`. Guarded to only emit while
status == EXECUTING.

## Fail-open contract

Auto-emit MUST NOT break the underlying tool. Every `set_sticky` / `clear_sticky`
call from checklist/project is guarded: a non-ok result or raised exception is
logged at warning and swallowed; the checklist/project tool returns its normal
result. `set_sticky` is already fail-open on the emit callback; the sidecar
write is cheap and harmless in non-web (terminal/Mattermost) conversations.

## Acceptance criteria

- `progress_tracker` is registered by directory scan; declares modes
  `["inline", "canvas", "sticky"]` and `accepts_input: false`; its schema
  validates a payload containing all five statuses.
- Pinning it to sticky (`widget_pin_sticky` or via checklist/project) renders a
  quiet step list; the collapsed line shows `summary`.
- `checklist_create` pins a progress_tracker with the first step `in_progress`
  and the rest `pending`; each `checklist_step_done` advances the mapping;
  the last `checklist_step_done` and `checklist_abort` clear the slot.
- Project auto-emits during EXECUTING on `project_next_task` / step / plan
  mutations and clears on DONE; no emit outside EXECUTING.
- A sticky failure never breaks the checklist or project tool (fail-open).
- `make check` (lint + typecheck + check-js + message-types drift) and
  `make test` pass.

## Testing

- **Widget** (`tests/test_widgets.py`): progress_tracker registered; 3 modes;
  schema validates a 5-status payload. `make check-js` for the Lit element.
- **Checklist** (`tests/test_checklist_tools.py`, rewritten async): create →
  `set_sticky` called with first-step-in_progress mapping; step_done advances;
  final step_done → `clear_sticky`; abort → `clear_sticky`; sticky failure is
  swallowed and the tool still returns its normal text.
- **Project** (`tests/test_project_*` / skill tests): emit-during-executing on
  step update; clear-on-done; no-emit outside executing; fail-open.
- Manual web QA via a local web-only server (`MATTERMOST_ENABLED=false`, unique
  `HTTP_PORT`): live look, collapse/expand, reload recovery, checklist run
  driving the slot end-to-end.

## Docs

- Document `progress_tracker` in the widget-surface doc (alongside the sticky
  section from #419) and note the checklist/project auto-emit behavior in the
  relevant docs (checklist / project skill docs).
- `CLAUDE.md`: add the widget dir to the widgets list; note the checklist/
  project auto-emit side effect.

## References

- Widget catalog epic: #256
- Sticky widget slot: #419 (PR #642) — spec/plan at
  `docs/dev-sessions/2026-07-23-1545-sticky-widget-slot/`
- `src/decafclaw/tools/checklist_tools.py`, `src/decafclaw/checklist.py`
- `src/decafclaw/skills/project/` (`tools.py`, `state.py`, `plan_parser.py`)
- `src/decafclaw/sticky.py`, `src/decafclaw/tools/sticky_tools.py`
