# Plan — 692-reflexive-reads-hallucinated-absence

## Phase 1: Setup
- Update local `main` branch with `origin/main` to fast-forward recent commits.
- Create git worktree for isolated branch development under `fix/issue-692`.
- Install dependencies via `uv sync`.
- Establish test suite baseline using `make test`.

## Phase 2: Implementation
- **AGENT.md Prompt Updates:**
  - Insert `**Check visible context before reading.**` directive under `### Tool usage` to discourage reflexive read tools.
  - Insert `**Empty search is not evidence of absence.**` directive under `### Tool usage` to enforce fallback to visible context and general knowledge.
  - Refine `**Search the vault before saying "I don't know."**` under `## Vault — Your Persistent Memory` to specify that reflexive searches at conversation start should only target specific topics/projects that are not already in visible context, and not general trivia.

## Phase 3: Verification
- Update Case 1 in `evals/over_ceremony.yaml` to test `What's the capital of France?` directly without cushioned text, asserting `max_tool_calls: 0` and no reflexive reads.
- Create a new test case `evals/empty_search_fallback.yaml` to explicitly assert the fallback behavior to visible context when a search returns empty.
- Run `uv run python -m decafclaw.eval evals/over_ceremony.yaml` and verify it passes.
- Run `uv run python -m decafclaw.eval evals/empty_search_fallback.yaml` and verify it passes.
- Run `make check` to ensure Ruff/Pyright/TSC are fully green.
- Run `make test` to ensure unit tests have no regressions.
