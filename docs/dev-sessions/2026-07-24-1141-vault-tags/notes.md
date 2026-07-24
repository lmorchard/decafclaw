# Notes: first-class vault tags (#318)

Session working log. Final summary goes here before the PR.

## Phase log
- Phase 1 — tags.py foundation: pending
- Phase 2 — inline #tags in composite embeddings: pending
- Phase 3 — journal inline #tags emission: pending
- Phase 4 — tag-filtered vault_search: pending
- Phase 5 — vault_tags() tool + /api/vault/tags: pending
- Phase 6 — Tags tab (web UI): pending

## Decisions (from brainstorm)
- Unify at index/query layer; journal daily-file format unchanged.
- Inline #tags = native per-entry mechanism (per-entry frontmatter rejected: Obsidian mid-file --- = <hr>).
- On-demand scan, no persistent tag index.
- AND default + any_tag=true; case-insensitive, no -/_ merge.
- Full feature, Tags tab last.
