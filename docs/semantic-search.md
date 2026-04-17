# Semantic Search

DecafClaw uses embedding-based semantic search for finding relevant memories and past conversations. This goes beyond substring matching — it finds results based on meaning, not exact keywords.

## How it works

1. Text is sent to an embedding API (default: `text-embedding-004` via LiteLLM)
2. The resulting vector is stored in a SQLite database using [sqlite-vec](https://github.com/asg017/sqlite-vec)
3. On search, the query is embedded and compared using SIMD-accelerated cosine distance via sqlite-vec's `vec0` virtual table
4. Top-K most similar results are returned

## Storage

Embeddings are stored in `data/{agent_id}/workspace/embeddings.db` — a SQLite database with:

- `memory_embeddings` table: text metadata (entry text, file path, source type, created timestamp)
- `embeddings_vec` virtual table (`vec0`): embedding vectors with cosine distance metric, keyed by rowid matching `memory_embeddings.id`
- `metadata` table: tracks which embedding model and dimensions were used (warns on mismatch)

The `vec0` table handles vector storage and similarity search in native C with SIMD acceleration. The `memory_embeddings` table holds only metadata — no embedding BLOBs in new databases.

Four source types are indexed:

| Source | What's indexed | Boost | When |
|--------|---------------|-------|------|
| `page` | Agent vault pages | 1.3x | On `vault_write`, or via reindex |
| `user` | User's Obsidian pages | 1.2x | Via reindex |
| `journal` | Agent journal entries | 1.0x | On `vault_journal_append`, or via reindex |
| `conversation` | User and assistant messages | 1.0x | During conversation (if embedding model configured) |

Agent pages and user pages receive similarity boosts so curated knowledge ranks above raw entries at equal semantic distance.

## Configuration

Set in `.env` or environment:

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `text-embedding-004` | Model name for the embedding API |
| `EMBEDDING_URL` | Falls back to `LLM_URL` | Embedding API endpoint |
| `EMBEDDING_API_KEY` | Falls back to `LLM_API_KEY` | API key for embeddings |
| `EMBEDDING_DIMENSIONS` | `768` | Vector dimensions (must match your embedding model) |
| `MEMORY_SEARCH_STRATEGY` | `substring` | `substring` or `semantic` for memory search |

When `MEMORY_SEARCH_STRATEGY=semantic`, `vault_search` uses embeddings. Otherwise it falls back to case-insensitive substring matching. Conversation search always uses embeddings when an embedding model is configured.

## Tools

- **`vault_search`** — searches vault pages, journal entries, and user notes (semantic or substring)
- **`conversation_search`** — searches past conversation archives using semantic search

## CLI tools

```bash
make reindex              # Rebuild all embeddings from memory files + conversation archives
decafclaw-search "query"  # Search the embedding index from the command line
```

Options for `decafclaw-search`:
- `--type page|journal|user|conversation|all` — filter by source type (default: all)
- `--top-k N` — number of results (default: 5)

## Reindexing

The index is rebuilt automatically if empty when a search is performed. To force a full rebuild:

```bash
make reindex
```

This deletes the existing database and re-embeds all vault pages, journal entries, and conversation archives. Useful after changing the embedding model/dimensions or if the index gets corrupted. Supports `--vault`, `--journal` flags for subset reindexing and `--concurrency N` for parallel API calls.

## Related

- [Vault](vault.md) — the unified knowledge base indexed by semantic search
- [Context Composer](context-composer.md#vault-retrieval) — vault retrieval, relevance scoring, token budget
