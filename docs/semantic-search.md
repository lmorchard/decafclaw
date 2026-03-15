# Semantic Search

DecafClaw uses embedding-based semantic search for finding relevant memories and past conversations. This goes beyond substring matching — it finds results based on meaning, not exact keywords.

## How it works

1. Text is sent to an embedding API (default: `text-embedding-004` via LiteLLM)
2. The resulting vector is stored in a SQLite database alongside the text
3. On search, the query is embedded and compared against stored vectors using cosine similarity
4. Top-K most similar results are returned

## Storage

Embeddings are stored in `data/{agent_id}/workspace/embeddings.db` — a SQLite database with:

- `memory_embeddings` table: text, embedding vector (as binary blob), source type, file path
- `metadata` table: tracks which embedding model was used (warns on mismatch)

Two source types are indexed:

| Source | What's indexed | When |
|--------|---------------|------|
| `memory` | Memory file entries | On save, or via reindex |
| `conversation` | User and assistant messages | During conversation (if embedding model configured) |

## Configuration

Set in `.env` or environment:

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `text-embedding-004` | Model name for the embedding API |
| `EMBEDDING_URL` | Falls back to `LLM_URL` | Embedding API endpoint |
| `EMBEDDING_API_KEY` | Falls back to `LLM_API_KEY` | API key for embeddings |
| `MEMORY_SEARCH_STRATEGY` | `substring` | `substring` or `semantic` for memory search |

When `MEMORY_SEARCH_STRATEGY=semantic`, `memory_search` uses embeddings. Otherwise it falls back to case-insensitive substring matching. Conversation search always uses embeddings when an embedding model is configured.

## Tools

- **`memory_search`** — searches memories using configured strategy (substring or semantic)
- **`conversation_search`** — searches past conversation archives using semantic search

## CLI tools

```bash
make reindex              # Rebuild all embeddings from memory files + conversation archives
decafclaw-search "query"  # Search the embedding index from the command line
```

Options for `decafclaw-search`:
- `--type memory|conversation|all` — filter by source type (default: all)
- `--top-k N` — number of results (default: 5)

## Reindexing

The index is rebuilt automatically if empty when a search is performed. To force a full rebuild:

```bash
make reindex
```

This deletes the existing database and re-embeds all memory files and conversation archives. Useful after changing the embedding model or if the index gets corrupted.
