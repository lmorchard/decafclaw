---
schedule: "30 23 * * *"
allowed-tools: improvement_inbox_export
required-skills:
  - improvement-inbox
---

Nightly improvement-inbox sweep. Activate the `improvement-inbox` skill and
call `improvement_inbox_export` so pending improvement-scout findings land on
`agent/pages/decafclaw/improvement-inbox.md`. Then report how many items were
exported; do not dismiss or clear anything.

Note for operators: a contrib skill's schedule is advisory until this file is
copied into `data/{agent_id}/schedules/` as an admin schedule. Do not attempt
to enable it from inside the skill.
