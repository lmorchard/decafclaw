# issue-24-system-prompt-living

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/24 (System prompt as a living document)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-24-system-prompt-living
- Branch: issue-24-system-prompt-living
- Base: origin/main at 288fa0581f4a4dcbe692f1ee418f202e5f009af2
- Flow: implement → publish draft PR (submit-pr, same session) → keep working → final commits to same draft. NO merge, NO ready-mark, NO review request. Unattended: take supported recommended answers, record in PR description.
- Baseline: `make test` green at 288fa058 — 4575 passed, 2 skipped (50.14s).
- Board: Ready → In progress (readback matches).
- Implementation COMPLETE. Committed 4c05a2fdd7152364c46aa71c5ebaea95358611ec,
  published as DRAFT PR https://github.com/lmorchard/decafclaw/pull/1084.
- Hosted CI GREEN at 4c05a2f: lint-and-test (required) passed, js-test
  (required) passed, check-tui passed. mergeable: MERGEABLE, base 0 behind.
- DO NOT mark ready / request review (handoff limit). Board stays In progress
  while the PR is a draft.
- Next action (coordinator): dispatch independent local review (fresh context,
  same local model — user-approved exception) of head 4c05a2f, then `pr ready`
  with --issue/--owner/--project, then review follow-up. No merge (flow limit).
- Uncommitted: this notes file only (kept in working tree; no notes-only
  commit while the draft is up).

## Confirmed scope (from handoff; authoritative)

- New tool `update_system_prompt(section: str, content: str)` in `tools/prompt_tools.py`:
  - Allowed mutable sections: `USER.md`, `CUSTOM.md` in `data/{agent_id}/`.
  - `SOUL.md` guarded → descriptive error, file untouched.
  - Invalid section names → descriptive error.
  - User confirmation (existing confirmation flow) BEFORE writing.
  - Writes updated text to `data/{agent_id}/{section}`.
- `load_system_prompt()` includes `CUSTOM.md` wrapped in `<custom_context>` when present (non-empty); excludes when absent.
- Unit tests in `tests/test_prompts.py` covering the issue's verification criteria.
- Out of scope: mid-turn hot reload, automatic unconfirmed rewrites, arbitrary writes outside `data/{agent_id}/`.

## Decisions

- **Secret policy interaction (handoff-mandated check).** The tool writes into
  `data/{agent_id}/` (admin region). Per the "one shared rule" convention
  (`secret_policy.py`, docs/tools.md #secret-file-protection), every tool that
  mutates under `config.agent_path` consults the same rule. Resolution:
  `update_system_prompt` applies (a) the refuse tier via `is_refused(section, config)`
  (default set does NOT cover USER.md/CUSTOM.md, but admin-widened
  `secret_policy.refuse_paths` must be honored), and (b) the marker guard
  `marker_in(content)` (refuse payloads containing `<redacted>` — same
  read→write clobber protection as admin_write). Redaction tier does not apply:
  USER.md/CUSTOM.md are not in `REDACTABLE_FILES` (config.json/mcp_servers.json).
  This mirrors exactly how `admin_write` guards non-JSON files.
- **Confirmation action type.** New `ConfirmationAction.UPDATE_SYSTEM_PROMPT`
  with a dedicated `UpdateSystemPromptHandler` in prompt_tools.py, registered in
  ConversationManager.__init__ next to AdminMutationHandler, mapped in
  tools/confirmation.py `_get_tool_action_map`. Keeps admin_tools.py from growing
  a branch for a tool it doesn't own; recovery path re-validates section
  allowlist + secret refusal + marker guard (must inherit guards, per #1013).
- **Unattended/child gate.** Same categorical block as admin mutations
  (`ctx.is_unattended or ctx.is_child`) — "automatic unconfirmed prompt
  rewrites" is out of scope, and scheduled/heartbeat turns cannot reach a human.
- **Priority/timeout.** `priority: low` (on-demand, like admin mutations),
  `timeout: None` (confirmation-gated opt-out per CLAUDE.md).
- **Empty content allowed** (with confirmation) — the diff preview shows the
  wipe; the loader drops empty sections, so this is the "clear the section" path.
- **TOCTOU.** Reuse admin_tools `_capture_snapshot`/`_revalidate_snapshot` +
  `file_lock`: between confirmation and write, a replaced/symlinked target
  (e.g. `USER.md -> web_tokens.json`) aborts with "fresh confirmation required".
- **Docs updated in same PR** (project rule): docs/tools.md (new section +
  secret-file protection "six tools" → include update_system_prompt),
  docs/context-composer.md (CUSTOM.md row + `<custom_context>` delimiter),
  docs/data-layout.md (CUSTOM.md in tree, USER.md/CUSTOM.md agent-writable
  note), AGENT.md (one line: the sanctioned path when user explicitly asks),
  CLAUDE.md (key files + XML tag list + data-layout bullet + confirmation-gated
  tool list).
- **Evals.** New tool description → tool_choice cases in
  evals/tool_choice/core_overlaps.yaml (update_system_prompt vs admin_write vs
  workspace_write). AGENT.md line is an LLM-visible prompt change → same cases
  guard it. `make eval-tools` is a gate-external check; run it if the env allows,
  else report as evidence limit.

## Plan

1. `src/decafclaw/prompts/__init__.py`: load CUSTOM.md after USER.md, wrap in
   `<custom_context>` (same empty-body gating). Update module + function docstrings.
2. `src/decafclaw/tools/prompt_tools.py`: `update_system_prompt` tool +
   `PROMPT_TOOLS`/`PROMPT_TOOL_DEFINITIONS` + `UpdateSystemPromptHandler`.
3. `src/decafclaw/confirmations.py`: add `UPDATE_SYSTEM_PROMPT` action.
4. `src/decafclaw/tools/confirmation.py`: map `update_system_prompt` → action.
5. `src/decafclaw/conversation_manager.py`: register handler.
6. `src/decafclaw/tools/__init__.py`: merge PROMPT_TOOLS / PROMPT_TOOL_DEFINITIONS.
7. `tests/test_prompts.py`: TestCustomContext (loader) + TestUpdateSystemPrompt
   (writes, guards, errors, confirmation, unattended/child, marker, secret
   policy, recovery handler).
8. `evals/tool_choice/core_overlaps.yaml`: disambiguation cases.
9. Docs + AGENT.md + CLAUDE.md.
10. `make fmt`, targeted tests, `make check`, full `make test`; commit per phase;
    publish draft via submit-pr at first significant checkpoint.

## Log

- 2026-10-18 1454: Worktree created from origin/main @ 288fa058 (ghflow
  `worktree create`); .env copied + HTTP_PORT=18880 added; `uv sync` ok.
  Board Ready → In progress (before: Ready, after: In progress, readback matches).
- 2026-10-18 1500: Baseline `make test`: 4575 passed, 2 skipped, 50.14s — green.
- 2026-10-18 1510: Code study done: prompts/__init__.py (wrap_xml +
  _PROMPT_FILES + USER.md block at lines 75-82), admin_tools.py (confirmation
  gate, snapshot/revalidate, secret guards, AdminMutationHandler recovery),
  confirmation.py (request_confirmation + _get_tool_action_map),
  secret_policy.py (is_refused/marker_in/refusal_message/marker_message),
  confirmations.py (ConfirmationAction enum + registry), conversation_manager.py
  (handler registration at __init__), tools/__init__.py (TOOLS +
  TOOL_DEFINITIONS merge), tool_registry.py (priority buckets: low = on-demand),
  tests/test_admin_tools.py (mock confirmation pattern), conftest.py
  (config/ctx fixtures: agent_path = tmp/test-agent).
- 2026-10-18 1540: Test-first: wrote TestCustomContext (5 loader tests) +
  TestUpdateSystemPrompt (15 tool tests) + TestUpdateSystemPromptRecovery
  (5 handler tests) in tests/test_prompts.py; confirmed red (import error /
  absent behavior).
- 2026-10-18 1610: Implemented: prompts/__init__.py CUSTOM.md block
  (<custom_context>, after <user_context>, same empty-body gating);
  tools/prompt_tools.py (tool + def + UpdateSystemPromptHandler);
  confirmations.py UPDATE_SYSTEM_PROMPT action; tools/confirmation.py map;
  conversation_manager.py handler registration; tools/__init__.py merge
  (priority low, timeout None).
- 2026-10-18 1620: Test bug found: dropped `self` in class methods (pytest
  bound the instance to `ctx`). Fixed. Also `upper()` uppercases the
  extension — allowlist keyed by upper-cased name ("USER.MD" -> "USER.md").
- 2026-10-18 1630: Guard-coverage spot checks (disable-guard → tests must
  fail): SOUL guard 3/3 fail, marker guard 2/2 fail, confirmation gate 2/2
  fail, secret-policy refusal 1/1 fail, CUSTOM.md loader 3/5 fail (absent-by-
  default + empty-file guards correctly keep passing). All restored.
  NOTE: one spot-check script had a .bak bug (wrote patched version to .bak);
  prompts/__init__.py "restore" put back the block-less file — re-applied the
  CUSTOM.md block by hand; verified via git diff.
- 2026-10-18 1635: SOUL guard tests initially passed without the guard because
  the generic invalid-section error also mentions "SOUL.md is guarded" —
  tightened assertions to "core identity file" (guard-specific phrasing).
- 2026-10-18 1640: Evals: 4 tool_choice cases added to
  evals/tool_choice/core_overlaps.yaml (update_system_prompt vs admin_write /
  workspace_write / vault_journal_append + negative cases). Docs updated:
  tools.md (Prompt Sections section + "six tools"→seven + marker guard),
  context-composer.md (CUSTOM.md row, <custom_context> delimiter, layout),
  data-layout.md (tree + secret-files section), AGENT.md (one line under
  "Don't fix the prompt mid-task"), CLAUDE.md (key files, XML tag list,
  data-layout bullet, confirmation-gated tool list).
  test_tool_timeout.py: update_system_prompt added to the timeout=None guard.
- 2026-10-18 1650: Checks (working tree, pre-commit): make test 4600 passed /
  2 skipped (baseline 4575 + 25 new); make check fully green (install-js,
  check-npm-policy, check-message-types, fmt-check, lint, typecheck 0
  errors, check-js, check-browser-assets); make fmt 437 unchanged.
- 2026-10-18 1645: `make eval-tools` (filtered to the 4 new cases): FAILED
  with "LLM call failed: All connection attempts failed" on all 4 cases —
  provider endpoint unreachable from this environment. EVIDENCE LIMIT, not a
  code failure. Loadout confirmed correct: "101 tool(s) loaded" (new tool
  present). Did not retry (environment limit; hosted CI settles the gates —
  evals are outside `make check`/CI anyway).
- 2026-10-18 1655: Committed 4c05a2fd (15 files, +1048/−22): feature + tests
  + evals + docs + these notes, one cohesive commit.
- 2026-10-18 1700: Published DRAFT PR #1084 (MokaGnome, base main, head
  issue-24-system-prompt-living @ 4c05a2fdd7152364c46aa71c5ebaea95358611ec;
  no pre-existing PR for the branch; verify-commit --on branch and
  --pr-head both clean; is_pr_head: true). Body: summary + review (not
  performed, draft) + "## Decisions taken unattended" (secret-policy
  interaction, empty-content, new action type) + commits + checks +
  "Closes #24". Body file:
  /private/var/folders/8p/p5kz_p794bbbwgg2_hgn34nw0000gn/T/opencode/issue24-body-full.md
- 2026-10-18 1705: pr-state: CI green at 4c05a2f — lint-and-test (required)
  passed, js-test (required) passed, check-tui passed; mergeable MERGEABLE;
  base_behind_by 0. Description refreshed to the green state. Board stays
  "In progress" (draft PR does not qualify for In review).

## Post-review correction (handoff correction-24.md)

Independent review (fresh context, same local model under the user-approved
exception) found one actionable defect + two accepted notes.

- 2026-10-18 1800s: **F1 (actionable) — recovery path never applied an
  approved write.** `UpdateSystemPromptHandler.on_approve` read
  `action_data.get("section")`, but the shared machinery
  (`admin_tools._confirm_admin_mutation`) persists the section under the key
  **`path`** — verified in code (admin_tools.py:333-339; AdminMutationHandler
  reads `action_data.get("path")` at :803/:837). No code ever writes a
  `section` key, so 100% of legitimate recoveries were dropped with a
  confusing `section '' is not a valid prompt section name` cancel. The old
  recovery tests passed because they hand-built `action_data` with a `section`
  key — the shape I assumed, not the shape the flow persists (shape drift).
  Fix: `on_approve` reads `action_data.get("path") or ""`; `on_deny` reads
  `action_data.get("path", "file")` (mirrors AdminMutationHandler.on_deny —
  also fixes accepted Note 1, the literal-'section' denial message).
- 2026-10-18: **Note 2 (accepted) — description overstated timing.**
  "picked up by load_system_prompt on the next prompt assembly" → "on the
  next server restart or skill refresh (system prompt assembly is not
  per-turn)". Verified: `config.system_prompt` is assembled at startup
  (decafclaw/__init__.py:59) and reassembled only on skill refresh
  (skill_tools.py:1160-1168, `refresh_skills` + activate_skill catalog-miss).
  One-liner added to docs/tools.md Prompt Sections. Notes 3 (declined) and
  4 (no action) per coordinator settlement.
- 2026-10-18: **Recovery tests now drive the real persistence shape.** New
  `_capture_pending` helper: runs the interactive tool with a capturing
  confirmation that simulates process death while pending (no write), then
  round-trips the captured request through
  `ConfirmationRequest.to_archive_message`/`from_archive_message` — exactly
  what `_scan_archive_for_pending` does on restart. Rebuilt 4 of 5 recovery
  tests on it (approve-apply, stale, deny, + new contract test
  `test_interactive_request_persists_section_under_path_key`); 2 tests
  (SOUL.md revalidation, marker) stay hand-built — the interactive flow can
  never emit those shapes — but now use the real `path` key.
- 2026-10-18 1915: **Red check (test teeth).** Temporarily reverted the
  handler to the buggy `section` reads: 4/6 recovery tests fail, reproducing
  F1's exact symptom (`section '' is not a valid prompt section name`),
  incl. the denial-message test. Fix re-applied; all green.
- 2026-10-18 1925: Checks at the fix: tests/test_prompts.py 41 passed (was
  40 — +1 contract test); test_admin_tools/test_tool_timeout/test_tool_registry
  125 passed; `make test` 4601 passed / 2 skipped; `make check` exit 0
  (install-js, check-npm-policy, check-message-types, fmt-check, lint,
  typecheck, check-js, check-browser-assets). `make eval-tools` still not
  runnable (endpoint unreachable) — evidence limit unchanged.
- 2026-10-18 1937: Committed a9fd6838e1c077b44daeea1e895208b3ba5eae14
  "fix(prompts): apply recovered prompt updates — read the persisted
  \"path\" key (#24)" (4 files, +173/−98; code + these notes together);
  pushed to issue-24-system-prompt-living; verify-commit clean on both
  --on branch and --pr-head (is_pr_head: true).
- 2026-10-18 1941: CI green at a9fd6838 — `gh run list --branch` shows the
  CI run on headSha a9fd6838... completed success (created 02:35:28Z);
  pr-state: lint-and-test (required) passed, js-test (required) passed,
  check-tui passed; mergeable MERGEABLE; base_behind_by 0.
- 2026-10-18 1943: PR description refreshed (pr body → gh pr edit, NOT
  marked ready, no review request): body v2 at
  /private/var/folders/8p/p5kz_p794bbbwgg2_hgn34nw0000gn/T/opencode/issue24-body-v2.md
  — 2 commits, "Checks at `a9fd683`" green, Review section now records the
  performed review + F1 + fix + "Delta review of `a9fd683` pending".
  Read-back: isDraft true, head a9fd6838, body updated. Board stays "In
  progress" (draft PR does not qualify for In review).
- 2026-10-18 1945: Correction complete. Remaining for the coordinator:
  delta review of a9fd6838 (fresh context, same local model exception),
  then `pr ready --issue ... --owner lmorchard --project 6` (moves issue
  In review), then user review. No merge (flow limit). Notes-only entries
  after this line (publication log) intentionally left uncommitted per the
  no-notes-only-commit constraint.

## Ready submission (handoff ready-24.md)

- 2026-10-18 2330s: Delta review of `4c05a2f..a9fd683` reported favorable
  (F1 resolved end-to-end via reviewer's own reproduction; rebuilt recovery
  tests verified with teeth — independent red check 4/6 fail under the bug;
  no new actionable findings). Full coverage of head a9fd683.
- 2026-10-18: Final PR description assembled (pr body →
  issue24-body-final.md): 2 commits, checks at a9fd683 green, Review
  section = base review (F1 + 4 notes) + correction + favorable delta
  review + **declined note recorded with reason** (stale-test assertion
  improvement — routine, non-blocking; correction cycle + re-review +
  ready did not fit before the 22:31 PDT queue deadline, so reviewed head
  a9fd683 reaches the endpoint) + model evidence (llama-local same-model
  under the user-approved exception; independence via fresh-context
  reviewer) + new "## Evidence limits" section (eval-tools unexecuted,
  endpoint unreachable; 4 committed tool_choice cases to run when
  reachable) + existing Decisions taken unattended + Closes #24.
- 2026-10-18: `pr ready` executed: description confirmed, ready confirmed
  (before is_draft true → after false, OPEN), expected_head_matches true
  (a9fd6838), board In progress → In review (readback_matches true,
  item PVTI_lAHNVLfOAUg5p84J7FOq).
- 2026-10-18: Independent read-backs: gh pr view — isDraft false, state
  OPEN, head a9fd6838, live body contains all final sections (grep on
  ## headers + key lines); board GraphQL read — issue #24 "System prompt
  as a living document" Status = In review; gh run list --branch — CI run
  on headSha a9fd6838 completed success; pr-state — lint-and-test
  (required) passed, js-test (required) passed, check-tui passed,
  MERGEABLE, base 0 behind, no review requests, no reviews.
- Endpoint reached: PR #1084 open, ready for review, issue #24 In review.
  Remaining is the user's review (no merge, no reviewer request — flow
  limits). Worktree: only notes.md uncommitted (publication + ready logs),
  intentionally no notes-only commit.

## Review follow-up (Copilot comments r4310672115, r4310672146)

- 2026-10-20: Addressed symlink / redirection bypasses in `update_system_prompt` and its recovery handler:
  - Added `_check_section_target(config, canonical)`: rejects pre-existing symlinks before reading/snapshotting, checks resolved destination against `_GUARDED_SECTIONS` (`SOUL.md`) and `is_refused` (`web_tokens.json`), and retains section-name refusal.
  - In `UpdateSystemPromptHandler.on_approve`: applied `_check_section_target` to on-disk state AND added destination/redirection checks on the persisted `snapshot.resolved`.
  - Added 6 regression tests in `tests/test_prompts.py` covering symlink destinations (`SOUL.md`, `web_tokens.json`, arbitrary file) for both interactive and recovery paths.
  - All 47 prompt tests passed; full test suite (4607 tests) and `make check` passed cleanly.
