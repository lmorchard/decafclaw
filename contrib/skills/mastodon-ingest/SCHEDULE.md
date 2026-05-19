---
schedule: "30 */12 * * *"
model: default
required-skills:
  - mastodon-ingest
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
---

Time for the scheduled Mastodon ingestion. Follow the mastodon-ingest skill instructions to completion.
