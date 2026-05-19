---
schedule: "45 */12 * * *"
model: default
required-skills:
  - linkding-ingest
  - tabstack
allowed-tools: shell($SKILL_DIR/fetch.sh), shell($SKILL_DIR/fetch.sh *), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, tabstack_extract_markdown, current_time, delegate_tasks
---

Time for the scheduled Linkding bookmark ingestion. Follow the linkding-ingest skill instructions to completion.
