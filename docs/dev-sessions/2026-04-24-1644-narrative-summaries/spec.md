# Narrative summaries for scheduled skills — spec

Tracks: [#362](https://github.com/lmorchard/decafclaw/issues/362)

## Goal

Reframe the `HEARTBEAT_OK` escape hatch in the shared polling preamble and in scheduled skills' SKILL.md bodies so scheduled tasks always produce narrative. The token is preserved as a **leading marker** for quiet cycles (keeping existing consumers working) but paired with a mandatory narrative summary — including when "nothing notable" happened this cycle.

The token was adopted from Openclaw as a terse "nothing to report" signal. It has TWO real consumers in decafclaw:

1. **`heartbeat.py::is_heartbeat_ok()` (line 115)** — called by `heartbeat.py::run_section_turn` to gate alert-vs-all-clear notification priority ("Heartbeat completed" vs "Heartbeat: N alert(s)").
2. **The same function called by `schedules.py::run_schedule_task` (line 294)** — used to pick between a tidy `Schedule 'name': HEARTBEAT_OK` log line and the response-preview log line.

Both signals are legitimate. The #362 problem is specifically that scheduled skills emit a BARE token as the final assistant message, which crowds out real narrative for the newsletter composer.

**Fix:** require narrative always, but preserve `HEARTBEAT_OK` as a leading marker for quiet cycles in the scheduled branch. The marker goes FIRST (within the 300-char window `is_heartbeat_ok()` scans) so detection still works; the narrative follows so archives stay readable. Heartbeat keeps its original terser wording — it has no narrative-retrospective consumer.

## Why now

Surfaced during #283/#356 smoke-testing. The newsletter composer found 3 recently-run scheduled-task conversations but had "nothing to report" because each ended with just `HEARTBEAT_OK`. The newsletter workaround (`_is_status_token()`, PR #356) falls back to the previous assistant text — usually just the turn's opener ("I'll fetch the latest posts…") — which is thin narrative. The proper fix is upstream: get the skills to emit real narrative in the first place.

## Root cause

HEARTBEAT_OK is instructed in two places per scheduled turn:

1. **The shared preamble** built in `src/decafclaw/polling.py::build_task_preamble`, called by heartbeat (task_type `"scheduled heartbeat check"`) and scheduled tasks (task_type `"scheduled task"`). Both paths need the token preserved for `is_heartbeat_ok()` detection; the scheduled path additionally must demand narrative.
2. **Each scheduled SKILL.md body** — dream, garden, linkding-ingest, mastodon-ingest — ends with a "if nothing new, respond with HEARTBEAT_OK" fallback.

With both instructions framing the token as the DEFAULT quiet-cycle response, the model (especially on minor-activity cycles) interprets "nothing new" liberally and emits bare `HEARTBEAT_OK` as its final message instead of narrating.

## Design

### Scope

5 files, all prompt edits (no Python-logic changes):

- `src/decafclaw/polling.py` — rewrite one line of the preamble.
- `src/decafclaw/skills/dream/SKILL.md` — rewrite the ending instructions.
- `src/decafclaw/skills/garden/SKILL.md` — same.
- `contrib/skills/linkding-ingest/SKILL.md` — same.
- `contrib/skills/mastodon-ingest/SKILL.md` — same.

Plus one new unit test in `tests/test_polling.py` (create if absent) that pins the preamble text shape.

### Branched preamble

`polling.py` is shared between scheduled tasks AND heartbeat. Both consume the token via `is_heartbeat_ok()`, but they have different UX needs: heartbeat is fine with a bare token (quick status check); scheduled tasks need narrative for the newsletter.

So `build_task_preamble` branches on `task_type`:

- **Heartbeat path** (task_type contains `"heartbeat"`) — unchanged terse wording: "If there is nothing to report, respond with HEARTBEAT_OK."
- **Scheduled path** — always-narrate AND preserve the token as a leading marker for quiet cycles: "End your turn with a short narrative summary of what you did this cycle. If the cycle was quiet — nothing notable happened, no changes made — begin your summary with HEARTBEAT_OK on its own line, followed by a brief note saying why. Otherwise, just describe the actual activity."

The leading position of the marker keeps `is_heartbeat_ok()` detection reliable (it scans the first 300 chars). The narrative follows so archived scheduled-task conversations stay readable for retrospective tools.

### Preamble rewrite

File: `src/decafclaw/polling.py`, inside `build_task_preamble()`.

Branch the closing instruction on whether `"heartbeat"` appears in `task_type.lower()`:

- **Heartbeat path** keeps the existing wording: `"If there is nothing to report, respond with HEARTBEAT_OK.\n"`
- **Scheduled path** uses: `"End your turn with a short narrative summary of what you did this cycle. If the cycle was quiet — nothing notable happened, no changes made — begin your summary with HEARTBEAT_OK on its own line, followed by a brief note saying why. Otherwise, just describe the actual activity.\n"`

The surrounding structure (greeting line, "Execute the following task…", workspace-tools preference) stays identical across both paths.

### SKILL.md body rewrites

Each of the four SKILL.md files ends with a conditional "respond with HEARTBEAT_OK" fallback. Replace each with a single narrative-required instruction that **keeps HEARTBEAT_OK as a leading marker for quiet cycles** — so `is_heartbeat_ok()` detection still works for the log-line tidiness signal in `schedules.py::run_schedule_task`.

**`src/decafclaw/skills/dream/SKILL.md`** — current tail:

```
- If you made changes, summarize what you consolidated and any new pages created.
- If there was nothing new to consolidate, respond with HEARTBEAT_OK.
```

Proposed:

```
End with a short narrative summary: what you consolidated this cycle and any new pages created. If the journal was quiet and nothing new came up, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.
```

**`src/decafclaw/skills/garden/SKILL.md`** — current tail:

```
- Summarize what you tidied: pages merged, links fixed, summaries added, etc.
- If the vault is already in good shape, respond with HEARTBEAT_OK.
```

Proposed:

```
End with a short narrative summary of what you tidied: pages merged, links fixed, summaries added, etc. If the vault was already in good shape and nothing needed attention, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.
```

**`contrib/skills/linkding-ingest/SKILL.md`** — current tail:

```
After all delegates complete, summarize what was processed and what wiki pages were updated or created.
If there was nothing interesting to ingest, respond with HEARTBEAT_OK.
```

Proposed:

```
After all delegates complete, end with a short narrative summary of what was processed and what wiki pages were updated or created. If there was nothing interesting to ingest this cycle, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.
```

**`contrib/skills/mastodon-ingest/SKILL.md`** — current tail:

```
If you made vault changes, summarize what you added/updated.
If there was nothing interesting to ingest, respond with HEARTBEAT_OK.
```

Proposed:

```
End with a short narrative summary of what you added or updated in the vault. If there was nothing interesting to ingest this cycle, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.
```

## Testing

### Unit tests

Extend `tests/test_polling.py` with two tests pinning each branch:

- `test_build_task_preamble_heartbeat_keeps_status_token` — calls `build_task_preamble("heartbeat check")` and asserts `"HEARTBEAT_OK" in result`. Pins that heartbeat's alert-vs-OK gating stays intact.
- `test_build_task_preamble_scheduled_requires_narrative_with_marker` — calls `build_task_preamble("scheduled task")` and asserts `"HEARTBEAT_OK" in result` AND `"narrative summary" in result`. Pins BOTH invariants: scheduled tasks get narrative, and the quiet-cycle marker is preserved (as a leading marker instruction, not a bare-token fallback) so `is_heartbeat_ok()` detection in `schedules.py` still works.

Also update existing `tests/test_heartbeat.py::test_build_section_prompt_*` which exercise heartbeat's `build_section_prompt` (which calls `build_task_preamble("scheduled heartbeat check")`): they should assert `"HEARTBEAT_OK" in prompt` (heartbeat path).

These tests catch accidental re-introduction of the token into the scheduled path OR accidental removal from the heartbeat path.

### Existing tests

All existing `pytest` tests must still pass. Particularly: any tests asserting specific preamble substrings or asserting that `HEARTBEAT_OK` appears in scheduled-task archives (unlikely but worth grepping for during implementation) will need updating.

### Manual verification

The real signal is LLM behavior, which no automated test can cover. Success criterion after merge:

- Wait a cycle for scheduled tasks to run (dream at 3am, garden Sunday 3am, linkding/mastodon every ~4h if configured on your system).
- Run `!newsletter 48h` and eyeball: do the scheduled-task entries now show real narrative content in `final_message`, instead of `HEARTBEAT_OK` workaround fallbacks?

## Out of scope

- **Removing newsletter's `_is_status_token()` filter.** Keeping as defense-in-depth for historical archives — `workspace/conversations/schedule-*.jsonl` files produced before this change still contain bare `HEARTBEAT_OK` endings, and the filter keeps the newsletter working correctly for retrospective windows (`!newsletter 7d` a week after merge). A follow-up can remove the filter once we're confident no relevant archives carry stale tokens.
- **Workspace-level user skills.** If a user has their own scheduled skills in `workspace/schedules/` or similar, they're user-owned and outside this repo. Users can update them on their own schedule.
- **Migrating contrib skills to bundled.** The contrib tier exists for a reason; that's a separate concern.
- **Changing the heartbeat subsystem's own orchestration.** Only the prompt shape changes here.

## Success criteria

- `make lint && make typecheck && make test` clean, including the two new preamble unit tests.
- `"HEARTBEAT_OK"` still appears in all four scheduled SKILL.md bodies AND in both branches of `build_task_preamble` — it was never removed, just reframed as a "leading marker for quiet cycles" rather than a "bare-token fallback."
- Existing `is_heartbeat_ok` tests in `tests/test_heartbeat.py` continue to pass — heartbeat's gating preserved.
- Existing `test_schedules.py::test_heartbeat_ok_detected` continues to pass — scheduled-task `is_ok`/log-line signal preserved.
- Existing `_is_status_token()` tests in `tests/test_newsletter_skill.py` continue to pass (filter is unchanged; still correct defensive behavior for historical archives).
- Post-merge smoke test: `!newsletter 48h` shows scheduled-task `final_message` values containing real narrative (not bare tokens). Quiet-cycle scheduled runs should begin with `HEARTBEAT_OK` followed by narrative — the scheduler's log line still reads `Schedule 'name': HEARTBEAT_OK`.
