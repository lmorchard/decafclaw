---
name: research_brief
description: "Research a topic and produce a short brief — demo of the workflow engine."
kind: workflow
user-invocable: true
argument-hint: "[start|list|switch|status] <topic>"
workflow:
  initial-phase: gather
---

Research a topic and produce a short written brief.

When invoked as `!research_brief start <topic>` or `/research_brief start <topic>`,
call `workflow_start` with name=`research_brief` and slug derived from the topic.
After that, call `phase_advance` to route between phases based on what each phase
produces. Use `workflow_status` if you ever lose track of where you are.

User said: $ARGUMENTS
