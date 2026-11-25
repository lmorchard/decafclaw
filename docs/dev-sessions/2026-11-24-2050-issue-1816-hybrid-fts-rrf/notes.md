# issue-1816-hybrid-fts-rrf

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1816
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1816-hybrid-fts-rrf
- Branch: issue-1816-hybrid-fts-rrf
- Base: origin/main at fe6c9e34e030b20b84a79a7863e0715b7f46a0df
- Status: implemented, targeted tests and `make check` green (known macOS host-metrics failures only); PR pending.

## Log

- Added the `fts_pages` FTS5 table (porter unicode61) keyed by `memory_embeddings.id`, maintained by `index_entry_sync` and every delete/prune path, with a first-open backfill for existing databases.
- Added `search_fts_sync`: tokenizes the query to safe quoted OR terms, returns bm25-ranked rows with `id` and `bm25`.
- Added `_reciprocal_rank_fusion` in `memory_context`: merges by id, carries raw/normalized RRF scores and branch ranks. `retrieve_memory_context` runs both branches and fuses when the lexical branch has matches; dense-only fallback when FTS is empty, disabled, or errors.
- `score_candidates` treats `rrf_score_normalized` as authoritative composite relevance so the lexical boost survives scoring; recency/importance break ties. Retrieval telemetry carries `rrf_score`, `vector_rank`, and `fts_rank`.
- Config: `vault_retrieval.hybrid_enabled`, `rrf_k`, `fts_weight`, `vector_weight`, `hybrid_candidates`, with `VAULT_RETRIEVAL_*` env aliases.
- Tests: FTS index/maintenance/backfill/sanitization in `tests/test_embeddings.py`; RRF math, dedupe, exact-token rank 1, and fallbacks in `tests/test_memory_context.py`; hybrid scoring/threshold behavior in `tests/test_vault_retrieval_extension.py`.
- Docs: semantic-search, context-composer, config, extensions.
