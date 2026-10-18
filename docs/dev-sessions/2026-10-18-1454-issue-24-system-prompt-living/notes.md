# issue-24-system-prompt-living

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/24 (System prompt as a living document)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-24-system-prompt-living
- Branch: issue-24-system-prompt-living
- Base: origin/main at 288fa0581f4a4dcbe692f1ee418f202e5f009af2
- Flow: implement → publish draft PR (submit-pr, same session) → keep working → final commits to same draft. NO merge, NO ready-mark, NO review request. Unattended: take supported recommended answers, record in PR description.
- Baseline: `make test` green at 288fa058 — 4575 passed, 2 skipped (50.14s).
- Board: Ready → In progress (readback matches).
- Implementation COMPLETE. All checks green at working tree (about to commit).
- Next action: commit → publish draft PR via submit-pr → (evals: connection-failed,
  see Log) → final report.

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
