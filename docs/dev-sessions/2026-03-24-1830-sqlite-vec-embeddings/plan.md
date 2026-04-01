# Plan: Replace O(N) cosine scan with sqlite-vec

## Overview

Four steps, each building on the last. Each step ends with lint + test passing.

**Key design decisions from review:**
- vec0 MATCH queries must be subqueries — JOINing directly with MATCH may not push the constraint into the virtual table
- source_type filtering happens post-query (vec0 has no knowledge of metadata)
- Over-fetch from vec0, then filter/boost/trim in Python
- Schema detection via PRAGMA table_info to handle old vs fresh DBs in one code path

---

## Step 1: Add sqlite-vec dependency, load extension, create vec0 table

**Goal:** Get sqlite-vec loaded and the vec0 virtual table created alongside the existing schema. Nothing else changes — all existing behavior is preserved.

**Changes:**
- `pyproject.toml`: Add `sqlite-vec` to dependencies
- `embeddings.py`: Import `sqlite_vec`, load extension in `_get_db()`, create vec0 virtual table
- `tests/test_embeddings.py`: Verify the vec0 table exists after `_get_db()`

**Prompt:**
> In `pyproject.toml`, add `"sqlite-vec>=0.1.6"` to dependencies.
>
> In `src/decafclaw/embeddings.py`:
> 1. Add `import sqlite_vec` at the top
> 2. In `_get_db()`, immediately after `conn = sqlite3.connect(...)`, add:
>    ```python
>    conn.enable_load_extension(True)
>    sqlite_vec.load(conn)
>    conn.enable_load_extension(False)
>    ```
> 3. After the existing `CREATE TABLE IF NOT EXISTS` statements and the source_type migration, add:
>    ```python
>    conn.execute("""
>        CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
>            embedding float[768],
>            distance_metric = 'cosine'
>        )
>    """)
>    ```
>
> Do NOT change any other functions. All existing behavior preserved.
>
> In `tests/test_embeddings.py`, add:
> ```python
> def test_vec0_table_exists(config):
>     conn = _get_db(config)
>     tables = [r[0] for r in conn.execute(
>         "SELECT name FROM sqlite_master WHERE type='table'"
>     ).fetchall()]
>     conn.close()
>     assert "embeddings_vec" in tables
> ```
>
> Run `make check && make test`.

---

## Step 2: Dual-write inserts to vec0

**Goal:** `index_entry_sync` writes the vector to `embeddings_vec` in addition to the existing `memory_embeddings` insert. Search still uses the old Python path.

**Changes:**
- `embeddings.py`: In `index_entry_sync`, after the existing INSERT, insert into `embeddings_vec` using `sqlite_vec.serialize_float32()`
- `tests/test_embeddings.py`: Verify vec0 gets populated on insert

**Prompt:**
> In `src/decafclaw/embeddings.py`, modify `index_entry_sync()`:
>
> After the existing `INSERT OR IGNORE INTO memory_embeddings` execution, add:
> ```python
> if cursor.lastrowid:  # non-zero means a new row was inserted (not a dedup ignore)
>     conn.execute(
>         "INSERT INTO embeddings_vec(rowid, embedding) VALUES (?, ?)",
>         (cursor.lastrowid, sqlite_vec.serialize_float32(embedding)),
>     )
> ```
>
> Note: need to capture the cursor from the existing execute call (change `conn.execute(...)` to `cursor = conn.execute(...)`).
>
> In `tests/test_embeddings.py`, add:
> ```python
> def test_vec0_populated_on_insert(config):
>     """Inserting an entry also populates the vec0 table."""
>     vec = [1.0] * 768
>     index_entry_sync(config, "test.md", "test entry", vec)
>     import sqlite3
>     conn = sqlite3.connect(str(config.workspace_path / "embeddings.db"))
>     # vec0 tables don't appear in sqlite_master queries the same way,
>     # but we can query them directly
>     rows = conn.execute("SELECT rowid FROM embeddings_vec").fetchall()
>     conn.close()
>     assert len(rows) == 1
> ```
>
> Run `make check && make test`.

---

## Step 3: Switch search to sqlite-vec, remove numpy

**Goal:** `search_similar_sync` uses the vec0 table for similarity search. The Python cosine loop and numpy dependency are removed.

**Critical design note:** vec0 MATCH must be a subquery — do NOT JOIN directly with MATCH. The pattern is:
```sql
SELECT v.rowid, v.distance
FROM embeddings_vec v
WHERE v.embedding MATCH ?
ORDER BY v.distance
LIMIT ?
```
Then JOIN the results with `memory_embeddings` for metadata. Source_type filtering and wiki boost happen post-query in Python.

**Changes:**
- `embeddings.py`: Rewrite `search_similar_sync`:
  1. Serialize query with `sqlite_vec.serialize_float32()`
  2. Subquery vec0 for nearest `top_k * 3` rowids+distances (over-fetch for source_type filtering + wiki boost headroom)
  3. JOIN with `memory_embeddings` to get metadata
  4. Filter by source_type if specified
  5. Convert distance to similarity (`1 - distance`), apply wiki boost, sort, trim to `top_k`
- `embeddings.py`: Remove `import numpy as np`, `_deserialize_embedding`, `_serialize_embedding`, `import struct`
- `pyproject.toml`: Remove `numpy` from dependencies
- `tests/test_embeddings.py`: Remove serialize/deserialize imports and test

**Prompt:**
> In `src/decafclaw/embeddings.py`, rewrite `search_similar_sync`:
>
> ```python
> def search_similar_sync(config, query_embedding: list[float], top_k: int = 5,
>                         source_type: str | None = None) -> list[dict]:
>     """Find the top K most similar entries by cosine similarity (sync)."""
>     with _open_db(config) as conn:
>         _check_model(config, conn)
>
>         query_vec = sqlite_vec.serialize_float32(query_embedding)
>
>         # Over-fetch from vec0 to allow for source_type filtering and wiki boost reranking
>         fetch_k = top_k * 3
>
>         rows = conn.execute("""
>             SELECT m.entry_text, m.file_path, m.source_type, v.distance
>             FROM (
>                 SELECT rowid, distance
>                 FROM embeddings_vec
>                 WHERE embedding MATCH ?
>                 ORDER BY distance
>                 LIMIT ?
>             ) v
>             JOIN memory_embeddings m ON m.id = v.rowid
>         """, (query_vec, fetch_k)).fetchall()
>
>     if not rows:
>         return []
>
>     WIKI_BOOST = 1.2
>
>     results = []
>     for entry_text, file_path, row_source_type, distance in rows:
>         if source_type and row_source_type != source_type:
>             continue
>         similarity = 1.0 - distance
>         if row_source_type == "wiki":
>             similarity *= WIKI_BOOST
>         results.append({
>             "entry_text": entry_text,
>             "file_path": file_path,
>             "similarity": similarity,
>             "source_type": row_source_type,
>         })
>
>     results.sort(key=lambda x: x["similarity"], reverse=True)
>     return results[:top_k]
> ```
>
> Also:
> - Remove `import numpy as np`
> - Remove `_deserialize_embedding` function
> - Remove `_serialize_embedding` function
> - Remove `import struct`
> - In `pyproject.toml`, remove `numpy>=2.4.3` from dependencies
>
> In `tests/test_embeddings.py`:
> - Remove imports of `_serialize_embedding` and `_deserialize_embedding`
> - Remove `import struct`
> - Remove `test_serialize_roundtrip`
> - Verify remaining tests pass: `test_index_and_search`, `test_source_type_filtering`, `test_dedup_by_hash`, `test_empty_search`, `test_model_sentinel`
>
> Run `make check && make test`.

---

## Step 4: Migration, clean schema, delete sync, final cleanup

**Goal:** Auto-migrate existing DBs. Fresh DBs get clean schema (no `embedding` column). Deletes keep both tables in sync. All dead code removed.

**Schema handling strategy:** Use a single helper `_has_embedding_column(conn)` that checks PRAGMA table_info. This determines:
- Whether to run migration (old DB with BLOBs → copy to vec0)
- Whether INSERT needs the `embedding` column (old DB) or not (fresh DB)

**Changes:**
- `embeddings.py`:
  - Change CREATE TABLE to omit `embedding` column (fresh DBs get clean schema; old DBs skip via IF NOT EXISTS)
  - Add `_has_embedding_column()` helper
  - Add migration logic in `_get_db()`: if vec0 empty + old DB has embedding BLOBs → bulk copy
  - `index_entry_sync`: use `_has_embedding_column` to decide INSERT shape — pass `b''` on old DBs, omit column on fresh DBs
  - `delete_entries` and `delete_by_source_type`: collect IDs first, delete from both tables
  - Remove the `source_type` ALTER TABLE migration (it's for very old DBs — keep it, it's harmless)
- `tests/test_embeddings.py`:
  - `test_migration_from_legacy_db` — create old-schema DB manually, verify auto-migration
  - `test_delete_cleans_vec0` — verify delete removes from both tables
  - `test_delete_by_source_type_cleans_vec0`

**Prompt:**
> In `src/decafclaw/embeddings.py`:
>
> 1. Add a helper:
>    ```python
>    def _has_embedding_column(conn) -> bool:
>        """Check if memory_embeddings has the legacy embedding BLOB column."""
>        cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()]
>        return "embedding" in cols
>    ```
>
> 2. Change the CREATE TABLE in `_get_db()` to omit the `embedding` column:
>    ```sql
>    CREATE TABLE IF NOT EXISTS memory_embeddings (
>        id INTEGER PRIMARY KEY AUTOINCREMENT,
>        file_path TEXT NOT NULL,
>        entry_hash TEXT NOT NULL UNIQUE,
>        entry_text TEXT NOT NULL,
>        source_type TEXT NOT NULL DEFAULT 'memory',
>        created_at TEXT NOT NULL
>    )
>    ```
>    Old DBs already have the table → IF NOT EXISTS skips this → their embedding column is preserved.
>
> 3. After vec0 creation, add migration:
>    ```python
>    vec_count = conn.execute("SELECT COUNT(*) FROM embeddings_vec").fetchone()[0]
>    if vec_count == 0 and _has_embedding_column(conn):
>        legacy_count = conn.execute(
>            "SELECT COUNT(*) FROM memory_embeddings WHERE length(embedding) > 0"
>        ).fetchone()[0]
>        if legacy_count > 0:
>            log.info(f"Migrating {legacy_count} embeddings to vec0 table...")
>            conn.execute("""
>                INSERT INTO embeddings_vec(rowid, embedding)
>                SELECT id, embedding FROM memory_embeddings
>                WHERE length(embedding) > 0
>            """)
>            conn.commit()
>            log.info("Vec0 migration complete")
>    ```
>
> 4. In `index_entry_sync`, branch on schema:
>    ```python
>    if _has_embedding_column(conn):
>        cursor = conn.execute(
>            """INSERT OR IGNORE INTO memory_embeddings
>               (file_path, entry_hash, entry_text, embedding, source_type, created_at)
>               VALUES (?, ?, ?, ?, ?, ?)""",
>            (file_path, _entry_hash(entry_text), entry_text, b'', source_type,
>             datetime.now().isoformat()),
>        )
>    else:
>        cursor = conn.execute(
>            """INSERT OR IGNORE INTO memory_embeddings
>               (file_path, entry_hash, entry_text, source_type, created_at)
>               VALUES (?, ?, ?, ?, ?)""",
>            (file_path, _entry_hash(entry_text), entry_text, source_type,
>             datetime.now().isoformat()),
>        )
>    ```
>
> 5. Update `delete_entries`:
>    ```python
>    def delete_entries(config, file_path: str, source_type: str | None = None) -> int:
>        with _open_db(config) as conn:
>            if source_type:
>                ids = [r[0] for r in conn.execute(
>                    "SELECT id FROM memory_embeddings WHERE file_path = ? AND source_type = ?",
>                    (file_path, source_type),
>                ).fetchall()]
>                conn.execute(
>                    "DELETE FROM memory_embeddings WHERE file_path = ? AND source_type = ?",
>                    (file_path, source_type),
>                )
>            else:
>                ids = [r[0] for r in conn.execute(
>                    "SELECT id FROM memory_embeddings WHERE file_path = ?",
>                    (file_path,),
>                ).fetchall()]
>                conn.execute(
>                    "DELETE FROM memory_embeddings WHERE file_path = ?",
>                    (file_path,),
>                )
>            for row_id in ids:
>                conn.execute("DELETE FROM embeddings_vec WHERE rowid = ?", (row_id,))
>            conn.commit()
>            return len(ids)
>    ```
>
> 6. Update `delete_by_source_type` similarly — collect IDs, delete from both tables.
>
> 7. In `tests/test_embeddings.py`, add:
>    - `test_migration_from_legacy_db`: manually create old-schema DB with struct.pack embedding, call `_get_db()`, verify vec0 populated, verify search works
>    - `test_delete_cleans_vec0`: index entry, delete it, verify vec0 empty
>    - `test_delete_by_source_type_cleans_vec0`: index two types, delete one, verify vec0 has correct count
>    - `test_fresh_db_no_embedding_column`: create fresh DB via `_get_db()`, verify PRAGMA table_info has no `embedding` column
>
> Run `make check && make test`.

---

## Post-implementation

1. `make build-eval-fixtures` — rebuild eval fixture DB with new schema (requires embedding API access)
2. `decafclaw-reindex` — rebuild live DB (Les runs this on the deployment machine)
3. `decafclaw-search "test query"` — verify results look right
4. Update `CLAUDE.md` key files if needed (embeddings.py description)
5. Check `docs/` for any embeddings documentation to update
6. Commit per step (or squash), PR
