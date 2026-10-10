# 995 — Keep a short journal window for "dream" skill so it knows what it did recently

_Source: GitHub issue [lmorchard/decafclaw#995](https://github.com/lmorchard/decafclaw/issues/995) (triage:ready). Confirmed decisions with Les (2026-10-08)._

## Problem & Bounded Slice

The scheduled `dream` skill often rehashes topics and vault pages it touched on prior nights.
Three factors drive this behavior:
1. Each scheduled run starts in a fresh conversation (`schedule-dream-<YYYYMMDD-HHMMSS>`) with no context from prior runs.
2. In Phase 2 (Gather), `dream` runs `conversation_search` with broad queries and no date bound, retrieving conversation turns from months earlier (confirmed in archive `schedule-dream-20260813-141816` matching May 2026 conversations).
3. `dream` has no mechanism to know which vault pages it modified or topics it consolidated in the past week, leading to repeated pages on consecutive nights (e.g., `agent/pages/Dream Skill Documentation Issue` on 2026-05-26 followed by `agent/pages/Dream Skill Troubleshooting.md` on 2026-05-27).

**Confirmed decisions (Les, 2026-10-08):**
1. **Mechanism (a): derive recent dream runs from `schedule-dream-*` archives via a `dream_recent_runs` tool.**
   Extract the activity collection logic from `skills/newsletter/tools.py` into a shared module (`decafclaw.scheduled_activity`) without duplication.
   A rolling vault page was rejected because pruning depends on LLM compliance. Scheduled recent-journal surfacing was rejected because it affects all scheduled tasks.
2. **Phase 2 gets a date-bounded search.**
   Add an optional `days: int = 0` parameter to `conversation_search` (`src/decafclaw/tools/conversation_tools.py`).
3. **Dream SKILL.md updates:**
   Phase 1 calls `dream_recent_runs` to inspect runs from the past 7 days and avoids redoing touched topics/pages without new source material. Phase 2 bounds `conversation_search` to recent days.

## Concrete Changes & File Targets

1. **Shared Scheduled Activity Module (`src/decafclaw/scheduled_activity.py`):**
   - Extract and share:
     - `parse_scheduled_conv_id(conv_id: str) -> tuple[str, datetime] | None`
     - `extract_activity(path: Path) -> tuple[str, list[str]]`
     - `is_status_token(content: str) -> bool`
     - `collect_scheduled_activity(config, hours: int = 24, skill_name: str | None = None, exclude_skill_name: str | None = None) -> list[dict]`
   - Each activity record contains:
     - `skill_name: str`
     - `conv_id: str`
     - `started_at: str` (ISO 8601 UTC)
     - `final_message: str`
     - `vault_pages_touched: list[str]`

2. **Refactor Newsletter Skill (`src/decafclaw/skills/newsletter/tools.py`):**
   - Import and use the shared `collect_scheduled_activity`, `parse_scheduled_conv_id`, `extract_activity`, and `is_status_token` from `decafclaw.scheduled_activity`.
   - Maintain backwards-compatible aliases for existing tests (`_collect_scheduled_activity`, `_parse_conv_id`).

3. **Dream Skill Tools (`src/decafclaw/skills/dream/tools.py`):**
   - Implement `SkillConfig` with `window_days: int = 7`.
   - Implement `dream_recent_runs(ctx: "Context", days: int | None = None) -> ToolResult`.
   - Expose `TOOLS = {"dream_recent_runs": dream_recent_runs}` and `TOOL_DEFINITIONS`.
   - Tool description and parameters:
     - `days`: optional integer, defaults to 7.
     - Returns human-readable summary in `ToolResult.text` and structured records in `ToolResult.data["runs"]`.

4. **Date Bound on `conversation_search` (`src/decafclaw/tools/conversation_tools.py`):**
   - Update `tool_conversation_search(ctx: "Context", query: str, days: int = 0) -> str`.
   - If `days > 0`:
     - Filter out archives older than the cutoff (using `parse_scheduled_conv_id` or `st_mtime`).
     - Filter out message entries older than the cutoff when message timestamps are present.
   - If `days <= 0`: preserve exact existing behavior (unbounded search).
   - Update `CONVERSATION_TOOL_DEFINITIONS` parameter schema for `conversation_search` to add `days` (optional integer).

5. **Dream Skill Instructions (`src/decafclaw/skills/dream/SKILL.md`):**
   - Phase 1 (Orient): Call `dream_recent_runs` (default 7 days) to see recent topics and pages touched. Note pages and topics touched in the last 7 days and do not re-consolidate them unless new journal entries provide fresh facts.
   - Phase 2 (Gather): Pass `days=7` (or narrower) to `conversation_search` to avoid pulling historical conversations.

6. **Documentation Updates:**
   - `docs/tools.md`: Update `conversation_search` parameters (`days`).
   - `docs/skills.md`: Document `dream_recent_runs` tool under `dream` skill.

7. **Unit Tests & Evals:**
   - `tests/test_scheduled_activity.py`:
     - Test `parse_scheduled_conv_id`.
     - Test `collect_scheduled_activity` filtering by `skill_name`, `exclude_skill_name`, and time window.
     - Test `extract_activity` extracting final assistant message and touched vault pages.
   - `tests/test_dream_skill.py`:
     - Test `dream_recent_runs` tool registration via skill loader.
     - Test `dream_recent_runs` returns only dream runs within window with correct fields.
   - `tests/test_conversation_tools.py`:
     - Test `conversation_search` with `days` bound excludes older archives and entries.
     - Test `conversation_search` without `days` preserves default unbounded behavior.
   - Evals:
     - `evals/skills.yaml` or `evals/dream.yaml`: Add eval test verifying dream uses `dream_recent_runs` and respects recent-runs context.

## Explicit Exclusions & What We're NOT Doing

- No rolling vault page for dream logs (rejected: pruning requires LLM compliance).
- No changes to recent-journal surfacing in scheduled turns (rejected: affects all scheduled tasks).
- No changes to compaction or conversation archive formats.
