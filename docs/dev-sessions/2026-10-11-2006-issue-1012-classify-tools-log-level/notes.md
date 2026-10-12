---
# Delivery notes: issue 1012 (classify_tools log level)

## Resume here
- Goal: deliver #1012 through PR review follow-up (PR left open, no merge). This handoff: implement + draft PR publication — **done**.
- Issue: https://github.com/lmorchard/decafclaw/issues/1012 (board: Backlog -> In progress done; In review happens in the LATER flow step, not this one)
- Target: /Users/lmorchard/devel/mine/decafclaw; base origin/main @ cbe24424f90c1cb130229fa57ee5e75588cc84cf
- Worktree: .claude/worktrees/issue-1012-classify-tools-log-level; branch: issue-1012-classify-tools-log-level
- **PR: https://github.com/lmorchard/decafclaw/pull/1062 (DRAFT, OPEN, head aa8a44868173b7751789337a879b635ad81fe7c0, base main, mergeable, CI was pending at handoff)**
- Identity: MokaGnome (ghflow exec for commits/pushes/gh writes); `gh api user` returned MokaGnome before first write
- Review plan: user-approved exception — same model for implementation and review (both dispatched as opencode `general` subagents inheriting session model llama.cpp/llama); reviewer runs in a fresh context via review-changes. Waives model difference only; independent fresh-context review still required.
- Endpoint: through PR review follow-up; PR open; NO merge.
- Next action: (for the parent flow) read hosted CI for head aa8a448 (lint-and-test, js-test required), then dispatch independent review (review-changes) on the published head, then ready-for-review + board In review + review request. See "Environment flake" below before re-running local `make test`.

## Decisions
- 2026-10-11: User (Les) requested delivery of #1012 with the same model for implementation and review. Scope: this task only.
- 2026-10-11: Implementation model recorded per handoff instruction: both this worker and the later reviewer run as opencode `general` subagents inheriting the session model `llama.cpp/llama`. No per-agent model override is configured — that is the recorded configuration-level evidence; per-worker runtime identity is not independently resolvable from inside the worker.
- 2026-10-12: One logical commit for the complete atomic change (tests + fix + docs) rather than a separate red-test commit: the repo squash-merges PRs to single commits and a red commit would needlessly fail CI.

## Log
- 2026-10-11: Worktree created from origin/main @ cbe24424f90c1cb130229fa57ee5e75588cc84cf (branch `issue-1012-classify-tools-log-level`). `uv sync` done; `.env` copied from main clone + `HTTP_PORT=8765` added.
- 2026-10-11: Board transition #1012 Backlog → In progress via ghflow CLI: before=Backlog, after=In progress, added=false, readback_matches=true (1 attempt).
- 2026-10-11: Re-verified issue claims against base cbe2442 (facts at the issue's ca9796de base were claims):
  - Combined check at `src/decafclaw/tools/tool_registry.py:190-199` (issue said 144-158 at old base): `if active_tokens > budget or len(active) > max_active:` → single `log.warning("Critical tool set exceeds budget or count: %d tools / %d tokens (budget %d, max %d). Critical tools are included anyway.", ...)`.
  - `max_active = getattr(config.agent, "max_active_tools", 40)` (tool_registry.py:155); default in config_types.py:233 is `max_active_tools: int = 30` (issue's "cap is 30" holds).
  - Token budget = `config.tool_context_budget` (config.py:299) = `compaction.max_tokens * tool_context_budget_pct` (default 100000 * 0.10 = 10000).
  - A SECOND pre-existing `log.info` at tool_registry.py:234-241 ("Tool classification: %d active ...") fires whenever `deferred` is non-empty — tests distinguish the new count-overflow INFO from it (message-content assertion; count-only test case has empty deferred so exactly one INFO).
  - `docs/tool-priority.md:69` (issue said :66): "2. Active set starts with all `critical` tools (hard floor — logged as a warning if it exceeds budget, but still included)."
  - `src/decafclaw/eval/__main__.py:52-57`: silencer comment; `setLevel(logging.ERROR)` at :67.
  - Logger name `decafclaw.tools.tool_registry` (tool_registry.py:13).
  - Test fixture: root `conftest.py` `config` fixture = default Config (budget 10000, max_active 30). `_make_tool_def` helper at tests/test_tool_registry.py:21. `estimate_tokens` = `len(text)//4` (util.py:7-9): default def ≈29 tokens; `"x"*200` def ≈77 tokens.
- 2026-10-12 (commit aa8a448, all 4 files):
  - **Tests first (red):** new `TestClassifyToolsOverflowLogging` (4 tests, caplog) in tests/test_tool_registry.py. Pre-fix: `test_count_only_overflow_logs_info_not_warning` FAILED with the exact reported WARNING ("Critical tool set exceeds budget or count: 35 tools / 1330 tokens (budget 100000, max 30). Critical tools are included anyway."); the 3 protected-behavior tests passed pre-fix (as expected — they guard existing behavior).
  - **Fix:** tool_registry.py — split the check: `active_tokens > budget` keeps `log.warning` (message unchanged); `elif len(active) > max_active` logs new `log.info("Critical tool set over count cap: %d tools (max %d); normal and low tools are deferred.")`.
  - **Docs:** tool-priority.md:69 now says the hard floor is "still included even when over; a token-budget overrun is logged as a warning, a count-cap overrun as info".
  - **Eval runner:** __main__.py comment updated — silencer kept for the token-budget warning; count-only INFO noted as silenced by the same level bump.
- 2026-10-12: Checks at head aa8a448 (worktree):
  - `uv run pytest tests/test_tool_registry.py`: 69 passed (4 new).
  - Related files (test_context_composer.py, test_preempt_search.py, test_search_tools.py): 202 passed.
  - Full suite `uv run pytest tests/ contrib/skills/`: **4511 passed, 2 skipped** at `-n 0` (129.52s), `-n 4` (56.92s), `-n 18` (31.34s), `-n auto` direct (30.44s), and `sh -c` + explicit options (46.13s).
  - `make fmt` (reformatted 1 file — merged the info message to one line), `make lint`, `make typecheck` (0 errors), `make check` (all 7 components green): all pass.
- 2026-10-12: **Environment flake (pre-existing, mechanism undetermined).** Full-suite runs intermittently die at ~84% progress. 13 full-suite runs this session: 8 died (2 on the UNMODIFIED base commit — runs started before any edit, collection predated the edits — 6 with this change), 5 passed (all with this change). `make test` specifically: 0/6 (all "make: *** [test] Error 129" at 84%). Same command with ANY extra CLI option (e.g. `-n 18`) passed 5/5. Experiments:
  - BSD make reports "Error 1" for any signal-killed recipe (verified: kill -1/-9/-15 all → Error 1) and passes explicit exit codes through (exit 129 → Error 129) — so Error 129 is a real exit code 129 from the recipe shell, i.e. the shell's `wait` saw the child die with status 129 (128+1 = SIGHUP) or the child exited 129.
  - Traps on HUP/TERM/INT in the wrapper shell never fired; the wrapper vanished → consistent with SIGKILL of the tree or SIGHUP ignored under nohup.
  - No xdist "crashed worker" output (xdist would report it), no crash reports in ~/Library/Logs/DiagnosticReports, no jetsam/kill events in `log show`.
  - A victim fully detached via `os.setsid()` (new session, outside the tool session tree) also died → not this session's cleanup.
  - Counter-example to command-line-pattern theories: `-n auto ... -v` direct died once and passed once (identical command) → intermittent, not command-determined.
  - `ulimit` generous (1M fds, 10k procs); 64GB RAM, 28% free; machine runs several concurrent agent sessions (opencode ×2, claude ×4, codex).
  - `security_monitor.py` contains "pkill" only inside its block-pattern regex list — it evaluates commands, sends no signals. Not the killer.
  - **Conclusion for this task:** pre-existing shared-environment flake, not caused by this change (base code died too); the suite itself is green (5 direct full-suite passes with the change); hosted CI (which runs `uv run pytest tests/ contrib/skills/` directly) is the authoritative gate. Do NOT burn more time re-running `make test` locally.
- 2026-10-12: Draft PR published: pushed branch, verify-commit aa8a448 --on branch (subject_matches, on_branch, parents=[base]) OK; `gh pr create --draft` → **https://github.com/lmorchard/decafclaw/pull/1062**; read back: OPEN, is_draft=true, head aa8a448, head_branch correct, base main, base_behind_by=0, mergeable; verify-commit --pr-head OK (is_pr_head=true). Body file: docs/dev-sessions/2026-10-11-2006-issue-1012-classify-tools-log-level/pr-body.md (left untracked; canonical body is on the PR). CI at handoff: lint-and-test (required) + js-test (required) pending, check-tui (optional) pending. No review requests/reviews/comments (none requested — per task limits).
- 2026-10-12: This notes file + pr-body.md committed? notes.md committed to the branch per repo convention (recent PRs #1060/#1057/#1055 all carry their session notes); pr-body.md left untracked.
