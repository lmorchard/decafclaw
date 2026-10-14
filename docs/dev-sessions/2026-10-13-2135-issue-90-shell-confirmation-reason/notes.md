# Issue #90 — shell confirmation shows monitor reason + aux risk/reason

Coordinator-dispatched run (ghflow implement-issue + submit-pr), 2026-10-13.

## Scope (binding, from issue comments 2026-10-08 interview)

`src/decafclaw/tools/shell_tools.py`, `check_shell_approval` only:

1. Security-monitor ASK path: confirmation message includes `decision.reason`.
2. Aux-LLM DECLINED path (fallthrough prompt): confirmation message includes aux `risk` and `reason`.
3. No monitor ASK and no aux decision → message exactly as today.
4. Caller-supplied `message` still takes precedence.
5. Do NOT implement/touch #982 (closed; `decline_reason` action_data flow already exists — preserve it).
   Shared risk-level model for non-shell confirmations: out of scope.

Acceptance: tests a–d in `tests/test_shell_approval.py` + `make check` + `make test`.

## Setup

- Worktree `.claude/worktrees/issue-90-shell-confirmation-reason`, branch `issue-90-shell-confirmation-reason` from `origin/main` @ e3d31836 (CI green).
- `uv sync` OK. `.env` copied from main clone + `HTTP_PORT=18928` (checked other worktrees: 8054/8095/8097/8765/8791/18853/18891/18892/18893/18927 in use).
- Baseline `make test`: 4536 passed, 2 skipped (32.04s).

## Research findings

- ASK path: `shell_tools.py:660-680`, message built at 675: `Shell command (security review requested): `cmd``.
- Fallthrough prompt: `shell_tools.py:776-784`, message at 780: `Shell command: `cmd``. `decline_reason`/`suggested_rule` already passed as kwargs (→ `action_data`, used by #982 widget). `risk` is logged + archived + published but never stored in a local var.
- All monitor ASK decisions carry non-empty reason (pattern reasons hardcoded; tier-2 LLM default `LLM classifier evaluation`; fail-open path hardcoded) — see `security_monitor.py:276-295,354,360-365`.
- Aux decline vars: `decline_reason = str(data.get("reason") or "").strip()` (line 743) — will mirror with `decline_risk`.
- Multi-line confirmation message convention: email `_format_confirmation_message` (`email_tools.py:108-125`) joins lines with `\n`; archive `shell_approval` content uses `Command:/Risk:/Reason:` lines.
- `aux_approval_enabled` default False (`config_types.py:178`).
- Callers: `tool_shell` (no message), `skills/background/tools.py:484` (custom `message=`) — item 4 covered by `message or ...`.
- Test patterns: patch `decafclaw.tools.shell_tools.request_confirmation` with AsyncMock, inspect `call_args.kwargs["message"]`. ASK without LLM: use tier-1 sensitive command (e.g. `chmod 777 ...`). Clean fallthrough: `echo hello` (not ambiguous, no sensitive pattern).
- Doc to update: `docs/tools.md` approval-sources paragraph (lines ~167-182).

## Message wording (decision)

- ASK: `Shell command (security review requested): `cmd`\nReason: {decision.reason}`
- Fallthrough: `Shell command: `cmd`` + `\nRisk: {risk}` if risk + `\nReason: {reason}` if reason (each conditional, so no-decision case is byte-identical to today).

## Log

- [x] board: Ready → In progress (ghflow board set-status, readback confirmed)
- [x] tests written (a: ASK reason in message; b: aux decline risk+reason in message; c: no-decision message unchanged; d: caller message precedence on both paths)
- [x] tests fail pre-implementation (a, b failed on missing text; c, d passed as regression guards)
- [x] implementation: ASK path appends `Reason: {decision.reason}` (guarded by truthiness); aux decline captures `decline_risk` alongside `decline_reason`; fallthrough prompt appends `Risk:`/`Reason:` lines only when set. `message or ...` precedence preserved; `decline_reason`/`suggested_rule` kwargs untouched (#982 flow intact).
- [x] targeted tests green (56/56 in file) — note: monitor wraps pattern reasons as `Sensitive command requires explicit confirmation: {reason}`, test asserts the full string
- [x] docs/tools.md approval-sources paragraph updated (#90)
- [x] `make fmt` clean (432 files unchanged)
- [x] `make check` green (install-js, check-message-types, fmt-check, lint, pyright 0 errors, check-js, module graph)
- [x] `make test`: 4540 passed, 2 skipped (14.35s) vs baseline 4536/2
- [x] commit + draft PR: #1070 (draft, head `issue-90-shell-confirmation-reason`, base `main` @ e3d31836). Body at `pr-body.md` in this dir (not committed). `verify-commit --pr-head` clean. CI: js-test + check-tui passed, lint-and-test pending. Not marked ready; no review requested (independent fresh-context review is coordinator-dispatched post-handoff).
