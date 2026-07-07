# Phase 3 — Live smoke transcript

Date: 2026-07-06
Worktree: `.claude/worktrees/fix-613-research-timeout` on `fix/613-research-timeout` @ post-Phase-2 tip (eba7b36)
Model: `vertex-gemini-flash` (default for `/research`)
Conversation: `web-lmorchard-e1b647d0`

## Setup

- Worktree `.env` had `HTTP_PORT=18895`, `MATTERMOST_ENABLED=false`, `TABSTACK_API_KEY` enabled (from earlier session setup).
- Server: `uv run decafclaw` web-only on `0.0.0.0:18895`.
- Client: `decafclaw-client send`/`respond` against `http://localhost:18895`.

## Result: `/research` completes end-to-end

**Total wall-clock time: ~3 minutes** (from the second `user_input` response to `status="done"`).

Final journal state (`smoke-journal-snapshot.json`):

```json
{
  "status": "done",
  "seqs": ["0", "1", "2", "3", "3.0.0", "3.1.0", "4", "4.0.0", "4.1.0", "5"],
  "kinds": [
    "user_input", "user_input", "llm_call",
    "parallel", "tool_call", "tool_call",
    "pipeline", "llm_call", "llm_call",
    "subagent"
  ]
}
```

Contrast with #582's pre-fix smoke: `status="error"`, seqs missing the outer parallel `(3,)` entry.
Contrast with #613's pre-fix smoke: `status="error"`, all `tool_call` results were `[error: tool tabstack_research timed out after 180s]`.

Post-fix state:
- **2 queries** (LLM chose 2 within the new 2-3 bounds; schema `minItems: 2` accepted).
- **Both `tabstack_research` calls returned real markdown** — 5,260 chars and 4,012 chars respectively. NO timeout errors.
- **Outer parallel entry at seq `(3,)`** with the assembled results dict.
- **Pipeline stages** at `(4, 0, 0)` and `(4, 1, 0)` — one summarize `llm_call` per query.
- **Outer pipeline entry at seq `(4,)`** with the assembled summaries.
- **Subagent entry at seq `(5,)`** — the final report dict, titled "Restoring Our Underwater Forests: The Science and Value of Kelp Forest Restoration" with a 3,977-char markdown body.

## Timing observations

- **Tabstack per-call duration: ~3 minutes**. Both calls finished well under the 600s ceiling. The ceiling is generous — 5× typical wall-clock — which is the defensive posture we wanted.
- **Full workflow wall-clock: ~3 minutes**. Parallel stage dominates; pipeline + subagent are fast because they're single LLM calls each against the already-fetched content.
- **No `[error: timed out]` entries anywhere** in the journal. Contrasts with the #613 pre-fix smoke where all 3-5 parallel calls hit 180s and returned error text.

## Acceptance check

**#613 acceptance criteria (from the issue body + spec):**
- ✅ `/research` completes on real input (`status="done"`, real report dict returned).
- ✅ Tabstack calls return real content, not timeout errors.
- ✅ Journal advances through all stages: user_input × 2 → llm_call (plan) → parallel → tool_call × 2 → pipeline → summarize llm_call × 2 → subagent → done.
- ✅ Regression test in `tests/test_tool_timeout.py` guards the tabstack timeout override from silent removal.
- ✅ Existing unit tests (2982) still pass without modification.

**Load-bearing win:** `/research` is now a usable demo of #574's fan-out primitives. Before this fix, it hit the FIRST_EXCEPTION hang (#582), then the primitive-fixed but tool-timing-out state (#613's original repro), and only NOW reaches subagent synthesis with real content.

## Artifacts

- Post-run journal snapshot: `smoke-journal-snapshot.json` (sibling file).
- Server log: `/tmp/decafclaw-613-smoke.log` (last activity is the workflow-complete signal).
- Client log: `/tmp/decafclaw-613-final.log` (received the `message_complete` with the report).
