---
schedule: "15 */12 * * *"
enabled: false
model: default
required-skills:
  - meta-ingest
  - tabstack
allowed-tools: shell($SKILL_DIR/fetch.sh), shell($SKILL_DIR/fetch.sh *), workspace_read, vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, tabstack_extract_markdown, current_time, delegate_tasks
---

Time for the scheduled meta-ingestion. Follow the meta-ingest skill instructions to completion.
