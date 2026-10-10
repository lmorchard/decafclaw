# Research: Issue 995 — Dream Skill Rehashing Cause

## Step 1: Investigation of Real `schedule-dream-*` Archives

### Hypothesis
The scheduled "dream" skill rehashes topics it consolidated on prior nights because:
1. Each nightly run starts as a fresh conversation (`schedule-dream-<YYYYMMDD-HHMMSS>`) with no memory of prior runs.
2. In Phase 2 (Gather), `skills/dream/SKILL.md` runs `conversation_search` with broad queries and no date bound. Because `conversation_search` is unbounded, it repeatedly matches older conversations from weeks or months earlier.
3. Dream has no visibility into what pages or topics it modified in recent runs (within the last 7 days).

### Archive Evidence
Inspection of real archives under `data/decafclaw/workspace/conversations/schedule-dream-*`:

1. **Broad, unbounded `conversation_search` queries:**
   Recent dream runs executed queries such as:
   - `conversation_search({"query": "preference OR decision OR update OR correction OR task OR note"})` (`schedule-dream-20260813-141816`)
   - `conversation_search({"query": "new information OR update OR decision OR preference"})` (`schedule-dream-20260818-200039`)
   - `conversation_search({"query": "corrections OR updates OR preferences OR decisions OR project context OR status changes OR recurring themes OR insights"})` (`schedule-dream-20260723-173257`)
   - `conversation_search({"query": "DecafClaw"})` (`schedule-dream-20260603-200020`, `schedule-dream-20260604-200052`, `schedule-dream-20260609-200045`, `schedule-dream-20260624-161315`)

2. **Matching months-old archives:**
   In `schedule-dream-20260813-141816` (August 13, 2026), the query:
   `conversation_search({"query": "preference OR decision OR update OR correction OR task OR note"})`
   matched conversations from May 2026:
   `--- [schedule-mastodon-ingest-20260518-133031] user ---`
   returning 3-month-old tasks that had long since been completed or consolidated.

3. **Duplicated page topics across consecutive runs:**
   In `schedule-dream-20260526-200000`, dream wrote:
   `agent/pages/Dream Skill Documentation Issue` (referencing `agent/journal/newsletters/2026-05-24`).
   On the very next evening, `schedule-dream-20260527-200032`, dream wrote:
   `agent/pages/Dream Skill Troubleshooting.md` rehashing the exact same topic and referencing the same journal entries `agent/journal/newsletters/2026-05-22`, `2026-05-23`, `2026-05-24`.

### Conclusion
The hypothesis is fully confirmed. Unbounded `conversation_search` returns stale historical conversations, and the lack of a recent-runs record causes dream to reprocess the same topics across consecutive nights.
