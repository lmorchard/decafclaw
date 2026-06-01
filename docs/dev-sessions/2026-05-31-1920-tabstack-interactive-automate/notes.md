# Notes — Tabstack interactive automate + SDK refresh

## Investigation (2026-05-31)

- Installed `tabstack` is **2.3.0** (pin `>=2.3.0`); local checkout at
  `~/devel/tabstack-python` is **2.6.1**.
- Changelog gap:
  - 2.4.0 added the `automate_input` endpoint (interactive form-data handshake).
  - 2.6.0 swapped the `/automate` SSE root to a typed discriminated-union event model.
- **Current skill breakage:** `tools.py` reads `event.message/finalAnswer/report/
  metadata` at the top level; real payload is under `event.data` (both 2.3.0 dict and
  2.6.1 typed). `extract.json`/`generate.json` now return plain dicts, so `result.data`
  is also broken.
- Event envelope (2.6.1): `AutomateEvent` and `ResearchEvent` both = union of
  `{event: <discriminator>, data: <typed payload>}`.
  - automate final answer: `.data.final_answer` on `complete`/`task:completed`/
    `task:validated`/`task:aborted`.
  - research final answer: `.data.report` on `complete`.
- Interactive: `automate(interactive=True, data={...})`; on
  `interactive:form_data:request` (and `:error` re-request) the stream carries
  `request_id` + `fields[{ref,label,fieldType,required,options}]`. Answer via
  `agent.automate_input(request_id, fields=[{ref,value}])` or `cancelled=True`.
  Request expires after 2 min (`410 Gone`).

## Design decisions (from Les)

- **Scope:** agent-supplied data (not live human-in-the-loop — infeasible in the turn
  model: stream dies across a turn pause, 2-min request expiry).
- **Safety:** confirmation gate before submitting personal data into a form, using the
  inline-blocking `request_confirmation` pattern (same as `send_email`).
- **Process:** tracked dev session on a worktree from main.

## Progress log

- **Phase 1+2 (commit `00f716d`)** — bumped pin to `>=2.6.1`; rewrote automate/research
  event handling against the typed `.data` envelope (dispatch on `event` discriminator);
  fixed `extract_json`/`generate` plain-dict responses; added `data`+`interactive` params,
  `_match_fields`, confirmation gate via `request_confirmation`, graceful cancel + missing-
  field reporting, `_safe_input` (swallows 410 Gone); raised `tabstack_automate` per-tool
  timeout to 300s.
- **Phase 3 (commit `ea03992`)** — SKILL.md interactive docs; `tests/test_tabstack_tools.py`
  (12 tests incl. a guard sabotage-check); two `tool_choice` eval cases.
- **Phase 4** — `docs/skills.md` tabstack section updated; PR opened (#569).
- **Follow-up (richer progress)** — expanded `_automate_progress` to narrate the
  high-signal events the typed union now exposes (step counter, navigation title,
  action + ref, action failures, extraction, waiting, reconnect, validation). Two
  fixes folded in: (a) the top-level `error` event and unsuccessful `complete` nest
  the message under `data.error.message` (was reading `data.message` → hard errors
  surfaced nothing); now `_automate_error_text` extracts it and `_compose_automate_result`
  reports `[error: ...]`. (b) **Safety:** `agent:action`/`browser:action_started` carry
  the typed-in `value` (potentially the personal data we just confirmed) — progress
  never echoes `value`. Form handling now narrates the field *labels*, never values.
  Tests + guards added (value-leak guard, error extraction).

## Findings during execution

- **Default model is `vertex-gemini-flash`.** `make eval-tools` is noisy on Flash — a
  `<no_tool>` epidemic across unrelated pre-existing cases (web-fetch, vault-notes,
  workspace-write, ask-choice, delegate). Baseline isn't all-green; this is the
  "evals guard structure, tuning from live smoke testing" reality.
- **First eval case draft was bad.** "fill out the form with my email and name" (no
  values) → the model reasonably *asks for the data* (`<no_tool>`) rather than calling a
  tool. Lesson: tool_choice form-fill scenarios must include concrete data inline, or the
  correct behavior is to pause-and-ask. Revised cases pass 2/2 stably on Flash.
- Verified the guard with a real sabotage edit (`if False and missing:`) — the
  missing-required test fails as intended, then reverted.

## Live validation

- **Happy path confirmed (Les, live).** `interactive=true` form-fill against a real form
  worked end-to-end: the `interactive:form_data:request` fired, the confirmation gate
  appeared, and submission completed. This was the #1 open caveat — the typed
  interactive request path is verified against live tabstack.

## Remaining smoke-test TODOs

- Missing-field retry loop (omit a required value → cancel + report → re-run).
- Confirmation deny path (decline → cancelled, nothing submitted).
- Confirm non-interactive automate + research still extract answers correctly.
- Watch for `410 Gone` if approval is slow (>2 min); confirm it's reported, not crashed.
