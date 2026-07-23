---
schedule: "0 */4 * * *"
model: default
required-skills:
  - rss-ingest
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
---

Time for the scheduled RSS ingestion. Follow the rss-ingest skill instructions to completion.
