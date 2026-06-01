# TODO — Tabstack interactive automate + SDK refresh

## Phase 1 — SDK bump + event/response rewrite
- [x] Bump pin `tabstack>=2.6.1` in pyproject.toml; `uv sync`
- [x] `_event_payload`/discriminator dispatch (via `_automate_progress` + kind checks)
- [x] Rewrite automate event handling (progress + final answer + errors)
- [x] Rewrite research event handling (progress + report + errors)
- [x] Delete `_get_field` / `_get_report` / `_log_stream_event`
- [x] Fix extract_json / generate to use plain-dict result
- [x] lint + typecheck clean
- [x] Commit

## Phase 2 — Interactive form-fill + gate
- [x] Add `data` + `interactive` params to tabstack_automate
- [x] `_match_fields` (matched / missing)
- [x] Missing required → automate_input(cancelled=True) + report
- [x] Confirmation gate → approve → automate_input(fields=)
- [x] Deny → automate_input(cancelled=True) + report
- [x] 410 Gone handling (`_safe_input` swallows + logs)
- [x] Raise per-tool timeout 300s
- [x] Update TOOL_DEFINITIONS (data + interactive params, gate note)
- [x] lint + typecheck clean
- [x] Commit

## Phase 3 — Docs + tests + evals
- [x] SKILL.md: interactive mode, data param, gate, retry loop
- [x] Unit tests: automate/research extraction (2.6.1 shapes)
- [x] Unit tests: interactive approve / missing / deny
- [x] Sabotage-check on required-field guard (verified by breaking prod code)
- [x] Unit tests: extract_json / generate dict shape
- [x] tool_choice eval cases (2/2 pass on Flash, ran twice)
- [x] make test green (2719 passed); durations clean
- [x] Commit

## Phase 4 — Retro
- [x] notes.md final summary + live-smoke TODOs
- [x] docs/skills.md tabstack section updated
- [ ] Open PR
- [ ] Live test web UI / Mattermost (post-merge)
