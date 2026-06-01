# Spec — Tabstack interactive automate + SDK refresh

## Problem

The `tabstack` skill targets `tabstack>=2.3.0`, but the SDK has advanced two minor
versions and our usage is now stale and partly broken:

- **2.4.0** added the `automate_input` endpoint — the interactive human-in-the-loop
  form-data handshake.
- **2.6.0** replaced the `/automate` SSE root with a fully-typed discriminated-union
  event model (`AutomateEvent` = union of envelopes discriminated on `event`, payload
  in `.data`).

Our `tools.py` reads `event.message`, `event.finalAnswer`, `event.report`,
`event.metadata` at the **top level** of each event via `getattr` poking
(`_get_field` / `_get_report`). In both 2.3.0 (untyped `event.data` dict) and 2.6.1
(typed `event.data`) the payload lives under `.data`, so this poking returns `None` —
progress messages and final-answer extraction don't line up with the real shape. It's
also exactly the "getattr-fallback maintenance trap" the project conventions warn
against.

Observed symptom: an agent using `tabstack_automate` against a form-bearing page
(Google Maps transit) hit "request_user_data was cancelled". That is the tabstack
server cancelling its own form-data request because nothing answered it in time — we
never enabled `interactive=True` and never wired up `automate_input`, so any form-fill
the server attempts dies after the 2-minute input window.

## Goals

1. **Refresh the SDK** to `>=2.6.1` and rewrite event handling against the typed
   discriminated-union model. Read payloads from `event.data` keyed off the `event`
   discriminator instead of `getattr` poking. This alone fixes the current breakage.
2. **Support interactive form-fill with agent-supplied data.** The agent passes a
   `data` blob (personal info gathered beforehand) plus `interactive=True`; when the
   stream emits an `interactive:form_data:request` (or `:error`) event, the tool
   matches requested fields against `data`, submits matched values via `automate_input`,
   and cancels gracefully for missing required fields — reporting which fields were
   missing so the agent can ask the user and retry.
3. **Gate form submission behind a confirmation.** Submitting personal data into a web
   form is an outward-facing, sensitive action; route it through `request_confirmation`
   (the same inline-blocking pattern `send_email` uses) showing the form description,
   page URL, and the field→value pairs about to be submitted.

## Non-goals

- **Live human-in-the-loop** (pause the turn, show the user a form widget, resume the
  live SSE stream). Infeasible in DecafClaw's turn model: a tool returns a single
  result, and `WidgetInputPause` *ends* the turn and resumes on a *new* turn — by then
  the SSE stream is dead and the 2-minute `request_id` has expired. Keeping the tabstack
  stream alive across a turn pause is background-task machinery and out of scope.
- Changing `extract`/`generate` behavior beyond what the SDK bump requires.

## Design

### Event handling (both automate and research)

The new events are typed pydantic models with an `event` string discriminator and a
typed `data` payload. Switch on `event.event`:

- **Progress** (publish `tool_status`): `agent:status` (`.data.message`),
  `agent:reasoned` (`.data.reasoning`), `agent:action` (`.data.action`),
  `browser:navigated` / `browser:action_started` (human-readable), `done`/`error`
  (`.data.message`).
- **Final answer** (automate): first non-empty `.data.final_answer` seen across
  `complete`, `task:completed`, `task:validated`, `task:aborted`.
- **Final answer** (research): `ResearchEvent` — report on the final/complete event
  (mirror the automate approach against that union).
- **Errors**: `error` / `ai:generation:error` → surface `.data.message`.

Both `AutomateEvent` and `ResearchEvent` use the same envelope (`event` discriminator +
typed `.data`). Research final answer = the `complete` event's `.data.report`; progress
= each event's `.data.message`. Replace `_get_field` / `_get_report` getattr poking with
discriminator-keyed access. A tiny `_event_payload(event)` helper returning `event.data`
is fine; the dispatch keys off `event.event`.

### Extract / generate response-shape fix (also broken)

In 2.6.1 `extract.json()` and `generate.json()` return **plain dicts**
(`ExtractJsonResponse` / `GenerateJsonResponse` are now `Dict[str, object]`), not
objects with a `.data` attribute. The current `result.data` access (carrying a
`# type: ignore[attr-defined]`) is broken — use `result` directly. `extract.markdown()`
still returns `ExtractMarkdownResponse` with `.content` (unchanged).

### Interactive form-fill

`tool_tabstack_automate(ctx, task, url=None, data=None, interactive=False)`:

- `data`: dict of personal/contextual info for form filling. Passed through to the SDK
  `data=` param (tabstack's own agent uses it as context) **and** used by our handler to
  answer `form_data:request` events.
- `interactive`: when `True`, pass `interactive=True` to the SDK and handle
  `interactive:form_data:request` / `interactive:form_data:error` events:
  1. For each requested field (`ref`, `label`, `field_type`, `required`, `options`),
     match against `data` by case-insensitive key/label correspondence.
  2. Build the `fields=[{ref, value}, ...]` list from matches.
  3. If any **required** field is unmatched → cancel via
     `automate_input(request_id, cancelled=True)`, record the missing field labels, and
     let the run end; the tool result reports the missing fields back to the agent.
  4. Otherwise route through the **confirmation gate** (`request_confirmation`,
     `tool_name="tabstack_automate"`) showing `form_description`, `page_url`, and the
     field→value pairs. On approve → `automate_input(request_id, fields=...)`. On deny →
     `automate_input(request_id, cancelled=True)` and report the denial.

`interactive` defaults `False`, so existing read-only/search automate calls are
unaffected and never prompt.

### Timeout

`tabstack_automate` currently uses the default 180s per-tool timeout. Interactive runs
add a confirmation round-trip plus tabstack's input window and can exceed 180s. Raise
the per-tool `timeout` for `tabstack_automate` in `TOOL_DEFINITIONS` (target ~300s) so
interactive runs don't get killed mid-handshake. (The SDK's own request timeout already
defaults to 600s for streaming endpoints.)

## Acceptance criteria

- Pin is `tabstack>=2.6.1`; `make lint` / `make typecheck` clean against the typed events.
- `tabstack_automate` and `tabstack_research` extract final answers and publish progress
  correctly against the 2.6.1 event shapes (unit tests with mocked streams).
- A `form_data:request` event with all required fields present in `data` → confirmation
  gate fires → on approve, `automate_input` called with matched `{ref, value}` fields.
- Missing required field → `automate_input(cancelled=True)`; tool result names the
  missing fields.
- Confirmation deny → `automate_input(cancelled=True)`; tool result reports denial.
- Non-interactive automate calls never prompt and behave as before.
- `tool_choice` eval still routes form-fill / browser-interaction tasks to
  `tabstack_automate` (not `tabstack_research`).
- SKILL.md documents interactive mode, the `data` param, and the confirmation behavior.

## Open questions / risks

- **Field matching heuristic.** Start simple: case-insensitive match of `data` keys
  against field `label` (and exact-key fallback). Document that `data` keys should
  mirror field labels. Fuzzy matching deferred.
- **Multiple forms in one run.** Each `form_data:request` gets its own gate; the
  confirmation `always` return can pre-approve for the rest of the run (mirrors
  shell/email preapproval).
- **2-minute expiry.** Confirmation default timeout (60s) fits inside the window; if the
  user is slow, tabstack returns `410 Gone` on `automate_input` — catch and report.
