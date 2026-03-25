# Notes: sqlite-vec embeddings

## Session log

### 2026-03-24 — Session created

- Created branch `sqlite-vec-embeddings` from main
- Wrote initial spec based on issue #123 and current embeddings.py code
- Researched sqlite-vec alternatives — sqlite-vec is the clear winner (MIT/Apache-2.0, minimal migration, same SQLite file)

### 2026-03-25 — Implementation

**Key findings during implementation:**

1. **`distance_metric=cosine` syntax** — it's a column-level option, not a table option. Must be `float[768] distance_metric=cosine` (no quotes, no spaces around `=`, same argument as the dimension). Took a few tries to discover this — the docs examples use a different quoting style than what actually works in v0.1.7.

2. **vec0 MATCH must be a subquery** — direct JOINs with MATCH may not push the constraint into the virtual table. Used subquery pattern:
   ```sql
   SELECT ... FROM (
       SELECT rowid, distance FROM embeddings_vec WHERE embedding MATCH ? ORDER BY distance LIMIT ?
   ) v JOIN memory_embeddings m ON m.id = v.rowid
   ```

3. **Over-fetch `top_k * 3`** — needed because source_type filtering and wiki boost happen post-query. vec0 doesn't know about metadata.

4. **Schema branching** — `_has_embedding_column(conn)` via PRAGMA table_info cleanly handles both legacy (has embedding BLOB NOT NULL) and fresh (no embedding column) DBs.

5. **numpy removed** — `sqlite_vec.serialize_float32()` replaces both `struct.pack` serialization and numpy for cosine computation.

**Files changed:**
- `src/decafclaw/embeddings.py` — core rewrite
- `tests/test_embeddings.py` — 4 new tests, 1 removed
- `pyproject.toml` — added sqlite-vec, removed numpy

**Test results:** 700 tests passing, lint + typecheck clean.

**Still needed before merge:**
- `make build-eval-fixtures` — rebuild eval fixture DB (requires embedding API)
- `decafclaw-reindex` — rebuild live DB on deployment machine
- Les review
