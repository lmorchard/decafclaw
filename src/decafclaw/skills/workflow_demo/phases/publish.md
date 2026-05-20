---
kind: inline
tools: [vault_write, workflow_artifact_read, workflow_status]
---

Read the final draft from `artifacts/draft/brief.md` via `workflow_artifact_read`.
Write it to a new vault page at `briefs/<slug>` (use the current workflow run's
slug as the filename component — call `workflow_status` if you need to look it
up, or fall back to slugifying the topic). The `.md` suffix is added by the tool.

Write with appropriate frontmatter:

```yaml
---
title: <topic title>
tags: [research, brief]
summary: <first paragraph of the brief>
---
```

Use `vault_write` to create the page. Report the vault page path to the user.
The workflow is now complete.
