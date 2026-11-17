---
name: improvement-inbox
description: Triage the improvement scout's silent JSONL inbox — list, inspect, summarize, dismiss, archive, export to the vault, and run whole-session retrospectives.
---

# Improvement inbox

The improvement scout extension buffers out-of-band friction suggestions in
`workspace/improvement_inbox.jsonl` (one JSON object per line). This skill is
the explicit counterpart: it never talks to the active session on its own, and
only runs when you are asked to inspect or manage the queue.

Inbox records use the statuses `pending`, `exported`, `dismissed`, and
`archived`. Mutations preserve the rest of the file and its audit history.

## Tools

- **`improvement_inbox_list`** — list items. Defaults to `pending`; pass
  `status: "all"` to see every status, or filter by `subsystem` / `severity`.
- **`improvement_inbox_show`** — full detail for one `item_id`, including the
  observed friction, suggested improvement, trigger, and conversation id.
- **`improvement_inbox_summary`** — compact rollup: counts by subsystem and
  severity plus recurring themes across titles and friction descriptions.
- **`improvement_inbox_dismiss`** — mark one or more `item_ids` as dismissed
  with an optional reason (for example `"Duplicate of #130"`). The record stays
  in the file for audit.
- **`improvement_inbox_clear`** — archive items older than `older_than_days`
  (default 30), or delete them when `archive: false`.
- **`improvement_inbox_export`** — append new pending items to
  `agent/pages/decafclaw/improvement-inbox.md` and mark them `exported`.
  Re-running it never duplicates an item already on the page.
- **`improvement_retro`** — ask the evaluator model to scan the current (or a
  named) conversation's full history for subtler multi-turn friction and buffer
  any finding as a new pending item.

## Working notes

- Prefer `improvement_inbox_list` and `improvement_inbox_show` before
  dismissing anything; a repeated symptom may already be one item.
- Export when you want the queue visible in the vault. Export is append-only,
  so dismissed or archived items are not removed from the page.
- `SCHEDULE.md` describes a nightly export sweep. Contrib schedules are
  discovery-only until the operator copies this skill's schedule into the
  admin schedules directory (`data/{agent_id}/schedules/`); do not try to
  enable it from inside the skill.
