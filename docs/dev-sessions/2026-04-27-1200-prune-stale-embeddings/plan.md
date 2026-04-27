# Plan

See `spec.md` for decisions.

## Phase 1 — code + tests

- `prune_stale_embeddings(config) -> dict[str, int]` in
  `src/decafclaw/embeddings.py`. Iterate rows; classify by
  source_type; drop via existing `delete_entries` (one row at a
  time so embeddings_vec cleanup runs correctly via the
  existing helper).
- `prune_embeddings_cli` mirroring `reindex_cli`'s shape.
- Register `decafclaw-prune-embeddings` in `pyproject.toml`.
- `make prune-embeddings` target in Makefile.
- Tests in `tests/test_embeddings.py`: live page, missing page,
  live journal, missing journal, conversation legacy, memory
  legacy, unknown source type, idempotent re-run.

## Phase 2 — docs

- `docs/semantic-search.md`: a "Pruning stale entries" subsection
  describing what the sweep does and when to run it.
- `CLAUDE.md`: append `make prune-embeddings` to the Running
  block.

## Phase 3 — squash, push, PR, request Copilot

`Closes #305`. Note the deferred compaction hook in the PR
description.
