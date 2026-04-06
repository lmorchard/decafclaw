# Vault Rename — Spec

Mechanical rename of wiki/memory context terminology to vault_references/vault_retrieval.

- `wiki_context` → `vault_references` (explicit page injection via @[[Page]] or sidebar)
- `memory_context` → `vault_retrieval` (semantic search, auto-injected)

Module file `memory_context.py` stays as-is (named for purpose, not the old terminology).

Ripple: context_composer.py, agent.py, archive.py, compaction.py, events, web UI JS, mattermost display, config_types.py, CLAUDE.md, docs.
