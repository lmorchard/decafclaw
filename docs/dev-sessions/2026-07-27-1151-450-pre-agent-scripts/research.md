# Research — #450 pre-agent scripts + response-sentinel unification

Load-bearing facts only; `file:line` against `38467c7`.

## Scheduled-task execution

- `schedules.py:429` `run_schedule_task` — the whole lifecycle.
- **Prompt assembly, `schedules.py:497-500`:**
  ```python
  preamble = build_task_preamble("scheduled task", task.name)
  body = substitute_body(task.body, skill_dir=skill_dir)
  loaded_skills = _render_required_skill_bodies(config, required_skills)
  prompt = preamble + (f"{loaded_skills}\n\n" if loaded_skills else "") + body
  ```
  One expression, one insertion point for a `<pre_script_output>` block.
- **Delivery, `schedules.py:513-521`:** `result_text = await future` → `ok = is_heartbeat_ok(result_text)` → `_notify_task_complete(...)`.
- `_notify_task_complete` (`schedules.py:542`) emits **one** inbox notification via `notifications.notify`. Channel adapters (Mattermost DM, email, vault page) are EventBus subscribers on that notification, so **suppressing here suppresses every channel** — no per-adapter work.
- `ScheduleTask` (`schedules.py:27`) is a frozen-style dataclass; frontmatter keys are read in `parse_schedule_file` (`schedules.py:59-96`). Adding a key touches both plus `serialize_to_markdown` (`schedules.py:188`) and `write_overlay` (`schedules.py:222`).

## The three response sentinels

| Sentinel | Helper | Match rule | What it gates today |
|---|---|---|---|
| `HEARTBEAT_OK` | `heartbeat.py:115` `is_heartbeat_ok` | substring, first 300 chars, case-insensitive | heartbeat: notification priority (`heartbeat.py:182`). Scheduled: **only** notification title + priority (`schedules.py:516`) — does *not* suppress |
| `BACKGROUND_WAKE_OK` | `heartbeat.py:126` `is_background_wake_ok` | **start-anchored** (leading whitespace only), first 300 chars, case-insensitive | wake-turn delivery (`conversation_manager.py:1541`) |
| `[SILENT]` | — | proposed | proposed: suppress scheduled delivery |

`is_background_wake_ok`'s docstring states start-anchoring exists because "requiring the sentinel at the start prevents mid-response mentions from accidentally" firing it. The newer sentinel was written to avoid the older one's defect.

## Start-anchoring is safe — the prompts already require it

`build_task_preamble` (`polling.py:36-79`) is the only producer of these instructions:

- **Heartbeat:** `"If there is nothing to report, respond with HEARTBEAT_OK."` — the response *is* the marker.
- **Scheduled:** `"begin your summary with HEARTBEAT_OK on its own line, followed by a brief note saying why."` — leading by explicit instruction, added deliberately in #362 so `is_heartbeat_ok`'s 300-char scan "still detects quiet cycles reliably."

So tightening the match to start-anchored aligns the code with what the prompt has always asked for. No prompt change needed.

## Unifying retires a live hazard

`agent.py:1220-1235` (from #711) documents why the loop-breaker's note must come *first* in an unwatched-turn join: `is_heartbeat_ok` scans the first 300 chars, and a mid-turn preamble mentioning the sentinel would land in that window and silently suppress the loop-breaker alert. The comment ends "Don't move this."

That ordering constraint exists **only** because the match is a substring. Start-anchoring removes the false-positive class, so the invariant stops being load-bearing. (Leave the ordering as-is — but the comment should note the constraint is now belt-and-braces rather than the sole defence.)

## Subprocess precedent

Only two non-test users: `tools/shell_tools.py` and `skills/claude_code/tools.py`. No existing pre-script mechanism anywhere, and the `ingest` skill has no `tools.py`. This is new machinery, not an extension of something.
