# Notes — Token accounting for archived role-remap messages

## Session log

- **2026-04-29 15:04** — Session started. Worktree at `.claude/worktrees/fix-token-accounting-393/`. Baseline `make test`: 2243 passed. Issue #393 seeded into `spec.md`.
- **Phase 1** — `75fb428` adds failing regression test; `6c59e00` fixup adds tool/memory mock context managers to match sibling `TestCompose` pattern (responsive to code review).
- **Phase 2** — `7207f0b` refactors `compose()`. Phase 1 test goes RED → GREEN. Full suite 2244/2244, `make check` clean. No existing tests required adjustment.
- **Phase 2 smoke test (real data)** — Loaded `web-lmorchard-e5a17d78.jsonl` (46 archived messages including 8 auto-injected remap-role messages) into a fresh `compose()` call. `history_entry.tokens_estimated`: pre-fix would report 2623 (LLM_ROLES only); post-fix reports 9569 — the 6946-token archived remap-role contribution is now correctly counted. Live `make dev` + Playwright was attempted first but the agent's playwright MCP child grabbed the Chrome lock; CLI test against real archived data was equivalent and faster.
- **Phase 2 fixup (`fb79e8d`)** — Phase 3's structural test surfaced a regression Phase 2 inadvertently introduced: the current-turn user message lost its accounting (pre-Phase 2 the role-based history filter happened to count it; post-Phase 2 the user_msg is appended past the diagnostics calc). Fixed by adding a `user_message` SourceEntry. Strictly additive — adds one entry to `composed.sources`, no schema change. Reviewer dispatch skipped for this 11-line additive change since Les approved the approach explicitly and the change is verified by a passing structural test against real production data flow.
- **Phase 3 (`1955b3c`)** — Adds the structural no-double-count guard test. Passes within the planned 5%/50-token tolerance (observed gap is ~12 tokens for the self-referential `[Context: ...]` status line). Suite: 2245 passed. `make check` clean.
- **Phase 4 (`630802c`)** — Adds the "History accounting" sub-section to `docs/context-composer.md` under "Token budget". Lint clean. Implementer audited the rest of the doc for contradictions; none.

## Deferred — for PR self-review

Code-review Minor nits on the Phase 2 commit, not blocking, can be picked up during PR self-review or skipped:

1. `src/decafclaw/context_composer.py:426` — drop the trailing inline comment `# computed earlier, single source of truth` on the `tokens_estimated=history_tokens` field; the block comment three lines above already explains the rationale.
2. Optional rename `combined` → `llm_messages` in compose() (around line 395). Reviewer noted churn cost vs marginal clarity gain — left as-is.
3. compose() docstring (around lines 266-267) — add a blank line between the contract paragraph and the archival note for cleaner rendered formatting.
