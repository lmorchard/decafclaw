---
name: research_brief
description: "Research a topic and produce a short brief — demo of the workflow engine."
kind: workflow
user-invocable: true
argument-hint: "[start|status|abort] <topic>"
required-skills: [tabstack]
workflow:
  initial-phase: gather
---

Research a topic and produce a short written brief.

When invoked as `!research_brief start <topic>` or `/research_brief start <topic>`,
call `workflow_start` with `name="research_brief"`. The engine activates
the `tabstack` skill (declared in required-skills above) before any
phase runs, then dispatches the gather subagent which fetches sources.

After the gather subagent completes, call `phase_advance` to route
between phases. Use `workflow_status` if you ever lose track of where
you are. Use `workflow_abort` if you need to start over.

User said: $ARGUMENTS
