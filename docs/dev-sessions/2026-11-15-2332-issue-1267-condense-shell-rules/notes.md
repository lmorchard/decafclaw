# issue-1267-condense-shell-rules

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1267 (Condense auto-approval rules for a conversation)
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1267-condense-shell-rules
- Branch: issue-1267-condense-shell-rules
- Base: origin/main at e4b3f148fb3a (handoff said 75c8c7db; origin moved, delta is session-sidebar only — none of my target files changed)
- Board: Ready → In progress (done via `worktree create --owner lmorchard --project 6`)
- Identity: MokaGnome; writes via `ghflow exec --` / ghflow commands
- Deadline: 2026-11-16 09:28 PST. Handoff: publish first significant checkpoint as a DRAFT PR via submit-pr, keep working on the same branch; do NOT mark ready.

## Scope (from issue)

1. **Rules-replace endpoint** `PUT /api/conversations/{id}/shell-approval/rules/replace` body `{"rules": [...]}` — atomic full-list replace; clears cached patterns; 409 busy/waiting.
2. **Bulk-edit draft (panel)** — edit mode in `shell-approval-panel.js`: each rule editable, add/remove rows; Save → replace endpoint; Cancel discards; single-rule actions hidden while editing.
3. **Condense action** `POST /api/conversations/{id}/shell-approval/condense` — builds prompt from current rules, one-shot completion (no tools) on resolved condense model, returns proposal `{"rules": [...]}`; NEVER mutates state; 409 busy/waiting; parse/model error → error, panel keeps draft.
4. **Condense model knob** `shell.condense_model` on ShellConfig + sidecar pointer + resolution (pointer → seed → active conv model → effective default; NO auxiliary_model step) + settings picker in `model-catalog.js`.

## Key code facts (verified at base e4b3f148)

- `approval_state.py`: rules = strings in `ApprovalState.aux_approval_guidance`; persisted via `manager.set_flag(conv_id, "aux_approval_guidance", ...)` which also calls `_persist_approval_state` (writes `data/{agent_id}/approval_state/{conv_id}.json`).
- Single-rule PUT/DELETE in `http_server.py` use `manager.set_flag(conv_id, "aux_approval_guidance", new_list)` + `set_flag(..., "llm_approved_shell_patterns", [])`. Replace reuses the same two set_flags with the full list.
- Judge call pattern (`shell_tools.py:615`): `response = await ctx.aux_approval_llm()(messages)`; `raw_text = response.get("content","").strip()`; strips ```json fences then `json.loads`.
- **Testability:** `stub_llm` (tests/conftest.py) patches `decafclaw.llm.call_llm`. context.py binds `call_llm` at module import, so a module-level import in http_server would NOT be intercepted. The condense handler must `from .llm import call_llm` at FUNCTION level (resolves the patched attr at call time). http_server already uses function-level imports for model_catalog/canvas/sticky/notifications, so this matches the file's pattern.
- `effective_aux_approval_model(config, active_model)` at model_catalog.py:418 is the template. Condense = same minus the `auxiliary_model` step.
- Conversation active model: `state.persisted.active_model` (PersistedTurnState field).
- ModelCatalog sidecar dataclass has `aux_approval_model`/`reflection_model`/`verifier_model` pointer fields; add `condense_model`.
- `catalog_payload()` exposes `<x>_model` (effective) + `<x>_pointer` (raw). Add condense pair.
- `models_catalog_set_aux_approval` handler is the template for a set-condense-pointer endpoint.
- API client: `make gen-api-client` (scripts/gen_api_client.py) regenerates committed TS client from the app's OpenAPI spec; needs node_modules (`make install-js`). New endpoints → new DefaultService methods.
- Panel JS imports `DefaultService` from `../lib/api-client/index.js`.

## Plan / phases (commit per phase)

1. [x] Config knob `shell.condense_model` + model_catalog (sidecar pointer, effective_condense_model, set_condense_model, catalog_payload) + tests/test_model_catalog.py resolution tests. → commit 86b4a7e8
2. [x] HTTP endpoints: rules/replace + condense (+ set-condense-pointer) in http_server.py; pydantic models; route table. tests/web/test_shell_approval_panel_http.py replace+condense tests + test_models_catalog_http.py condense pointer. → commit 40c04e16
3. [x] `make gen-api-client` (regenerate TS client for new endpoints) — in commit 40c04e16. Generated methods: `putConversationShellRulesReplaceApiConversationsIdShellApprovalRulesReplacePut(id,{rules})`, `postConversationShellCondenseApiConversationsIdShellApprovalCondensePost(id)`, `wrapperApiModelsCatalogCondensePost({model})`.
4. [x] Panel JS edit mode + condense (shell-approval-panel.js) + extend shell-approval-panel.test.js. Includes the transport fix: `scripts/sticky_api_request.ts` must list rules/replace + condense in the shellApproval family, or they ride the stock transport (no same-origin credentials → 401 in production; header-gated body decode breaks the headers-less test mocks).
5. [x] model-catalog.js condense picker + extend model-catalog.test.js.
6. [ ] Docs: config.md, model-selection.md, web-ui.md (panel + API table), tools.md.
7. [ ] Gates: make fmt, make check (lint/typecheck/check-js/browser-assets), make test, make test-js.

## Draft PR published (checkpoint 1)

- **PR: https://github.com/lmorchard/decafclaw/pull/1289** (DRAFT, base main, head 40c04e16)
- Author + commit authors: MokaGnome (no attribution finding). verify-commit --pr-head is_pr_head=True.
- Body: docs/dev-sessions/.../pr-body-draft.md (draft, "Part of #1267", no Closes).
- Continuing on the same branch; will push JS + docs commits to this draft. Do NOT mark ready (handoff: leave open).

## Success conditions → evidence map

1. Bulk edit + one-action save; GET returns saved list, patterns empty → replace endpoint test (full replace + pattern clear) + panel save-sends-full-list test.
2. Condense shows proposal w/ before-after; cancel leaves GET unchanged → condense endpoint test (never mutates) + panel cancel-discards test.
3. Saving condensed draft == manual bulk save (file holds new list, patterns cleared) → replace endpoint test covers the shared write path.
4. condense_model unset → active conv model (then default); set to key → that model → effective_condense_model tests + condense endpoint stub_llm model_name assertion.
5. Non-JSON model response → error; rules+patterns unchanged → condense parse-failure test.

## Log

- Baseline `make test`: 5502 passed, 2 skipped in 42s (clean).
- Port: HTTP_PORT=18961 set in worktree .env.
- Resume 2026-11-16: found the panel condense flow dead on arrival — the two new endpoints were missing from `scripts/sticky_api_request.ts`'s shellApproval family, so they rode the stock transport. Added both; regenerated the client (`make gen-api-client`, idempotent 3-line diff). Panel suite 13/13, full JS suite 687 passed, `make check-js` (tsc) clean.
