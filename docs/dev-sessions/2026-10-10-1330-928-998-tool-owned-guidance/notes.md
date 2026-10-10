# Session Notes: Tool-Owned Prompt Guidance (#928 & #998)

## Overview
Combining #928 and #998 to provide prompt-owned tool usage guidelines dynamically for active core tools and trusted skill tools, retiring static selection instructions from `AGENT.md`.

## Worktree & Baseline
- Worktree: `.claude/worktrees/feat-928-998-tool-owned-guidance`
- Port: `HTTP_PORT=19928`
- Baseline: `make test` passed (4441 passed, 2 skipped)

## Phase Progress
- [x] Phase 1: Tool Guidance Renderer, Trust Gating, and Consumer Registration
- [x] Phase 2: Context Composer and Agent Iteration Loop Injection
- [x] Phase 3: Migrate Workspace Guidance & Tool Definitions
- [x] Phase 4: Documentation and Full Gate Verification

## Token Accounting Before & After

- **Static system prompt (`AGENT.md`):**
  - Before: contained full static tool selection guide (15 lines of text across 11 workspace tools), ~110 tokens present on *every* turn regardless of active tools.
  - After: per-tool lines migrated to tool definitions; `AGENT.md` retains only the invariant ("Prefer surgical edits to full rewrites. Never rewrite an entire file when changing a few lines.").
  - Net static delta: **-110 tokens** from `AGENT.md` on every turn.

- **Dynamic tool guidance (`<tool_guidance>`):**
  - Critical tools active (`workspace_read`, `workspace_write`, `workspace_edit`): contributes ~80 tokens.
  - Deferred tools (`workspace_search`, `workspace_glob`, `workspace_move`, `workspace_delete`, etc.): 0 tokens while deferred.
  - Overall initial turn token delta: **-30 tokens** net reduction in prompt context while delivering more targeted instructions.

## Architectural Improvements
- Clean separation of concerns: `TurnRunner` in `agent.py` no longer performs manual array slicing or message pointer tracking; it delegates mid-turn tool message updates to `ContextComposer.update_iteration_tools()`.
- Trust boundary verified: `tests/test_discovered_skills_consumers.py` explicitly records the `get_trusted_skill_tool_names` gate requiring `skills.grants_capability(info)`.

