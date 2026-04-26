# Spec — Widget catalog Phase 2: input widgets (#256)

Tracking issue: https://github.com/lmorchard/decafclaw/issues/256
Builds on Phase 1 (PR #360, merged): widget plumbing, registry,
`data_table`, `vault_search` retrofit.

## Session scope

This session ships **input widgets**: the agent can pause its turn,
render an interactive widget in the web UI, and resume when the user
submits. The one bundled interactive widget is **`multiple_choice`**,
exercised by a new core tool **`ask_user`** — the first real capability
for "agent stops and asks the user to pick something."

Phases 3 (canvas panel) and 4 (`code_block`, polish) are out of scope
for this session.

## Problem

Phase 1 unlocked rich display output. The agent still has no way to
pause mid-turn and collect structured input from the user. The existing
pause primitive (`EndTurnConfirm`) only offers Approve/Deny buttons.

We want the agent to be able to say "there are three reasonable paths
here — which one?" and show a radio-button picker, not a wall of text
begging the user to respond with free-form text we then have to parse.

## Goals

- The agent has a way to ask the user a multiple-choice question
  mid-turn, pausing the loop until the user responds.
- Tools return `WidgetRequest(widget_type="multiple_choice", ...,
  on_response=cb, end_turn=True)`; the loop pauses and the frontend
  renders radio buttons / checkboxes.
- The user submits; the loop wakes up; the on_response callback
  decides what synthetic user message gets injected into history so
  the LLM sees the choice on its next iteration.
- Persistence survives server restart and browser reload via the
  existing confirmation archive + restart-scan path (with a sensible
  default when the in-memory callback is gone).
- Multi-tab: first-to-submit wins; other tabs reconcile via the
  broadcast response event.
- Mattermost / terminal get the text fallback (existing `ToolResult.text`
  behavior — no change to those channels).

## Non-goals (Phase 2)

- Widgetizing `EndTurnConfirm` — **deferred to a follow-up issue**.
  The parallel `EndTurnConfirm` UI stays exactly as-is. Phase 2
  converges the pause/resume backend under `confirmations.py` so
  collapsing `EndTurnConfirm` into a widget later is a small refactor.
- Canvas panel (`canvas_set`, `canvas_update`, `dc-canvas-panel`,
  `canvas.json`) — Phase 3.
- `code_block` / `markdown_document` widgets — Phase 3/4.
- Widgets with more complex input shapes than "submit once" (chat
  inside a widget, live streaming, etc.) — future.
- Workspace-tier widgets / iframe sandbox — still #358.

## Acceptance criteria

1. The LLM can call `ask_user(prompt, options, allow_multiple=False)`;
   the web UI renders a `<dc-widget-multiple-choice>` with the options
   as radios (or checkboxes if `allow_multiple=True`) and a Submit
   button.
2. The agent turn pauses until the user submits.
3. When the user submits, the next LLM iteration sees a user message
   that reflects the choice (default wording: `"User selected: <label>"`;
   `ask_user` uses this default).
4. Mattermost and terminal show only the tool's `text` — the
   "[awaiting user choice]" marker plus whatever `display_short_text`
   the tool set. No widget rendering there.
5. Reload a conversation that contains an answered `ask_user`: the
   `multiple_choice` widget re-renders with the chosen option visible,
   all controls disabled, the submit button relabeled. The injected
   user message is in the transcript as expected.
6. Server restart mid-pause: on next startup, the pending widget
   request is recovered from the archive. If the user submits after
   restart, a **default handler** injects `"User responded with: <data>"`
   as the user message (the in-memory `on_response` callback is gone
   but the loop can still resume gracefully).
7. Multi-tab: two tabs on the same conversation both show the widget.
   Submitting from one tab disables the widget in the other tab via
   the broadcast confirmation response event. Second submit attempt
   is a no-op (confirmation already resolved).
8. A tool sets `widget=WidgetRequest(accepts_input=True)` without
   `end_turn=True` → warning logged, widget stripped, text flows
   normally. A tool sets BOTH an input widget AND an
   `end_turn=EndTurnConfirm(...)` → widget wins, `EndTurnConfirm`
   dropped with warning.
9. Lint + typecheck + tests clean.

## Architecture

### Backend convergence: pause/resume rides on the confirmation infra

The `confirmations.py` module already provides:

- Persistent JSONL request + response records
  (`role: "confirmation_request"` / `"confirmation_response"`), keyed
  by `tool_call_id` + `confirmation_id`.
- A per-conversation pause via `request_confirmation(...)` that awaits
  an event-bus response.
- Startup-scan recovery of pending requests after server restart.
- Typed `ConfirmationAction` enum + a `ConfirmationRegistry` of
  handlers that dispatch on approve/deny.

Phase 2 adds **small extensions**:

1. **New `ConfirmationAction.WIDGET_RESPONSE`.** Action data carries
   the widget type + the serialized widget request (widget_type,
   target, data).
2. **`ConfirmationResponse.data: dict`** — a new free-form response
   field, populated for widget responses (the `{selected: "a"}` or
   `{selected: ["a", "b"]}` payload). Confirmation handlers for
   existing actions ignore it; the new widget handler reads it.
3. **`ConfirmationRequest.timeout: float | None = 300.0`** —
   `asyncio.wait_for(..., timeout=None)` already disables the deadline
   natively, so we widen the type and widget requests pass
   `timeout=None`. Existing confirmations keep their 5-min default
   with no code change.
4. **Added `respond_to_confirmation(..., data: dict | None = None)`**
   on the manager so WS handlers can pass the widget response payload
   through. The emitted `confirmation_response` event carries `data`
   when present.
5. **A default `WIDGET_RESPONSE` handler registered at startup.** Used
   by the recovery path (`recover_confirmation`) when the agent loop
   has died and a submit comes in. Handler writes a synthetic user
   message directly to the archive
   (`append_message(config, conv_id, {role: "user", content: "User
   responded with: <data>"})`). Returns
   `{"inject_message": "...", "continue_loop": False}` but the
   recovery path doesn't act on `continue_loop` beyond its own logic
   — the archive sync is the important bit.

### Live vs recovery paths (asymmetric — worth being explicit)

The confirmation infra has two convergent user-input paths but the
code for handling the response is DIFFERENT between them:

- **Live path:** agent-loop calls `request_confirmation` → waits on
  event → response arrives → loop directly reads
  `state.confirmation_response` and continues. No handler dispatch.
- **Recovery path:** a response arrives when no loop is waiting →
  `recover_confirmation` dispatches to the registered handler →
  handler does its thing.

Existing confirmations (`EndTurnConfirm`, shell approval, etc.) rely
on `approved: bool` alone in the live path; the recovery handler is a
defensive fallback.

For widgets, the live path is where the `on_response` callback lives:
the agent-loop branch for widget-input pauses looks up the callback
by `tool_call_id` in the in-memory map, calls it with `response.data`,
gets the inject string, and appends a synthetic user message to both
in-memory `history` and the archive. The recovery path's registered
`WIDGET_RESPONSE` handler does the archive append directly (there's
no `history` to sync, since no loop is running).

Both paths must produce the same archive end-state: a user message
whose content is the inject-string. Reload + next turn then reads
that message via normal history flow.

### Agent loop changes

In `_execute_single_tool` / `_resolve_widget`:

- After validating the widget payload (Phase 1), check if
  `widget.descriptor.accepts_input == True`.
- **Enforcement rules:**
  1. Input widget with no `end_turn=True` and no `EndTurnConfirm` →
     log warning, strip the widget, continue (text flows normally).
  2. Input widget with `end_turn=EndTurnConfirm(...)` → log warning,
     overwrite `end_turn=True` (widget-pause wins; no buttons).
  3. Display widget (`accepts_input=False`) + any `end_turn` — no
     conflict, no changes.
- If the widget is kept and is input-type, register the `on_response`
  callback (if set) in an in-memory map keyed by `tool_call_id`.
- Convert `result.end_turn = True` into a new `WidgetInputPause`
  sentinel dataclass: `WidgetInputPause(tool_call_id, widget_payload)`.
  Stored/returned the same way `EndTurnConfirm` is today (the
  `end_turn` flag on ToolResult + the `end_turn_signal` the tool-call
  batch carries up).
- Emit the `tool_end` event with the widget (as Phase 1).

In the outer agent loop, extend the existing `end_turn_signal` switch:

- `isinstance(end_turn_signal, EndTurnConfirm)` → existing buttons
  path (unchanged).
- **New:** `isinstance(end_turn_signal, WidgetInputPause)` → build
  `ConfirmationRequest(action_type=WIDGET_RESPONSE,
  action_data={widget_type, target, data}, tool_call_id=...,
  timeout=None)` and call `ctx.request_confirmation(request)`
  directly. This bypasses the `tools/confirmation.py` helper (which
  builds action_data from a fixed set of tool-specific extras) —
  widget requests carry arbitrary widget data and don't fit that
  helper's shape. The manager sets up `ctx.request_confirmation`
  during context setup (`conversation_manager.py:615`), so this call
  is already available.

  On response, look up the callback in the in-memory map, call it
  with `response.data`, get the inject-string, append as a user
  message to history + archive. Clear the in-memory callback entry.
  Continue loop.

The LLM's *last message* on the iteration before the pause is a tool
result with "[awaiting user choice]" text. When the loop resumes, the
injected synthetic user message follows that tool result, and the
next LLM call sees the question (via the tool's text) and the answer
(via the synthetic user message) in proper sequence.

### `on_response` callback contract

Signature: `on_response(response_data: dict) -> str` — sync for now.
Returns the string to inject into history as a user message.

If the tool wants structured state change, it can close over local
variables and do work inside the callback; the return value is only
the injected-into-LLM-visible text.

Restart-recovery default (no callback registered): inject
`f"User responded with: {response_data}"` as a user message.

### The `ask_user` tool

New core tool in `src/decafclaw/tools/core.py` (or a new
`user_input.py` if it grows). Always-loaded is overkill — set
`priority=low` so it's fetched on demand when relevant via tool_search
or preempt-match.

Signature (Python):

```python
async def ask_user(
    ctx,
    prompt: str,
    options: list[str | dict],
    allow_multiple: bool = False,
) -> ToolResult:
    ...
```

- `options` accepts bare strings (treated as `{value: s, label: s}`)
  or dicts `{value, label, description?}`.
- Returns `ToolResult` with:
  - `text`: `"[awaiting user response: <prompt>]"` (placeholder so the
    archive is readable; the injected response will follow later)
  - `display_short_text`: e.g. `"ask: pick one of N"`
  - `widget`: `WidgetRequest(widget_type="multiple_choice", data={...},
    on_response=_default_cb, end_turn=True)`
- Default `on_response`: formats selection as
  `"User selected: <label>"` (or `"User selected: a, b"` for multi).

### Tool description (LLM-facing)

> `ask_user` — Pause the turn and ask the user to choose from a list
> of options. Use ONLY when the right answer is genuinely ambiguous
> from context and you cannot make a reasonable choice on your own.
> Prefer to act on your best judgment; calling this tool is costly —
> it interrupts the user's flow. Reserve for decisions the user would
> want to weigh in on (e.g., "which of these three files should I
> edit?", "publish or save as draft?").

Wording matters — we want the LLM to reach for this sparingly. Eval
loop can validate.

### Bundled `multiple_choice` widget

New directory: `src/decafclaw/web/static/widgets/multiple_choice/`

**`widget.json`:**

- `name: "multiple_choice"`
- `description`: "Ask the user to choose one (or several) option(s)
  from a list. Radio buttons for single, checkboxes for multi."
- `modes: ["inline"]` — no canvas mode in Phase 2
- `accepts_input: true`
- `data_schema`:
  - required: `prompt` (string), `options` (array of `{value, label,
    description?}` objects)
  - optional: `allow_multiple` (boolean, default false)

**`widget.js`** — `<dc-widget-multiple-choice>`:

Props:

- `data`: `{prompt, options, allow_multiple}`
- `submitted`: boolean — when true, controls are disabled and the
  submit button is relabeled
- `response`: `{selected}` — for the reload/submitted case, shows
  which option(s) were picked

Behavior:

- Renders `prompt` as a small text header.
- Radios (allow_multiple=false) or checkboxes (true) for each option.
  Description shown as muted secondary text under the label, when
  present.
- Submit button disabled until at least one option is selected.
- On submit: dispatch `widget-response` CustomEvent with
  `detail = {selected: "..."}` (string for single; array for multi).
  `{bubbles: true, composed: true}` so it bubbles past `dc-widget-host`
  to the parent tool-message.
- Post-submit state: same DOM, but controls `disabled`, submit button
  relabeled "Submitted" and disabled, selected option visually
  indicated (bold + checked).
- Reload: parent passes `submitted=true` + `response={selected}` → same
  visual as post-submit.

### Frontend: bridge `widget-response` → WebSocket

Currently `dc-widget-host` doesn't touch WebSocket. The response path
adds:

- A listener on tool-message (or chat-view, whichever is cleaner) that
  catches `widget-response` events bubbling up, packages the detail as
  a `widget_response` WebSocket message with `tool_call_id` + `data`
  detail, and sends it over the existing WS.
- Receiving side: after the backend resolves and publishes the
  `confirmation_response` event, the existing `tool-status-store`
  gets that event (it already handles confirmation responses for
  EndTurnConfirm-style buttons); it needs one small extension to also
  forward the response `data` to the matching widget so it flips to
  `submitted=true, response=...`.
- Live case and reload case converge on: tool-message examines
  history, finds the matching `confirmation_response` for this
  `tool_call_id`, pulls `data`, and renders widget with
  `submitted=true, response=data`.

### WebSocket message names

Mirrors the existing `confirm_response` pattern, just for widgets:

- **Frontend → backend:** new incoming type `widget_response` with
  fields `{conv_id, confirmation_id, tool_call_id, data}`. Handler
  registered in the `websocket.py` handler map alongside
  `confirm_response`.
- **Backend handler** calls
  `manager.respond_to_confirmation(conv_id, confirmation_id,
  approved=True, data=msg["data"])`. `approved=True` because a widget
  submission isn't a yes/no decision; a submit happens, that's it.
- **Backend → frontend:** when a widget pause starts, the manager
  emits a `confirmation_request` event with
  `action_type="widget_response"` and `action_data={widget_type,
  target, data}`. The existing frontend `#pendingConfirms` state
  captures it (so the frontend knows the confirmation_id for this
  `tool_call_id`). When the widget submits, the frontend looks up the
  matching confirmation by `tool_call_id` and sends
  `widget_response` with the `confirmation_id`.
- **Confirmation UI filtering:** `confirm-view.js` renders
  approve/deny buttons for every pending confirm. We need to filter
  out confirms with `action_type="widget_response"` — those are
  rendered inline inside the tool message (by the widget itself). Add
  a filter: `pendingConfirms.filter(c => c.action_type !==
  "widget_response")` at the render site.
- **Live response emit:** when the manager resolves, it emits a
  `confirmation_response` event with the response fields. This event
  needs a new `data` field for the widget selection, so the frontend
  can update the widget's post-submit state.

### Archive shapes

**Request side:** The same `role: "confirmation_request"` record Phase
1 uses. For a widget request, `action_type = "widget_response"` and
`action_data = {widget_type, target, data}` — everything the frontend
needs to re-render the widget on reload.

The existing `tool` role record (Phase 1) already carries the `widget`
payload. The `confirmation_request` record is redundant with the `tool`
record's widget for the rendering case, but it's what the restart-scan
path picks up to recover. Having both:

- `tool` record: what the live Phase 1 pipeline uses to render the
  widget (frontend already wired).
- `confirmation_request` record: what the restart-scan + pause
  mechanism uses.

That redundancy is fine — they're independent concerns.

**Response side:** `role: "confirmation_response"` with `data = {...}`
(the widget's selection). The `data` field is new — Phase 2 adds it.

### Reload UX

On conversation reload, the frontend's message-store merges pairs:

- Phase 1: assistant `tool_calls` + matching `tool` records → tool
  message with widget.
- Phase 2: additionally, scan forward from the `tool` record for a
  matching `confirmation_response` (by `tool_call_id`). If found with
  `data`, pass `submitted=true, response=data` to the tool-message.
  Widget renders in post-submit state.

For a widget that was never answered (server died mid-pause and was
never resumed): `confirmation_request` exists, no
`confirmation_response`. On reload, the widget is live — user can
still submit. Backend startup-scan will have recovered the pending
request so the submission still flows through the confirmation infra
(just without the in-memory callback, hitting the default handler).

## Error handling / edge cases

- **Input widget without `end_turn=True`** → warning, strip widget,
  text only.
- **Input widget + `EndTurnConfirm`** → warning, widget wins,
  `EndTurnConfirm` dropped.
- **Input widget + `end_turn=True`** (happy path) → pause, await
  response, resume.
- **Widget type `multiple_choice` marked `accepts_input=true` but tool
  set `end_turn=False`** → same as "input widget without end_turn":
  strip, warn.
- **User disconnects before submitting** → pending confirmation stays
  in the archive. On next connect, widget renders as live (not
  submitted) — they can resume. Server restart is equivalent.
- **Tool returns widget with `widget_type` that has `accepts_input=true`
  but provides a callback that raises on call** → exception logged;
  fall through to the default handler ("User responded with: <data>").
  Do NOT fail the turn.
- **Second tab submits after first already resolved** → backend's
  confirmation-already-resolved path (same as EndTurnConfirm today).
  Second submit is a no-op; second tab's UI reconciles via the
  broadcast response event.
- **Archive has `confirmation_response` but registry lacks
  `WIDGET_RESPONSE` action (e.g., old archive from before Phase 2)** —
  not possible: the `WIDGET_RESPONSE` enum value is added by Phase 2
  and older archives wouldn't reference it. If a newer archive gets
  loaded by an older binary, the enum parse fails in
  `ConfirmationRequest.from_archive_message`; we'd handle that as a
  generic "unknown action type — skip" (matches existing behavior).
- **24h staleness on startup recovery.** Existing `_scan_archive_for_pending`
  skips confirmations older than 24h. Widgets inherit this — a user
  returning to a week-old pending widget won't have a registered
  pending confirmation, so a submit attempt would error out with "No
  pending confirmation for conv X." Acceptable for Phase 2; we can
  relax this later if it bites. The widget itself still renders from
  the archive record, just can't be submitted.
- **`options` list is empty** → agent-loop validation via `jsonschema`
  (data_schema requires `options` as an array; empty array is allowed
  by default JSON schema but the tool-level enforcement can tighten:
  `ask_user` returns an error if options is empty). Tool-level check
  in `ask_user` for sanity; widget renders nothing useful with empty
  options but doesn't crash.
- **`options` with duplicate values** → not forbidden by schema. UI
  would show two items with the same value; first-match wins on
  submit. Fine, tool-author problem.

## File inventory

New:

- `src/decafclaw/web/static/widgets/multiple_choice/widget.json`
- `src/decafclaw/web/static/widgets/multiple_choice/widget.js`
- `tests/test_widgets_input.py` — pause/resume, callback, default
  handler, enforcement rules.
- `tests/test_ask_user.py` — the new tool, happy path + option shapes.

Modified:

- `src/decafclaw/confirmations.py` — add `WIDGET_RESPONSE` action,
  extend `ConfirmationResponse` with `data` field, timeout=0 sentinel
  handling.
- `src/decafclaw/agent.py` — enforcement rules in `_resolve_widget`;
  pause-on-input-widget branch in the agent-loop end_turn switch;
  in-memory callback map; registry handler for `WIDGET_RESPONSE`.
- `src/decafclaw/tools/core.py` (or new `ask_user.py`) — the tool.
- `src/decafclaw/tools/__init__.py` — tool registration.
- `src/decafclaw/web/websocket.py` — `widget_response` incoming
  message handler; `confirmation_response` outgoing carries `data`.
- `src/decafclaw/web/static/components/messages/tool-message.js` —
  forward `submitted` + `response` to `dc-widget-host`; listen for
  `widget-response` to fire WS message.
- `src/decafclaw/web/static/components/widgets/widget-host.js` —
  pass `submitted` + `response` to the child widget component.
- `src/decafclaw/web/static/lib/tool-status-store.js` — widget
  response handling (update tool message with `submitted=true` +
  `response=data`).
- `src/decafclaw/web/static/lib/message-store.js` — reload-merge pass
  to pair widgets with their `confirmation_response` + attach
  submitted/response props.
- `src/decafclaw/web/static/styles/widgets.css` — styles for the
  `multiple_choice` widget.
- `docs/widgets.md` — Phase 2 addendum: `multiple_choice`, input
  widget contract, `ask_user` example.
- `CLAUDE.md` — key files note mentioning `ask_user` and the
  input-widget path.

## Open follow-ups tracked elsewhere

- **Collapse `EndTurnConfirm` into a widget** with multi-channel
  (Mattermost buttons) adapter — to be filed as a new follow-up
  issue during execution. Pre-req: Phase 2 lands.
- **Workspace-tier widgets + iframe sandbox** — #358 (still).
- **Per-tool renderer registry (#151)** — still held open; reassess
  after Phase 2.
