# Narrative summaries for scheduled skills — Implementation Plan

> **Note:** This plan reflects the **final shipped design** after two rounds of scope correction during execution. The initial plan proposed dropping `HEARTBEAT_OK` entirely from the scheduled path; the branch self-review then Copilot's review each surfaced a real consumer (`heartbeat.is_heartbeat_ok()` called from both `heartbeat.py` and `schedules.py`) that requires the token to keep detecting quiet cycles. The final design preserves the token in both preamble branches and reframes the scheduled-path SKILL.md bodies to emit it as a leading marker for quiet cycles rather than as a bare-token fallback. Session notes (`notes.md`) capture the pivot reasoning.

**Goal:** Reframe the `HEARTBEAT_OK` escape hatch so scheduled tasks always end with real narrative. The token stays as a leading marker for quiet cycles (so `is_heartbeat_ok()` detection keeps working); narrative is mandatory either way.

**Architecture:** Pure prompt edits to one Python file (`polling.py` — branch `build_task_preamble` on `task_type`) and four markdown files (scheduled SKILL.md finishing blocks). Plus two new unit tests pinning both preamble branches, and doc updates for `docs/schedules.md` and `docs/dream-consolidation.md`. No code-logic changes outside the preamble branching.

**Tech Stack:** Python / pytest. Markdown editing.

**Reference files:**
- Spec: [`docs/dev-sessions/2026-04-24-1644-narrative-summaries/spec.md`](spec.md)
- Session retro: [`docs/dev-sessions/2026-04-24-1644-narrative-summaries/notes.md`](notes.md)
- Preamble: `src/decafclaw/polling.py::build_task_preamble`
- Heartbeat consumer: `src/decafclaw/heartbeat.py::is_heartbeat_ok` (line 115) + `run_section_turn` (line 182)
- Scheduler consumer: `src/decafclaw/schedules.py::run_schedule_task` (line 294)

**Key invariants:**
- `HEARTBEAT_OK` is PRESERVED everywhere it existed — both preamble branches and all four scheduled SKILL.md bodies. The CHANGE is in framing: bare-token fallback → leading marker for quiet cycles paired with mandatory narrative.
- `is_heartbeat_ok()` detection continues to work for BOTH consumers (heartbeat cycle's alert-vs-OK gating AND schedules.py's log-line tidiness).
- Newsletter's `_is_status_token` filter is UNCHANGED (defense-in-depth for historical archives).
- Scheduled archives' final assistant messages now always contain real narrative, fixing the #362 symptom.

---

## Final shape of each touched file

### `src/decafclaw/polling.py::build_task_preamble`

Branched on whether `"heartbeat"` appears in `task_type.lower()`:

- **Heartbeat path** — unchanged terse wording: "If there is nothing to report, respond with HEARTBEAT_OK."
- **Scheduled path** — always-narrate AND preserve the token as a leading marker for quiet cycles: "End your turn with a short narrative summary of what you did this cycle. If the cycle was quiet — nothing notable happened, no changes made — begin your summary with HEARTBEAT_OK on its own line, followed by a brief note saying why. Otherwise, just describe the actual activity."

Leading position keeps `is_heartbeat_ok()`'s 300-char detection reliable. Narrative follows so archived scheduled-task conversations stay readable for retrospective tools like `!newsletter`.

### Four scheduled SKILL.md bodies

Each skill's finishing block is rewritten from `"if changes: summarize; else: respond with HEARTBEAT_OK"` to a single instruction: `"end with narrative; if quiet, begin with HEARTBEAT_OK on its own line then narrative"`. Tailored to each skill's domain vocabulary (dream = consolidated, garden = tidied, linkding = processed, mastodon = added/updated).

### Tests

- `tests/test_polling.py`:
  - `test_build_task_preamble_heartbeat_keeps_status_token` — heartbeat branch asserts `"HEARTBEAT_OK" in result`.
  - `test_build_task_preamble_scheduled_requires_narrative_with_marker` — scheduled branch asserts BOTH `"HEARTBEAT_OK" in result` AND `"narrative summary" in result`.
- `tests/test_heartbeat.py::test_build_section_prompt_*` — pre-existing tests, still assert `"HEARTBEAT_OK" in prompt` (heartbeat path).
- `tests/test_schedules.py::test_heartbeat_ok_detected` — pre-existing, unchanged. Still pins scheduled-task `is_ok` signal.

### Docs

- `docs/schedules.md`: rewrote the "Final summary" section and the silent-health-check example to describe the narrative-plus-marker pattern.
- `docs/dream-consolidation.md`: updated Finishing description.
- `docs/heartbeat.md`: unchanged (heartbeat uses the token the same way it always has).

---

## Execution record

This plan was executed in a single implementer pass via `superpowers:subagent-driven-development` (one dispatch covering all tasks sequentially — proportional to the small scope). The implementer caught an additional stale test assertion in `tests/test_heartbeat.py` during TDD and flipped it inline.

The initial commit series (pre-squash) was:

1. `docs(narrative-summaries): dev session spec (#362)`
2. `docs(narrative-summaries): implementation plan (#362)`
3. `fix(polling): drop HEARTBEAT_OK from scheduled-task preamble (#362)` — round 1, too aggressive
4. `fix(skills): drop HEARTBEAT_OK fallback from scheduled-skill finishing blocks (#362)`
5. `fix(polling): preserve HEARTBEAT_OK for heartbeat, drop only for scheduled (#362)` — round 2 pivot (branch self-review caught the heartbeat consumer)
6. `docs: update schedules + dream docs for always-narrate scheduled (#362)`

After Copilot review caught the `schedules.py` consumer, the final pivot rewrote commits 3-6 into the single squash commit: `fix(scheduled): always-narrate with quiet-cycle marker (#362)` — the one that landed in PR #368.

## Verification checklist (at HEAD)

- [x] `make lint && make typecheck && make test` all green (1894 tests)
- [x] Two new `tests/test_polling.py` tests pin both preamble branches
- [x] `tests/test_heartbeat.py::test_build_section_prompt_*` and `tests/test_schedules.py::test_heartbeat_ok_detected` still pass — both consumers preserved
- [x] `HEARTBEAT_OK` still appears in all four SKILL.md bodies (leading-marker framing) and in both preamble branches
- [x] `docs/schedules.md` and `docs/dream-consolidation.md` reflect the narrative-plus-marker pattern
- [ ] Manual (post-merge): wait for dream's next run, then `!newsletter 24h` — confirm scheduled-task entries show real narrative in `final_message`. Quiet cycles should still produce tidy `Schedule 'name': HEARTBEAT_OK` log lines.
- [ ] Manual (post-merge): heartbeat cycles still route as "Heartbeat completed" (normal priority) when all sections return HEARTBEAT_OK, not "Heartbeat: N alert(s)".
