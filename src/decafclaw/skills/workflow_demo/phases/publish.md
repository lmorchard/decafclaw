---
kind: inline
tools: [vault_write, workflow_artifact_read]
---

Read the final draft from `artifacts/draft/brief.md`. Write it to a new vault
page at `vault://briefs/{slug}.md` with appropriate frontmatter (title from
the topic, `tags: [research, brief]`, `summary` from the first paragraph).

Report the vault page path to the user. The workflow is now complete.
