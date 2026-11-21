# issue-1641-activity-aware-shell-approval

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1641
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1641-activity-aware-shell-approval
- Branch: issue-1641-activity-aware-shell-approval
- Base: origin/main at 1da41bd9144eca953c2523d15469fc687f9cfe89
- State: implementation complete locally; full `make check` and `make test` run before commit.
- Scope: Phases 0–3 of the design (instrumentation, deterministic read-only tier, judge risk semantics, activity direction). Phases 4 (global scope rulebook / promotion ledger) and 5 (notification polarity) remain on the issue.

## Decisions

- **Activity is recomputed from `ctx.history`, never persisted.** A sliding window (`shell.activity_window`, default 50 shell calls) makes compaction and restart reset direction for free; no new lifecycle hooks or admin state.
- **cwd names the primary domain; command tokens add domains.** A command reaching into another project counts as drift.
- **`unknown` is a real activity.** An unattributed first command asks once, then later unattributed commands are in-direction and reach the judge.
- **New activity never reaches the judge.** It prompts once with the direction stated (and is denied with the reason on unattended turns).
- **The judge may decline only for a named risk category.** No category means allow; native-tool feedback is advisory and is appended to the tool result.
- **Read-only tier runs after the security monitor and blanket pre-approval,** so Tier 2/ASK behavior is unchanged.
- **Telemetry is fail-open**; a missing bus or test double cannot change an approval decision.

## Evidence

- New modules: `src/decafclaw/activity.py`, `src/decafclaw/shell_readonly.py`, `src/decafclaw/approval_report.py`.
- New tests: `tests/test_activity.py`, `tests/test_shell_readonly.py`, `tests/test_shell_approval_activity.py`, `tests/test_approval_report.py`.
- Existing judge tests moved to mutating commands and seeded activity where they exercise the judge tier; read-only commands now intentionally bypass it.
- `make check`: pass (lint, typecheck, fmt, generated types, browser assets).
- `make test`: 6409 passed, 2 skipped at the final head; baseline before the change was 6149 passed, 2 skipped. Two timing-sensitive tests flaked only under parallel load and passed in isolation (`test_pin_sticky_emit_timeout_does_not_block_reader`, `test_above_budget_returns_job_handle_same_process`).

## Adversarial review findings (fixed)

Confirmed by execution in scratch repos:

- `git grep --open-files-in-pager='cmd'` executed `cmd`.
- `git cat-file --filters` executed a configured clean filter.
- `git fetch --upload-pack='cmd'` executed `cmd`.
- `git status` executed `core.fsmonitor`; `git diff` and `git log -p` executed `diff.*.textconv`.
- `grep -R`, `rg -L`, and `diff -r` read through symlinks outside the workspace.
- `curl --netrc` sent `~/.netrc` credentials.
- `gh api -XPOST`, `--method=POST`, and `-F` bypassed the read-only tier.
- Path-qualified fake verbs (`./ls`, `/tmp/evil/ls`) were trusted by basename.

Fixes: bare PATH verbs only; drop `git status/diff/show/fetch/ls-remote` and patch-producing `git log` from the silent tier (they still reach the judge); block the explicit exec/verify flags; block symlink-follow and file-list-indirection flags; block curl credential flags and `@file`; parse `gh api` method forms.

Residual, documented risks: a silent read-only command still counts toward establishing activity; `git config --get` can print global config values.

## Review follow-up (modularity)

- Activity vocabulary moved out of `activity.py` into `data/{agent_id}/activity_domains.jsonl` (admin-managed JSONL, seeded from the bundled `src/decafclaw/activity_domains.jsonl` on first start, reread each call). Malformed lines skip; an unusable file falls back to bundled.
- Judge prompt moved out of `shell_tools.py` into bundled `src/decafclaw/prompts/SHELL_APPROVAL.md` with per-agent `SHELL_APPROVAL.md` override and single-pass placeholder rendering (`shell_judge.py`). `build_aux_approval_prompt` is now a thin wrapper.
- New admin files are listed in the Web UI config-file browser. Docs updated (`tools.md`, `data-layout.md`).
- Kept the read-only classifier's hardcoded verb families: the reviewer accepted these as de facto standard operations. A pluggable per-command audit-module inventory remains a follow-up idea, not this PR.

## Not done

- Phase 4: global, scope-keyed rulebook + decisions ledger + promotion/demotion + automatic condensation retarget.
- Phase 5: notification polarity (quiet auto-approvals, ntfy/modal only for risk/denials).
- Golden-corpus replay and live shadow mode (§8.3–8.4) were not run: the audit log only has approval events from this change forward, and no live deployment was touched.
