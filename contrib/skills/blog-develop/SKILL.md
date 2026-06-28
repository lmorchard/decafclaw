---
name: blog-develop
description: On-demand workflow (/blog-develop <idea>) that develops any blog-post idea into a take-or-leave first draft — scout → interview → deep research → draft, with research and drafting run as child agents.
effort: strong
required-skills:
  - vault
  - tabstack
user-invocable: true
context: fork
allowed-tools: delegate_task, vault_read, vault_write, vault_list, vault_search, current_time, tabstack_research, tabstack_extract_markdown, web_fetch
---

# Develop a blog idea

Stub. Phases: scout, interview, research, draft. Workers run via
delegate_task and CANNOT write to the vault — you write the draft to
blog/drafts yourself. Interview asks one question per turn.
