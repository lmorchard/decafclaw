# Implementation Plan: Issue 995 — Dream Recent Runs & Bounded Conversation Search

## Phase 1: Shared Scheduled Activity Collection (`src/decafclaw/scheduled_activity.py`)
- [x] Create `src/decafclaw/scheduled_activity.py`.
  - Implement `parse_scheduled_conv_id`, `is_status_token`, `extract_activity`, and `collect_scheduled_activity`.
- [x] Refactor `src/decafclaw/skills/newsletter/tools.py` to use the shared functions from `decafclaw.scheduled_activity`, maintaining test-compatible wrappers.
- [x] Create `tests/test_scheduled_activity.py` testing ID parsing, filtering by skill_name, excluding skill_name, window cutoffs, and activity extraction.
- [x] Run `pytest tests/test_scheduled_activity.py tests/test_newsletter_skill.py` to verify no regressions.

## Phase 2: Date Bound on `conversation_search`
- [x] Update `src/decafclaw/tools/conversation_tools.py`:
  - Add `days: int = 0` to `tool_conversation_search`.
  - Filter archives by cutoff (using `parse_scheduled_conv_id` or `st_mtime`).
  - Filter individual messages by timestamp when available.
  - Update `CONVERSATION_TOOL_DEFINITIONS` schema for `conversation_search`.
- [x] Add unit tests in `tests/test_conversation_tools.py` verifying:
  - Archives outside window are excluded.
  - Entries within window are returned.
  - Default `days=0` preserves full history.
- [x] Run `pytest tests/test_conversation_tools.py`.

## Phase 3: `dream_recent_runs` Tool (`src/decafclaw/skills/dream/tools.py`)
- [x] Create `src/decafclaw/skills/dream/tools.py`:
  - `SkillConfig(window_days: int = 7)`.
  - `dream_recent_runs(ctx: "Context", days: int | None = None) -> ToolResult`.
  - Export `TOOLS` and `TOOL_DEFINITIONS`.
- [x] Update `src/decafclaw/skills/dream/SKILL.md`:
  - Phase 1: Call `dream_recent_runs` (default 7 days) to see what pages and topics were touched recently. Do not re-consolidate them unless fresh source material exists.
  - Phase 2: Use `conversation_search` with `days=7` (or narrower).
- [x] Create `tests/test_dream_skill.py`:
  - Verify tool registration via skill discovery/loader.
  - Verify `dream_recent_runs` only returns dream runs within the specified window with correct fields.
- [x] Run `pytest tests/test_dream_skill.py`.

## Phase 4: Docs, Evals, and Full Gate Verification
- [x] Update `docs/tools.md` and `docs/skills.md`.
- [x] Add an eval case in `evals/skills.yaml` or `evals/dream.yaml`.
- [x] Run `make fmt`, `make lint`, `make typecheck`, `make check`, and `make test`.
