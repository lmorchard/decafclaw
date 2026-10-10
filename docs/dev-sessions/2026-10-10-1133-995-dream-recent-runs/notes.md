# Notes: Issue 995 — Dream Recent Runs

## Context & Key Decisions
- Issue: https://github.com/lmorchard/decafclaw/issues/995
- Branch: `issue/995-dream-recent-runs`
- Worktree: `.claude/worktrees/issue-995-dream-recent-runs`
- Investigated live archives under `data/decafclaw/workspace/conversations/schedule-dream-*`:
  - Verified that `conversation_search` with broad queries matches arbitrarily old archives (e.g. August run matching May archives).
  - Verified that consecutive runs regenerate similar pages (e.g. `agent/pages/Dream Skill Documentation Issue` vs `agent/pages/Dream Skill Troubleshooting.md`).
- Decided against rolling vault log page (unreliable LLM trimming) and scheduled recent-journal surfacing (would affect all scheduled tasks).
- Extracted scheduled activity collection into `decafclaw.scheduled_activity` to share across `newsletter` and `dream`.
- Added `dream_recent_runs` tool in `src/decafclaw/skills/dream/tools.py` defaulting to 7 days.
- Added optional `days: int = 0` date bound filter to `tool_conversation_search` in `src/decafclaw/tools/conversation_tools.py`.
- Updated `src/decafclaw/skills/dream/SKILL.md` (Phase 1 checks `dream_recent_runs`; Phase 2 uses `conversation_search` with `days=7`).
- Verified:
  - `make check` clean (0 pyright errors/warnings, ruff format/lint clean, check-message-types, check-js).
  - `make test` passes (4426 passed, 2 skipped in 26s).
  - Eval `evals/dream.yaml` passes (dream calls `dream_recent_runs`, sees recently consolidated page, avoids calling `vault_write`).
  - `make eval-tools` passes new case `conv-vs-vault-recent-chat`.
