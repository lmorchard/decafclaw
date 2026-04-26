# Plan — Widget catalog Phase 2 (#256)

Goal: implement input widgets per `spec.md`. Each step ends green
(`make check && make test`) with one commit. Branch is
`widgets-phase-2-256`; PR at the end.

## Ordering rationale

Backend convergence first (confirmation infra extensions, agent-loop
pause), then the `ask_user` tool to exercise it with unit tests, then
frontend wiring (widget-host reload props, `multiple_choice`
component, tool-message reload merge, WS bridge), then dev + docs.
This order is test-driven throughout — the first several steps don't
need the UI to verify correctness.

Rough size: ~11 steps, ~15 files touched.

## Steps

### Step 1 — Confirmation infra extensions

**Files:**
- `src/decafclaw/confirmations.py`:
  - Add `ConfirmationAction.WIDGET_RESPONSE = "widget_response"`.
  - Widen `ConfirmationRequest.timeout` to `float | None = 300.0`.
  - Add `ConfirmationResponse.data: dict = field(default_factory=dict)`.
    Serialize in `to_archive_message` when non-empty; deserialize via
    `from_archive_message`.
- `src/decafclaw/conversation_manager.py`:
  - `request_confirmation`: switch `asyncio.wait_for` call to support
    `timeout=None` (already works natively — just pass through).
  - `respond_to_confirmation`: new kwarg `data: dict | None = None`,
    plumbed into the `ConfirmationResponse` construction and the
    emitted event payload.
- `tests/test_confirmations.py` (extend) — serialization round-trip
  for `data` field; timeout=None on request dataclass.
- `tests/test_conversation_manager.py` (or similar) — extend
  existing confirm tests: response with `data` field emits and
  persists; timeout=None doesn't raise.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): confirmation infra extensions for widget responses`

### Step 2 — `WidgetInputPause` sentinel + default handler

**Files:**
- `src/decafclaw/media.py` (or `agent.py` — wherever `EndTurnConfirm`
  lives) — add `WidgetInputPause` dataclass:
  ```python
  @dataclass
  class WidgetInputPause:
      tool_call_id: str
      widget_payload: dict  # {widget_type, target, data}
      on_response: Callable[[dict], str] | None = None
  ```
- `src/decafclaw/agent.py` (or new `widget_input.py`) — default
  `WIDGET_RESPONSE` confirmation handler. On recovery (ctx=None),
  writes a synthetic user message directly to the archive with
  `"User responded with: {data}"` formatting. On live path, wouldn't
  normally fire since the agent loop handles live responses directly
  — but safe to keep as a fallback.
- Register the handler at the two `ConversationManager(...)`
  construction sites — `runner.py:56` and `interactive_terminal.py:60`.
  There are currently no registered confirmation handlers; this is
  the first. Expose a `register_widget_handler(registry)` function
  from the new widget-input module; both call-sites invoke it right
  after constructing the manager:
  `register_widget_handler(manager.confirmation_registry)`.
- `tests/test_widget_input_pause.py` (new):
  - `WidgetInputPause` construction happy path.
  - Default handler on recovery writes a user message to the archive.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): WidgetInputPause sentinel and default recovery handler`

### Step 3 — Agent-loop branch: input-widget enforcement + pause

**Files:**
- `src/decafclaw/agent.py`:
  - Extend `_resolve_widget` with input-widget enforcement:
    - If `desc.accepts_input=True` and `end_turn is False` → strip
      widget with warning.
    - If `desc.accepts_input=True` and `end_turn=EndTurnConfirm(...)`
      → strip `end_turn` (keep widget, set `end_turn=True`) with
      warning.
    - If `desc.accepts_input=True` and `end_turn=True` → happy path;
      promote to `WidgetInputPause(tool_call_id, widget_payload,
      on_response)`.
  - `_execute_single_tool` / `_execute_tool_calls` returns
    `WidgetInputPause` in the same `end_turn` slot where
    `EndTurnConfirm` lives today; the prioritization logic already
    knows to prefer richer signals — extend it so `WidgetInputPause`
    sits alongside `EndTurnConfirm` (only one can win per batch; if
    both somehow occur, `WidgetInputPause` wins).
  - In the outer loop's switch block, add:
    ```python
    elif isinstance(end_turn_signal, WidgetInputPause):
        inject_message = await _handle_widget_input_pause(
            ctx, end_turn_signal, callbacks_map)
        if inject_message:
            synthetic = {"role": "user",
                         "source": "widget_response",
                         "content": inject_message}
            _archive(ctx, synthetic)
            history.append(synthetic)
            # continue the loop
    ```
  - New `_handle_widget_input_pause(ctx, signal, callbacks_map)`:
    builds `ConfirmationRequest`, awaits via `ctx.request_confirmation`,
    looks up callback by `tool_call_id`, returns inject string.
- In-memory callbacks registry: a module-level (or Context-carried)
  `dict[tool_call_id, Callable]`. Registered in `_resolve_widget`,
  cleared after response. Not serialized.
- `tests/test_agent_widgets.py` (extend) — enforcement rules
  exercised with stub widgets (accepts_input descriptors):
  - Input widget + `end_turn=True` → pause signal emitted.
  - Input widget + `end_turn=False` → widget stripped, warning logged.
  - Input widget + `end_turn=EndTurnConfirm` → widget kept,
    `EndTurnConfirm` dropped with warning.
  - Display widget (accepts_input=False) is unaffected.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): agent-loop pauses on input widgets and resumes with inject`

### Step 4 — End-to-end pause/resume integration test

Ensures the pieces from steps 1-3 talk correctly. Uses the
ConversationManager + a stub widget that sets `accepts_input=True`
with a callback.

**Files:**
- `tests/test_widgets_input_flow.py` (new):
  - Set up ConversationManager + stub widget registry with an
    `accepts_input=true` widget.
  - Monkeypatch `execute_tool` to return a `ToolResult` with a
    `WidgetRequest` and `end_turn=True`.
  - Invoke a simulated turn (similar to existing manager tests).
  - Assert: agent loop pauses (confirmation_request event emitted).
  - Resolve the confirmation manually via
    `respond_to_confirmation(conv_id, confirmation_id, approved=True,
    data={"selected": "a"})`.
  - Assert: agent loop resumed; archive contains a synthetic
    `role: "user"` message with inject-string; the widget's
    callback was called exactly once; pending_confirmation cleared.
- Also test the callback-missing case: drop the callback from the
  in-memory map before resolving → confirm the default handler's
  path kicks in and archives a `"User responded with: ..."` message.

**Verification:** `make check && make test`.

**Commit:** `test(widgets): end-to-end input widget pause/resume integration`

### Step 5 — `ask_user` tool

**Files:**
- `src/decafclaw/tools/ask_user.py` (new) — the tool function,
  options normalization (strings → `{value, label}` dicts), default
  `on_response` callback producing `"User selected: X"` /
  `"User selected: X, Y"` for multi.
- `src/decafclaw/tools/__init__.py` — register `ask_user` in
  `TOOL_DEFINITIONS` with `priority="low"`. Tool description text
  per the spec: used sparingly, only when genuinely ambiguous.
- `tests/test_ask_user.py` (new):
  - Happy path: returns ToolResult with widget + end_turn=True.
  - Option normalization: strings, dicts, mixed.
  - `allow_multiple=True` → widget data reflects it.
  - Empty `options` → returns a descriptive error result (no widget).
  - Default `on_response` builds the expected inject-string for
    single and multi.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): ask_user tool for multiple-choice prompts`

### Step 6 — WebSocket `widget_response` incoming handler

**Files:**
- `src/decafclaw/web/websocket.py`:
  - New `_handle_widget_response(ws_send, index, username, msg, state)`.
    Fields expected: `conv_id, confirmation_id, tool_call_id, data`.
    Calls `manager.respond_to_confirmation(conv_id, confirmation_id,
    approved=True, data=msg["data"])`.
  - Register in the handler map alongside `confirm_response`.
- `tests/test_web_widgets_input.py` (new) OR extend
  `test_web_widgets.py`:
  - Set up a pending widget confirmation via manager; send
    `widget_response` WS message; assert `respond_to_confirmation`
    was called with the right args; assert the confirmation_response
    event carries `data`.

**Verification:** `make check && make test`.

**Commit:** `feat(widgets): WS widget_response handler routes to confirmation infra`

### Step 7 — Bundled `multiple_choice` widget

**Files:**
- `src/decafclaw/web/static/widgets/multiple_choice/widget.json`:
  - data_schema requires `prompt` (string), `options` (array of
    `{value, label, description?}`), optional `allow_multiple`.
  - `accepts_input: true`, `modes: ["inline"]`.
- `src/decafclaw/web/static/widgets/multiple_choice/widget.js` —
  `<dc-widget-multiple-choice>`:
  - Props: `data`, `submitted`, `response`.
  - Radios (single) or checkboxes (multi).
  - Descriptions render as muted secondary text under the label.
  - Submit button disabled until a selection exists.
  - On submit: dispatch `widget-response` CustomEvent with
    `{bubbles: true, composed: true}` and
    `detail = {selected: "<value>"}` (string for single; array for
    multi).
  - Post-submit state: controls disabled; submit labeled "Submitted"
    and disabled; selected option marked visually.
- `src/decafclaw/web/static/styles/widgets.css` — `multiple_choice`
  styles (radio/checkbox alignment, description text, submit button).

**Verification:** `make check-js`. Also extend
`tests/test_widgets.py` to assert a fresh registry scan finds
`multiple_choice` with the expected schema fields.

**Commit:** `feat(widgets): bundled multiple_choice widget`

### Step 8 — Frontend: widget-host forwards submitted/response + tool-message reload merge

**Files:**
- `src/decafclaw/web/static/components/widgets/widget-host.js`:
  - Add props `submitted` (boolean) and `response` (object).
  - Forward both to the mounted child widget via property assignment
    (along with `data`).
- `src/decafclaw/web/static/components/messages/tool-message.js`:
  - Accept `submitted` + `response` props.
  - Pass through to `dc-widget-host`.
- `src/decafclaw/web/static/components/chat-message.js` — add
  `submitted`, `response` to the forwarded `<tool-message>` props.
- `src/decafclaw/web/static/components/chat-view.js` — pass
  `submitted` / `response` from the merged message.
- `src/decafclaw/web/static/lib/message-store.js`:
  - Extend `#mergeToolMessages` to scan for a `confirmation_response`
    record matching the tool_call_id whenever the tool message has a
    widget. If found with `data`, set `submitted=true, response=data`
    on the synthetic tool message.
  - Lenient: if response exists without `data` (e.g., old records),
    just set `submitted=true` with `response={}`.
- `src/decafclaw/web/static/lib/conversation-store.js` — ChatMessage
  typedef gets `submitted?: boolean`, `response?: object`.

**Verification:** `make check-js`.

**Commit:** `feat(web): reload-merge widget responses and forward submitted state`

### Step 9 — Frontend: live WS bridge for widget-response

**Files:**
- `src/decafclaw/web/static/lib/tool-status-store.js`:
  - Handler for `widget-response` CustomEvent bubbling up from the
    chat region. Builds and sends the `widget_response` WebSocket
    message with `{conv_id, confirmation_id, tool_call_id, data}`.
  - The confirmation_id comes from `#pendingConfirms` lookup by
    `tool_call_id`.
  - Existing `confirmation_response` handler needs an extension:
    when the response has `data`, update the tool message's
    `submitted=true, response=data` so the widget flips.
- `src/decafclaw/web/static/components/confirm-view.js` — filter
  out pending confirms with `action_type === "widget_response"` so
  they don't render as approve/deny buttons.
- `src/decafclaw/web/static/components/chat-view.js` (or chat
  container) — add a single `@widget-response` listener that
  delegates to the store.

**Verification:** `make check-js`.

**Commit:** `feat(web): widget-response WS bridge and post-submit state flip`

### Step 10 — Full check + live smoke test gate

`make check && make test` one more time to confirm no regressions
across the backend + frontend.

**Live smoke (Les, post-push):**
- Invoke `ask_user` in a web-UI conversation. Confirm the widget
  renders (radios or checkboxes depending on `allow_multiple`).
- Submit. Confirm the widget disables and the next LLM response sees
  the choice ("User selected: X").
- Reload the conversation. Confirm the widget re-renders in its
  post-submit state.
- Open a second tab on the same conversation BEFORE submitting.
  Submit from tab A. Confirm tab B flips to post-submit state.

### Step 11 — Docs + follow-up issue

**Files:**
- `docs/widgets.md` — Phase 2 addendum: input-widget contract,
  `multiple_choice`, `ask_user` example, the "agent can stop and ask"
  affordance.
- `CLAUDE.md` — note input widget path in key files (ask_user tool,
  widget-input agent-loop branch).
- Log follow-up issue: "collapse EndTurnConfirm into a widget with
  Mattermost adapter" — via `gh issue create`. Prereq: this PR
  lands. References #256 and the EndTurnConfirm analysis from this
  session.

**Verification:** `make check && make test`. Visual check of
`docs/widgets.md`.

**Commit:** `docs(widgets): input widget contract + ask_user example`

### Step 12 — Final review + PR prep

- `git fetch origin && git rebase origin/main` — re-run lint + tests
  after.
- Update `notes.md` with observations from execution.
- Push branch, open PR against main, request Copilot review.
- Move #256 to "In review" on the project board if possible.

## Verification gates (per step)

1. `make lint` — ruff clean.
2. `make typecheck` — pyright clean.
3. `make check-js` — tsc clean (if any JS touched).
4. `make test` — pytest clean.
5. `git add {specific paths} && git commit` with
   `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
   trailer.

## Risk log

- **In-memory callback map lifecycle.** If we leak entries (e.g., the
  loop crashes after `_resolve_widget` registered a callback but
  before the await), the map grows unbounded. Mitigation: wrap the
  pause in `try/finally` that pops the entry regardless of outcome.
- **Confirmation_id plumbing on reload.** The archive persists the
  `confirmation_request` with its `confirmation_id`. The frontend's
  `#pendingConfirms` state is populated on reload via the
  `conv_history.pending_confirmation` seed (line 531 in
  `conversation-store.js`). That seed currently assumes a single
  confirm but we might have a widget confirm + nothing else — verify
  the seed path carries widget-typed confirms too.
- **`action_data` serialization.** Widget payload could contain
  arbitrary JSON — options list, prompt strings with special chars.
  `jsonschema` already validated before this point so it's trusted;
  archive JSONL just serializes and reads back, so this is fine.
  Worth having a test case with Unicode / quotes in the prompt.
- **Live-test difficulty.** Same as Phase 1: I can't hit Les's dev
  instance; live smoke falls to review time.
- **`_scan_archive_for_pending` scans the tail (64KB) of the
  archive.** For conversations with lots of chatter between
  confirmation_request and the end, the request could be off the
  scanned window. Inherits existing behavior — not a regression —
  but worth noting if it bites us.

## What's deliberately not here

- Collapsing `EndTurnConfirm` into a widget — follow-up issue.
- Canvas panel / Phase 3 work.
- Other widget types (`code_block`, `markdown_document`).
- Widget hot-reload.
- Making `on_response` async. Sync-only for v2; can widen later.
- Relaxing the 24h staleness check on startup recovery.
