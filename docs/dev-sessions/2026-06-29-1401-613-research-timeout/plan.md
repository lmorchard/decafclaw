# /research timeout fix Implementation Plan

**Goal:** Add `"timeout": 600` to `tabstack_research`'s definition and lower `/research`'s parallel-query count from 3-5 to 2-3. Add a regression test that guards the tabstack timeout override from silent removal. Live-smoke `/research` end-to-end on Flash to confirm the workflow now reaches `status="done"` with a real report.

**Approach:** Two small independent commits (timeout config + query-count reduction), then a live smoke. The changes compose but don't share files, so keeping them as separate phases keeps each independently revertable. TDD by default: Phase 1 has a regression test that fails pre-fix; Phase 2's regression is the live smoke.

**Tech stack:** Python 3.12. Changes in `src/decafclaw/skills/tabstack/tools.py`, `src/decafclaw/workflow/workflows/research.py`, `tests/test_tool_timeout.py`.

---

## Phase 1: `tabstack_research` timeout override + regression test

TDD: write the regression test first (fails because `timeout: 600` isn't in the definition yet), then add the key.

**Files:**
- Modify: `src/decafclaw/skills/tabstack/tools.py` — add `"timeout": 600,` to the tabstack_research dict at lines 525-546.
- Modify: `tests/test_tool_timeout.py` — add `test_tabstack_research_has_configured_timeout`.

**Key changes:**

In `src/decafclaw/skills/tabstack/tools.py`, find the `tabstack_research` entry in `TOOL_DEFINITIONS` (around line 525-546 per research.md §3). It currently has keys like `"name"`, `"description"`, `"parameters"`. Add `"timeout": 600,` as a sibling key. Match the placement pattern from `src/decafclaw/tools/delegate.py:546` (`"timeout": None,` sibling of the other keys). Add a one-line comment explaining WHY the override exists so a future refactor doesn't silently strip it:

```python
{
    "name": "tabstack_research",
    "description": "...",  # unchanged
    "parameters": {...},   # unchanged
    # Iterative research can take multiple minutes on broad topics
    # (multi-query search + page analysis across 3 iterations); the
    # 180s TOOL_TIMEOUT_SEC default fires mid-flight and returns
    # "[error: timed out]" while the tool is making progress. 600s
    # gives ~3-5x headroom for typical runs while bounding runaway
    # iteration bugs at 10 minutes. See #613 / PR #582 smoke.
    "timeout": 600,
},
```

In `tests/test_tool_timeout.py`, add a new test mirroring the existing per-tool-override coverage. The regression test asserts the specific configured value:

```python
def test_tabstack_research_has_configured_timeout():
    """Regression guard for #613: tabstack_research's iterative research
    takes longer than the 180s TOOL_TIMEOUT_SEC default; the definition
    carries an explicit 600s timeout. If a future tabstack refactor drops
    the override, this test flags it before /research silently regresses
    to timing out again."""
    from decafclaw.skills.tabstack.tools import TOOL_DEFINITIONS

    entry = next(
        (d for d in TOOL_DEFINITIONS if d.get("name") == "tabstack_research"),
        None,
    )
    assert entry is not None, "tabstack_research not in TOOL_DEFINITIONS"
    assert entry.get("timeout") == 600, (
        f"expected timeout=600, got {entry.get('timeout')!r}"
    )
```

If the existing test file already imports `TOOL_DEFINITIONS` from other tool modules, mirror that import style. Otherwise import at the top of the test (the tabstack module doesn't have any import-time side effects to worry about).

**Verification — automated:**
- [ ] `cd .claude/worktrees/fix-613-research-timeout && make lint`
- [ ] `make check`
- [ ] `make test` (baseline 2981 + 1 new = 2982)
- [ ] `uv run pytest tests/test_tool_timeout.py -v -k tabstack` — the new test passes.
- [ ] Pre-fix sanity: temporarily revert the `"timeout": 600,` addition; the new test should fail with `expected timeout=600, got None`. (Don't commit the revert; just confirm the test exercises the config.)

**Verification — manual:**
- [ ] Read the whole `tabstack_research` dict block after edit. Confirm the comment explaining the 600s value is in place and readable to a future maintainer.

---

## Phase 2: Lower `/research` parallel-query count to 2-3

Three coordinated edits in `src/decafclaw/workflow/workflows/research.py`. The schema, system prompt, and user prompt must stay consistent so the LLM's intent aligns with the structured-output budget.

**Files:**
- Modify: `src/decafclaw/workflow/workflows/research.py` — schema bounds, system prompt, user prompt.
- (No test changes — verified during plan-phase that `tests/test_workflow_research.py` mocks use 2 and 3 queries, both within the new 2-3 cap.)

**Key changes:**

In `src/decafclaw/workflow/workflows/research.py`, three edits:

1. **`_PLAN_SCHEMA` bounds** (currently at lines 43-44 per research.md §4):
   ```python
   # OLD
   "minItems": 3,
   "maxItems": 5,
   # NEW
   "minItems": 2,
   "maxItems": 3,
   ```

2. **`_SYS_PLAN` system prompt** (currently at lines 22-26):
   ```python
   # OLD
   _SYS_PLAN = (
       "You plan focused research sweeps. Given a topic and any scope notes, "
       "generate 3-5 search queries that together cover the topic without "
       "overlap. Each query should be specific enough to return a useful "
       "single-page result."
   )
   # NEW
   _SYS_PLAN = (
       "You plan focused research sweeps. Given a topic and any scope notes, "
       "generate 2-3 search queries that together cover the topic without "
       "overlap. Each query should be specific enough to return a useful "
       "single-page result."
   )
   ```

3. **User prompt** in `_research_plan_prompt` (currently at lines 78-79):
   ```python
   # OLD
   lines.append(
       "Generate 3-5 search queries that together cover this topic.")
   # NEW
   lines.append(
       "Generate 2-3 search queries that together cover this topic.")
   ```

All three "3-5" strings become "2-3." No other prompt or comment changes.

**Verification — automated:**
- [ ] `make lint`
- [ ] `make check`
- [ ] `make test` — test count unchanged at 2982; `tests/test_workflow_research.py` still passes (its mocks use 2 and 3 queries which fit the new 2-3 cap).
- [ ] `uv run pytest tests/test_workflow_research.py -v` — all 5+ tests pass without modification.

**Verification — manual:**
- [ ] `grep -n '3-5\|3–5' src/decafclaw/workflow/workflows/research.py` — should return nothing (no leftover "3-5" language).
- [ ] `grep -n '2-3\|2–3' src/decafclaw/workflow/workflows/research.py` — should return exactly 2 occurrences (system prompt + user prompt). Schema is bounded numerically, not textually.

---

## Phase 3: Live smoke + session artifacts

Walk `/research kelp forest restoration` on `vertex-gemini-flash` from the worktree server. Confirm the workflow reaches `status="done"` with a real report dict returned to the client — the acceptance criterion from #613.

**Files:**
- Create: `docs/dev-sessions/2026-06-29-1401-613-research-timeout/smoke.md` — transcript.
- Create: `docs/dev-sessions/2026-06-29-1401-613-research-timeout/smoke-journal-snapshot.json` — post-run journal.
- Modify: `docs/dev-sessions/2026-06-29-1401-613-research-timeout/notes.md` — append per-phase notes and retro.

**Smoke walk:**

1. `cd /Users/lorchard/devel/decafclaw/.claude/worktrees/fix-613-research-timeout`
2. `nohup uv run decafclaw > /tmp/decafclaw-613-smoke.log 2>&1 &`
3. Wait for `Uvicorn running on http://0.0.0.0:18895` in the log.
4. `export DECAFCLAW_TOKEN=$(jq -r 'keys[0]' /Users/lorchard/devel/decafclaw/data/decafclaw/web_tokens.json) DECAFCLAW_HOST="http://localhost:18895"`.
5. `uv run decafclaw-client send --prompt "/research kelp forest restoration" --format jsonl --timeout 60` — expect the first user_input suspension.
6. Respond with a topic (e.g., "kelp forest restoration techniques").
7. Respond with a scope (e.g., "for a general audience").
8. Poll the journal at `data/decafclaw/workspace/conversations/{conv_id}/workflow.json` every ~60s.
9. Expected journal state at completion:
   - `status: "done"` (not `"error"` as in the #582 smoke).
   - Outer parallel entry at seq `"3"` with 2-3 NON-error result dicts (real tabstack markdown, 3-8KB each — NOT `[error: ...timed out...]`).
   - Pipeline summarize entries at seq `"4.0.0"`, `"4.1.0"`, ... (one per query).
   - Outer pipeline entry at seq `"4"`.
   - Subagent entry at seq `"5"` with the final report dict.
10. Client receives a `message_complete` with the report title + body — NOT the fail-fast RuntimeError from the #582 smoke.
11. Capture the transcript in `smoke.md`, save the journal in `smoke-journal-snapshot.json`.

**Notes for smoke.md:**

- Reference the #582 smoke's `smoke-journal-snapshot.json` as the "before" state (all-error tool_calls, `status="error"`) versus this smoke's "after" state (real markdown, `status="done"`).
- Note the actual wall-clock time to complete (for the tabstack-timeout headroom sanity check — is 600s ceiling generous enough, or was it close?).
- If tabstack still takes >600s per call and the workflow times out despite the fix, flag as: (a) rerun with a narrower topic to confirm fix works on typical topics; (b) file a follow-up to bump the ceiling further or reconsider option 3 (lighter search tool).

**Verification — automated:**
- [ ] `make check` — still clean.
- [ ] `make test` — still 2982 passing.

**Verification — manual:**
- [ ] Live `/research` walk completes with `status="done"` and a report dict — not the fail-fast RuntimeError from #582.
- [ ] Inspect the journal: no `[error: tool tabstack_research timed out after 180s]` entries; instead, 2-3 substantive markdown result dicts.
- [ ] Wall-clock time for the parallel stage is under 600s (confirms the ceiling is generous enough for typical topics).
- [ ] Server log: no "Task exception was never retrieved" warnings; clean shutdown after the workflow completes.
- [ ] Update #613 comment with the merged-by-PR link once the PR opens.
