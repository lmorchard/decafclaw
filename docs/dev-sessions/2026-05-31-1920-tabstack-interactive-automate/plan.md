# Plan — Tabstack interactive automate + SDK refresh

Worktree: `.claude/worktrees/tabstack-interactive-automate`
Branch: `worktree-tabstack-interactive-automate`

## Phase 1 — SDK bump + event/response rewrite (foundation, fixes existing breakage)

1. Bump pin in `pyproject.toml`: `tabstack>=2.3.0` → `tabstack>=2.6.1`. `uv sync`.
2. Rewrite `src/decafclaw/skills/tabstack/tools.py` event handling:
   - Add `_event_payload(event)` → `event.data`; dispatch on `event.event`.
   - `tool_tabstack_automate`: progress from `agent:status`/`agent:reasoned`/
     `agent:action`/`browser:*`/`done`; final answer = first non-empty
     `.data.final_answer` across `complete`/`task:completed`/`task:validated`/
     `task:aborted`; surface `error`/`ai:generation:error` `.data.message`.
   - `tool_tabstack_research`: progress from `.data.message`; final answer =
     `complete` event `.data.report`; surface `error` `.data.message`.
   - Delete `_get_field` / `_get_report` / `_log_stream_event` getattr poking.
   - Fix `extract_json` / `generate` to use `result` (plain dict), drop `result.data`
     and the `# type: ignore`.
3. `make lint && make typecheck` clean.
4. **Commit:** "refresh(tabstack): SDK 2.6.1 typed events + dict extract/generate".

## Phase 2 — Interactive form-fill with agent-supplied data + confirmation gate

1. Add params to `tool_tabstack_automate`: `data: dict | None = None`,
   `interactive: bool = False`. Pass `data=`/`interactive=` through to
   `agent.automate(...)` (omit when not given).
2. Handle `interactive:form_data:request` / `interactive:form_data:error` events:
   - `_match_fields(requested_fields, data)` → (`matched=[{ref,value}]`, `missing=[labels]`)
     by case-insensitive label/key match (exact-key fallback).
   - If required fields missing → `await client.agent.automate_input(request_id,
     cancelled=True)`; accumulate missing labels into the result summary.
   - Else → `request_confirmation(ctx, tool_name="tabstack_automate", command=...,
     message=<form_description + page_url + field→value pairs>)`. Approve →
     `automate_input(request_id, fields=matched)`. Deny → `automate_input(request_id,
     cancelled=True)` + note denial. Honor `always`/preapproval return for the run.
   - Wrap `automate_input` in try/except for `410 Gone` (expired window) → report.
3. Raise `tabstack_automate` per-tool `timeout` in `TOOL_DEFINITIONS` to ~300s.
4. Update `tabstack_automate` TOOL_DEFINITIONS: add `data` (object) + `interactive`
   (boolean) params with descriptions; note confirmation gating in the description.
5. `make lint && make typecheck` clean.
6. **Commit:** "feat(tabstack): interactive form-fill with agent-supplied data + gate".

## Phase 3 — SKILL.md + tests + evals

1. SKILL.md: document interactive mode, the `data` param shape (keys ≈ field labels),
   the confirmation behavior, and the "supply data up front, retry with missing fields"
   loop. Update the `tabstack_automate` section + choosing-the-right-tool guidance.
2. Unit tests (`tests/`): mock `AsyncTabstack` async stream emitting typed events.
   - Final-answer + progress extraction for automate and research (2.6.1 shapes).
   - Interactive: all-required-present → gate fires → approve → `automate_input(fields=)`.
   - Missing required field → `automate_input(cancelled=True)` + missing reported.
   - Deny → `automate_input(cancelled=True)` + denial reported.
   - Sabotage-check: break the required-field guard, confirm a test fails.
   - extract_json / generate return-shape (plain dict) round-trip.
3. Eval: `evals/tool_choice/` — keep/sharpen a case routing browser-interaction /
   form-fill tasks to `tabstack_automate` (not `tabstack_research`). Minimal; prompt
   tuning comes from live smoke testing per project norms.
4. `make test` green; check `pytest --durations=25` for the new tests.
5. **Commit:** "docs+test(tabstack): interactive mode docs, unit tests, tool_choice eval".

## Phase 4 — Retro

- Update `notes.md` with a final summary, surprises, and live-smoke-test TODOs.
- Open PR. Live-test in web UI / Mattermost after merge (real form-fill behavior).

## Risk notes

- Confirmation round-trip must resolve inside tabstack's 2-min input window; default
  60s confirmation timeout fits. Catch `410 Gone` on slow approvals.
- Field-matching is intentionally simple; the missing-fields report + agent retry loop
  is the safety net, not a fuzzy matcher.
- Don't break non-interactive automate (default `interactive=False`, no `data`).
