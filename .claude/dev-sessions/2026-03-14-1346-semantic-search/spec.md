# Semantic Search for Memory

## Overview

Add vector embedding search to the memory system alongside the existing
substring search. Memory entries are embedded via LiteLLM's embedding
endpoint and stored in a SQLite index. Search computes cosine similarity
to find semantically related entries even when wording doesn't match.

This directly addresses the eval findings: "What do I do for a living?"
failing to find "software engineer" because substring search can't bridge
the semantic gap.

## Goals

- Find memories by meaning, not just keyword match
- "What drinks do I like?" finds memories about cocktails
- "What do I do for a living?" finds memories about work/job
- Coexist with substring search — semantic is an enhancement, not a replacement
- Learn RAG fundamentals: embeddings, cosine similarity, indexing

## Architecture

```
Markdown files (source of truth)
        │
        ├── read/write by memory.py (existing)
        │
        └── indexed by embeddings.py (new)
                │
                └── SQLite DB: data/workspace/{agent_id}/embeddings.db
                        - memory_embeddings table
                        - entry text + embedding vector (BLOB)
                        - file path + entry hash for dedup
```

The markdown files remain the source of truth. The SQLite DB is a
derived index that can be rebuilt from the files at any time.

## Embedding Model

- **Model:** `text-embedding-004` via LiteLLM proxy
- **Dimensions:** 768
- **Endpoint:** Same `LLM_URL` with `/v1/embeddings` path
- **Config:** `EMBEDDING_MODEL=text-embedding-004` (new env var)

## Components

### embeddings.py (new module)

Core operations:
- `init_db(config)` — create SQLite DB and table if not exists
- `embed_text(config, text)` — call embedding API, return vector
- `index_entry(config, file_path, entry_text)` — embed and store
- `search_similar(config, query, top_k=5)` — embed query, cosine
  similarity against all stored vectors, return top K entries
- `reindex_all(config)` — rebuild entire index from markdown files

### SQLite schema

```sql
CREATE TABLE IF NOT EXISTS memory_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    entry_hash TEXT NOT NULL UNIQUE,  -- SHA256 of entry text for dedup
    entry_text TEXT NOT NULL,
    embedding BLOB NOT NULL,          -- serialized float32 array
    created_at TEXT NOT NULL
);
```

### Cosine similarity in Python

```python
import numpy as np

def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
```

Load all embeddings, compute similarity with query vector, sort, return
top K. Fine for hundreds of entries. sqlite-vec migration for scale later.

### Integration with memory tools

- `memory_save` — after writing markdown, also index the new entry
- `memory_search` — configurable strategy via `MEMORY_SEARCH_STRATEGY`:
  - `substring` — existing behavior (default until semantic is proven)
  - `semantic` — embedding cosine similarity only
  - Default switches to `semantic` once eval results confirm it's better
- `memory_recent` — unchanged (chronological, not semantic)
- Future: `hybrid` strategy that merges both (deferred until we have
  eval data showing where each strategy wins/loses)

### Config additions

```
EMBEDDING_MODEL=text-embedding-004   # embedding model name
EMBEDDING_URL=                       # default: LLM_URL (same proxy)
EMBEDDING_API_KEY=                   # default: LLM_API_KEY
MEMORY_SEARCH_STRATEGY=substring     # substring | semantic (switch once proven)
```

## How Search Changes

Current flow:
```
memory_search("drinks") → substring grep → no match → fail
```

New flow:
```
memory_search("drinks") → embed "drinks" → cosine similarity →
    finds "cocktails are Boulevardier..." (similarity 0.85) → success
```

The tool description and search checklist can be simplified once
semantic search works — no more "try synonyms, try plural" guidance.

## Future: sqlite-vec migration

The plain SQLite + Python cosine approach works for hundreds of entries.
If it gets slow:

```sql
-- Add sqlite-vec virtual table
CREATE VIRTUAL TABLE vec_index USING vec0(
    id INTEGER PRIMARY KEY,
    embedding float[768]
);
-- Copy existing embeddings
INSERT INTO vec_index SELECT id, embedding FROM memory_embeddings;
-- Queries use vec_index for fast KNN
```

The schema is designed to make this migration additive.

## Scope

### In scope

- embeddings.py with SQLite storage and Python cosine similarity
- Embedding API calls via LiteLLM `/v1/embeddings`
- Index new entries on memory_save
- Semantic search in memory_search (alongside or replacing substring)
- Config: EMBEDDING_MODEL, EMBEDDING_URL, EMBEDDING_API_KEY
- numpy dependency for vector math

### Out of scope

- sqlite-vec (future optimization)
- Reindexing CLI command (future — for now, delete DB to rebuild)
- Embedding existing memories on startup (index on first search miss?)
- Batch embedding (one at a time is fine for now)
- Conversation archive embedding (just memories for now)
