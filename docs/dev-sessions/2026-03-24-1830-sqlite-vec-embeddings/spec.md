# Spec: Replace O(N) cosine scan with sqlite-vec

**Issue:** [#123](https://github.com/lmorchard/decafclaw/issues/123)
**Branch:** `sqlite-vec-embeddings`

## Problem

`search_similar_sync()` loads all embedding rows into Python and computes cosine similarity with NumPy. This is O(N) in both memory and compute, and sits on the hot path for every interactive turn (proactive memory context retrieval via `memory_context.py`).

Currently manageable with a small index, but latency will grow linearly with embeddings count.

## Goal

Replace the Python-side full scan with [sqlite-vec](https://github.com/asg017/sqlite-vec), moving vector distance computation into a native C SQLite extension.

## Constraints

- **No behavior change** — search results should be equivalent (same ordering, same filtering by source_type)
- **Backward compatible** — existing `embeddings.db` files must be migrated automatically on first use
- **Wiki boost preserved** — the 1.2x wiki boost must still work (post-query reranking since sqlite-vec doesn't support per-row boosting)
- **numpy can be removed** — once sqlite-vec handles similarity, numpy is only used for serialization, which `sqlite_vec.serialize_float32()` replaces
- **Tests must pass** — existing test_embeddings.py tests must continue to pass with minimal changes

## Design

### New dependency

Add `sqlite-vec` to `pyproject.toml` dependencies. Remove `numpy` (replaced by `sqlite_vec.serialize_float32()`).

### Schema changes

Create a `vec0` virtual table alongside the existing `memory_embeddings` table:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
    embedding FLOAT[768],
    distance_metric = 'cosine'
);
```

The existing `memory_embeddings` table stays — it holds metadata (file_path, entry_text, source_type, created_at, entry_hash). The vec0 table holds only the vector, keyed by rowid matching `memory_embeddings.id`.

**No duplicate embedding storage.** New inserts write the vector only to `embeddings_vec`, not to the `memory_embeddings.embedding` BLOB column. The old `embedding` column is left in place for existing rows (needed for migration) but is not written to going forward. On full reindex (`reindex_cli` deletes and recreates the DB), the rebuilt schema omits the `embedding` column entirely.

### Migration strategy

On `_get_db()`:
1. Load the sqlite-vec extension
2. Create the vec0 virtual table if it doesn't exist
3. Check if vec0 is empty but `memory_embeddings` has rows with a non-null `embedding` BLOB → bulk-copy into `embeddings_vec` matching by rowid
4. This is a one-time migration that runs automatically

### Search changes (`search_similar_sync`)

Replace the Python loop with a SQL join:

```sql
SELECT m.entry_text, m.file_path, m.source_type, v.distance
FROM embeddings_vec v
JOIN memory_embeddings m ON m.id = v.rowid
WHERE v.embedding MATCH ?
ORDER BY v.distance
LIMIT ?
```

With source_type filtering:

```sql
SELECT m.entry_text, m.file_path, m.source_type, v.distance
FROM embeddings_vec v
JOIN memory_embeddings m ON m.id = v.rowid
WHERE v.embedding MATCH ?
  AND m.source_type = ?
ORDER BY v.distance
LIMIT ?
```

Post-query: convert distance to similarity (`1 - distance`), apply wiki boost, re-sort, trim to top_k.

**Note on over-fetching:** When wiki boost is active (searching all types), we may need to fetch more than `top_k` from the vec0 query to account for wiki entries that would be boosted above non-wiki entries. Fetch `top_k * 2` and trim after boost.

### Insert changes (`index_entry_sync`)

Insert metadata into `memory_embeddings` (without the embedding BLOB), then the vector into `embeddings_vec`:

```python
cursor = conn.execute(
    "INSERT OR IGNORE INTO memory_embeddings (file_path, entry_hash, entry_text, source_type, created_at) VALUES ...",
    ...
)
if cursor.lastrowid:
    conn.execute(
        "INSERT INTO embeddings_vec(rowid, embedding) VALUES (?, ?)",
        (cursor.lastrowid, serialize_float32(embedding))
    )
```

The `embedding` BLOB column is no longer written to. Existing rows that have it are fine — it's only read during the one-time migration to vec0.

### Delete changes

When deleting from `memory_embeddings`, also delete matching rows from `embeddings_vec`:

```python
# Get IDs before deleting
ids = [row[0] for row in conn.execute(
    "SELECT id FROM memory_embeddings WHERE file_path = ?", (file_path,)
)]
conn.execute("DELETE FROM memory_embeddings WHERE file_path = ?", (file_path,))
for row_id in ids:
    conn.execute("DELETE FROM embeddings_vec WHERE rowid = ?", (row_id,))
```

### Serialization changes

Replace `_serialize_embedding` / `_deserialize_embedding` (struct.pack/unpack) with `sqlite_vec.serialize_float32()`. The existing blob format in `memory_embeddings.embedding` is the same (packed float32), but we use sqlite-vec's serializer for vec0 inserts. `_deserialize_embedding` is only needed during migration (reading old BLOBs); it can be removed after that code path is no longer needed.

### Reindex changes

`reindex_cli` already deletes and rebuilds `embeddings.db`. After the change:
- The rebuilt `memory_embeddings` table **omits the `embedding` BLOB column** — it only has metadata columns (file_path, entry_hash, entry_text, source_type, created_at)
- Vectors go exclusively into `embeddings_vec`
- This gives a clean schema without vestigial columns

## Out of scope

- Approximate nearest neighbor (ANN) indexes — sqlite-vec supports this but our dataset is small enough that brute-force vec0 is sufficient
- Changing the embedding dimension or model
- Temporal decay (#121) — separate concern, would benefit from this change
- Changing the API surface of `search_similar_sync` / `search_similar`

## Risks

- **macOS SQLite extension loading** — macOS system Python may not support `enable_load_extension`. Homebrew Python (which Les uses) should be fine. If not, we'll need to document the requirement.
- **sqlite-vec is still pre-1.0** — API could change, but the core vec0 interface is stable per the maintainer's blog posts.
- **Migration correctness** — the one-time migration must correctly map rowids. If `memory_embeddings.id` has gaps (from deletions), the vec0 rowids must match exactly.

## Success criteria

1. `make test` passes
2. `make check` passes (lint + typecheck)
3. `decafclaw-search "some query"` returns equivalent results
4. `decafclaw-reindex` rebuilds cleanly
5. Proactive memory context retrieval works in Mattermost (manual test after merge)
