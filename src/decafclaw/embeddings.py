"""Embedding index — SQLite storage and sqlite-vec cosine similarity search."""

import hashlib
import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import httpx
import sqlite_vec

from .llm.types import PROVIDER_FASTEMBED

log = logging.getLogger(__name__)


# Source type boosts for semantic search: curated pages > user pages > journal
_SOURCE_BOOSTS = {
    "page": 1.3,
    "user": 1.2,
    "wiki": 1.2,  # legacy compat
    "journal": 1.0,
    "memory": 1.0,  # legacy compat
}


@lru_cache(maxsize=None)
def _fastembed_model_dimensions(model: str) -> int | None:
    """Vector size declared by fastembed's model metadata, or ``None``.

    Uses fastembed's static model list, so no weights are downloaded. Results
    are cached: the catalogue does not change at runtime.
    """
    try:
        from fastembed import TextEmbedding
    except ImportError:
        return None
    try:
        for entry in TextEmbedding.list_supported_models():
            if entry.get("model") == model:
                dim = entry.get("dim")
                return int(dim) if dim else None
    except Exception as exc:  # pragma: no cover - defensive against library churn
        log.warning("Could not read fastembed model metadata: %s", exc)
    return None


def _effective_dimensions(config) -> int:
    """Vector size the configured embedding model produces.

    fastembed models carry their own ``dim`` and ignore the generic
    ``embedding.dimensions`` setting. Other providers keep using it.
    """
    ec = config.embedding
    if ec.provider == PROVIDER_FASTEMBED:
        dim = _fastembed_model_dimensions(ec.model)
        if dim:
            return dim
        log.warning(
            "fastembed model '%s' has no declared dimension; falling back to "
            "embedding.dimensions=%d. Set EMBEDDING_DIMENSIONS to match the model.",
            ec.model,
            ec.dimensions,
        )
    return ec.dimensions


def _vec_table_dimensions(conn) -> int | None:
    """Dimension of the existing ``embeddings_vec`` table, parsed from its DDL."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'embeddings_vec'").fetchone()
    if not row or not row[0]:
        return None
    match = re.search(r"float\[(\d+)\]", row[0])
    return int(match.group(1)) if match else None


def _stored_dimensions(conn) -> int | None:
    """Dimension recorded in the metadata table, if present."""
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_dimensions'").fetchone()
    try:
        return int(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _dimension_mismatch_message(index_dim, model_dim) -> str:
    return (
        f"Embedding dimensions mismatch: the index holds {index_dim}-dimensional vectors "
        f"but the configured model produces {model_dim}. Run `make reindex` "
        f"(or `decafclaw-reindex` on a bare install) to rebuild the index."
    )


def _index_mismatch(conn, vector_dim: int) -> bool:
    """True when ``vector_dim`` cannot be stored/searched in this index."""
    index_dim = _vec_table_dimensions(conn) or _stored_dimensions(conn)
    if index_dim is not None and index_dim != vector_dim:
        log.warning(_dimension_mismatch_message(index_dim, vector_dim))
        return True
    return False


def _index_incompatible(config) -> bool:
    """True when the current model's vectors cannot go into the existing index."""
    with _open_db(config) as conn:
        return _index_mismatch(conn, _effective_dimensions(config))


def _db_path(config) -> Path:
    """Path to the embeddings SQLite database."""
    return config.workspace_path / "embeddings.db"


def _has_embedding_column(conn) -> bool:
    """Check if memory_embeddings has the legacy embedding BLOB column."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()]
    return "embedding" in cols


def _init_schema(conn, config):
    """Create tables and indexes for the embeddings database."""
    # Fresh DBs get clean schema (no embedding BLOB column).
    # Old DBs already have the table — IF NOT EXISTS skips this,
    # preserving their embedding column for migration.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            entry_hash TEXT NOT NULL UNIQUE,
            entry_text TEXT NOT NULL,
            source_type TEXT NOT NULL DEFAULT 'memory',
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    # Migration: add source_type column to very old DBs
    try:
        conn.execute("ALTER TABLE memory_embeddings ADD COLUMN source_type TEXT NOT NULL DEFAULT 'memory'")
    except sqlite3.OperationalError as exc:
        # Already-applied migration on every existing DB; only logged for
        # parity with other except-paths so a genuine schema problem is
        # observable instead of swallowed.
        log.debug("embeddings: source_type migration skipped (already applied?): %s", exc)
    dim = _effective_dimensions(config)
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
            embedding float[{dim}] distance_metric=cosine
        )
    """)


def _migrate_legacy(conn):
    """Migrate legacy embedding BLOBs into vec0 table.

    Uses NOT EXISTS so it's idempotent — safe to retry after partial migration.
    """
    if not _has_embedding_column(conn):
        return
    legacy_count = conn.execute("""
        SELECT COUNT(*) FROM memory_embeddings m
        WHERE length(m.embedding) > 0
          AND NOT EXISTS (SELECT 1 FROM embeddings_vec v WHERE v.rowid = m.id)
    """).fetchone()[0]
    if legacy_count > 0:
        log.info(f"Migrating {legacy_count} embeddings to vec0 table...")
        conn.execute("""
            INSERT INTO embeddings_vec(rowid, embedding)
            SELECT m.id, m.embedding FROM memory_embeddings m
            WHERE length(m.embedding) > 0
              AND NOT EXISTS (SELECT 1 FROM embeddings_vec v WHERE v.rowid = m.id)
        """)
        log.info("Vec0 migration complete")


def _get_db(config) -> sqlite3.Connection:
    """Get or create the embeddings database."""
    path = _db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    _init_schema(conn, config)
    _migrate_legacy(conn)
    conn.commit()
    return conn


@contextmanager
def _open_db(config):
    """Context manager for embeddings DB — auto-closes on exit."""
    conn = _get_db(config)
    try:
        yield conn
    finally:
        conn.close()


def _entry_hash(text: str) -> str:
    """SHA256 hash of entry text for deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _resolve_embedding_provider(config):
    """Return ``(provider_name, provider_or_None)`` for the embedding config.

    An empty ``embedding.provider`` falls back to the effective default
    model's provider, matching the pre-fastembed behavior.

    The built-in fastembed provider needs no config entry, so it is registered
    lazily here when app startup (``init_providers``) has not run. The reindex
    and search CLIs never start the app, and they must still embed locally.
    """
    from .llm import get_provider, register_provider
    from .model_catalog import effective_default_model

    provider_name = config.embedding.provider
    if not provider_name:
        default_name = effective_default_model(config)
        if default_name and default_name in config.model_configs:
            provider_name = config.model_configs[default_name].provider
    if not provider_name:
        return "", None
    try:
        return provider_name, get_provider(provider_name)
    except KeyError:
        if provider_name != PROVIDER_FASTEMBED:
            return provider_name, None

    from .llm.providers.fastembed import FastembedProvider

    register_provider(PROVIDER_FASTEMBED, FastembedProvider())
    try:
        return provider_name, get_provider(provider_name)
    except KeyError:  # pragma: no cover - registration cannot fail
        return provider_name, None


async def embed_text(config, text: str, max_retries: int = 3) -> list[float] | None:
    """Call the embedding API and return the vector.

    Routes through the provider system if embedding.provider is set,
    otherwise uses the legacy httpx path via resolved().
    """
    ec = config.embedding

    provider_name, provider = _resolve_embedding_provider(config)
    if provider is not None:
        return await provider.embed(ec.model, text)

    if provider_name == PROVIDER_FASTEMBED:
        # Local-only configuration: silently rerouting to the remote endpoint
        # would restore the cloud egress this provider exists to remove.
        log.error(
            "Embedding provider 'fastembed' is unavailable for model '%s'. Check that the "
            "fastembed package is installed and the model can load. Refusing to fall back "
            "to the remote embedding endpoint; returning no embedding.",
            ec.model,
        )
        return None
    if provider_name:
        log.warning("Embedding provider '%s' not in registry, falling back to legacy", provider_name)

    # Legacy path: direct httpx to resolved URL
    import asyncio as _asyncio

    resolved = ec.resolved(config)

    body = {
        "model": resolved.model,
        "input": text,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {resolved.api_key}",
    }

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(resolved.url, json=body, headers=headers, timeout=30)
            if resp.status_code == 429 and attempt < max_retries:
                delay = 2**attempt  # 1s, 2s, 4s
                log.warning(f"Embedding API rate limited, retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
                await _asyncio.sleep(delay)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
        except httpx.HTTPStatusError as e:
            log.error(f"Embedding API call failed: {e}")
            return None
        except Exception as e:
            log.error(f"Embedding API call failed: {e}")
            return None
    return None


async def embed_texts(config, texts: list[str], concurrency: int = 4) -> list[list[float] | None]:
    """Embed several texts, preferring a provider batch method.

    fastembed embeds a whole chunk in one ONNX pass. Other providers keep the
    per-text path, gathered with the same concurrency as reindex. A failed
    batch yields ``None`` entries so callers skip those rows instead of
    aborting the whole run.
    """
    texts = list(texts)
    if not texts:
        return []

    _, provider = _resolve_embedding_provider(config)
    embed_batch = getattr(provider, "embed_batch", None) if provider is not None else None
    if embed_batch is not None:
        vectors = await embed_batch(config.embedding.model, texts)
        if vectors is None or len(vectors) != len(texts):
            log.warning("Batch embedding returned no usable vectors for %d text(s); skipping them", len(texts))
            return [None] * len(texts)
        return vectors

    import asyncio as _asyncio

    sem = _asyncio.Semaphore(concurrency)

    async def _one(text: str):
        async with sem:
            return await embed_text(config, text)

    return await _asyncio.gather(*(_one(text) for text in texts))


def _check_model(config, conn):
    """Warn when the DB was built with a different embedding model.

    Dimension mismatches are reported once by ``_index_mismatch`` at the point
    of use, so they are not duplicated here.
    """
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_model'").fetchone()
    if row and row[0] != config.embedding.model:
        log.warning(
            f"Embedding model mismatch: DB was built with '{row[0]}', "
            f"config uses '{config.embedding.model}'. "
            f"Run 'decafclaw-reindex' to rebuild."
        )


def _set_model(config, conn):
    """Record which embedding model and dimensions were used."""
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedding_model', ?)",
        (config.embedding.model,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedding_dimensions', ?)",
        (str(_effective_dimensions(config)),),
    )


def index_entry_sync(
    config, file_path: str, entry_text: str, embedding: list[float], source_type: str = "memory"
) -> bool:
    """Store an entry and its embedding in the index (sync).

    Returns ``False`` without inserting when the vector cannot live in this
    index (dimension changed without a rebuild), so a partial reindex cannot
    mix incompatible rows.
    """
    with _open_db(config) as conn:
        if _index_mismatch(conn, len(embedding)):
            return False
        _set_model(config, conn)
        entry_hash = _entry_hash(entry_text)
        now = datetime.now().isoformat()
        if _has_embedding_column(conn):
            # Legacy schema: pass empty blob for NOT NULL embedding column
            cursor = conn.execute(
                """INSERT OR IGNORE INTO memory_embeddings
                   (file_path, entry_hash, entry_text, embedding, source_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (file_path, entry_hash, entry_text, b"", source_type, now),
            )
        else:
            # Clean schema: no embedding column
            cursor = conn.execute(
                """INSERT OR IGNORE INTO memory_embeddings
                   (file_path, entry_hash, entry_text, source_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (file_path, entry_hash, entry_text, source_type, now),
            )
        if cursor.rowcount > 0 and cursor.lastrowid:
            conn.execute(
                "INSERT INTO embeddings_vec(rowid, embedding) VALUES (?, ?)",
                (cursor.lastrowid, sqlite_vec.serialize_float32(embedding)),
            )
        conn.commit()
        return True


def delete_entries(config, file_path: str, source_type: str | None = None) -> int:
    """Delete embeddings entries by file_path (and optionally source_type).

    Returns the number of rows deleted.
    """
    with _open_db(config) as conn:
        if source_type:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM memory_embeddings WHERE file_path = ? AND source_type = ?",
                    (file_path, source_type),
                ).fetchall()
            ]
            conn.execute(
                "DELETE FROM memory_embeddings WHERE file_path = ? AND source_type = ?",
                (file_path, source_type),
            )
        else:
            ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM memory_embeddings WHERE file_path = ?",
                    (file_path,),
                ).fetchall()
            ]
            conn.execute(
                "DELETE FROM memory_embeddings WHERE file_path = ?",
                (file_path,),
            )
        conn.executemany("DELETE FROM embeddings_vec WHERE rowid = ?", [(i,) for i in ids])
        conn.commit()
        return len(ids)


def delete_by_source_type(config, source_type: str) -> int:
    """Delete all embeddings entries for a given source type.

    Returns the number of rows deleted.
    """
    with _open_db(config) as conn:
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM memory_embeddings WHERE source_type = ?",
                (source_type,),
            ).fetchall()
        ]
        conn.execute(
            "DELETE FROM memory_embeddings WHERE source_type = ?",
            (source_type,),
        )
        conn.executemany("DELETE FROM embeddings_vec WHERE rowid = ?", [(i,) for i in ids])
        conn.commit()
        return len(ids)


def search_similar_sync(
    config, query_embedding: list[float], top_k: int = 5, source_type: str | None = None
) -> list[dict]:
    """Find the top K most similar entries by cosine similarity (sync).

    If source_type is specified, only search that type. Otherwise search all.
    Uses sqlite-vec's vec0 virtual table for SIMD-accelerated cosine distance.
    """
    with _open_db(config) as conn:
        _check_model(config, conn)
        if _index_mismatch(conn, len(query_embedding)):
            # vec0 would raise on the MATCH; return no results instead of
            # fabricating an empty-but-successful search or crashing.
            return []

        query_vec = sqlite_vec.serialize_float32(query_embedding)

        # Over-fetch to allow for threshold filtering and wiki boost reranking
        fetch_k = top_k * 3

        rows = conn.execute(
            """
            SELECT m.entry_text, m.file_path, m.source_type, m.created_at, v.distance
            FROM (
                SELECT rowid, distance
                FROM embeddings_vec
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
            ) v
            JOIN memory_embeddings m ON m.id = v.rowid
        """,
            (query_vec, fetch_k),
        ).fetchall()

    if not rows:
        return []

    results = []
    for entry_text, file_path, row_source_type, created_at, distance in rows:
        if source_type and row_source_type != source_type:
            continue
        similarity = 1.0 - distance
        similarity *= _SOURCE_BOOSTS.get(row_source_type, 1.0)
        results.append(
            {
                "entry_text": entry_text,
                "file_path": file_path,
                "similarity": similarity,
                "source_type": row_source_type,
                "created_at": created_at,
            }
        )

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


def find_similar_page_pairs(
    config,
    *,
    threshold: float = 0.9,
    limit: int = 20,
    source_type: str = "page",
    path_prefix: str | None = None,
) -> tuple[list[dict], int]:
    """Find high-similarity pairs in the existing index without re-embedding.

    Queries each stored vector against ``embeddings_vec`` (one connection,
    no provider calls) and returns ``(pairs, indexed_pages)``. Pairs are
    de-duplicated as ``(a, b)`` with ``a < b`` and sorted by similarity
    descending. ``indexed_pages`` counts distinct pages under
    ``path_prefix`` that have a stored vector. Missing DB or dimension
    mismatch returns ``([], 0)`` rather than creating/reindexing anything.
    """
    db_path = _db_path(config)
    if not db_path.exists():
        return [], 0

    threshold = max(0.0, min(1.0, threshold))
    limit = max(0, limit)
    per_page = max(10, min(max(limit, 1) * 3, 100))
    best: dict[tuple[str, str], float] = {}
    indexed: set[str] = set()

    with _open_db(config) as conn:
        if _index_mismatch(conn, _effective_dimensions(config)):
            return [], 0
        rows = conn.execute(
            """SELECT m.id, m.file_path, v.embedding
               FROM memory_embeddings m
               JOIN embeddings_vec v ON v.rowid = m.id
               WHERE m.source_type = ?""",
            (source_type,),
        ).fetchall()
        page_paths = {rowid: file_path for rowid, file_path, _blob in rows}
        if path_prefix is not None:
            indexed = {path for path in page_paths.values() if path.startswith(path_prefix)}
        else:
            indexed = set(page_paths.values())

        for rowid, file_path, blob in rows:
            if path_prefix is not None and not file_path.startswith(path_prefix):
                continue
            hits = conn.execute(
                """SELECT rowid, distance
                   FROM embeddings_vec
                   WHERE embedding MATCH ?
                   ORDER BY distance
                   LIMIT ?""",
                (blob, per_page),
            ).fetchall()
            for hit_rowid, distance in hits:
                if hit_rowid == rowid:
                    continue
                other_path = page_paths.get(hit_rowid)
                if other_path is None or other_path == file_path:
                    continue
                if path_prefix is not None and not other_path.startswith(path_prefix):
                    continue
                similarity = 1.0 - float(distance)
                if similarity < threshold:
                    continue
                pair = (file_path, other_path) if file_path < other_path else (other_path, file_path)
                if similarity > best.get(pair, -1.0):
                    best[pair] = similarity

    pairs = [
        {"a": a, "b": b, "similarity": round(similarity, 6)}
        for (a, b), similarity in sorted(best.items(), key=lambda item: (-item[1], item[0]))
    ][:limit]
    return pairs, len(indexed)


async def index_entry(config, file_path: str, entry_text: str, source_type: str = "memory"):
    """Embed and index an entry (async)."""
    embedding = await embed_text(config, entry_text)
    if embedding:
        index_entry_sync(config, file_path, entry_text, embedding, source_type=source_type)
        log.debug(f"Indexed {source_type} entry from {file_path}")


async def search_similar(config, query: str, top_k: int = 5, source_type: str | None = None) -> list[dict]:
    """Embed a query and find similar entries (async).

    If source_type is specified, only search that type.
    If the memory index is empty but memory files exist, reindex first.
    """
    # Check if index needs building — auto-reindex on empty DB
    with _open_db(config) as conn:
        if source_type:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings WHERE source_type = ?",
                (source_type,),
            ).fetchone()[0]
        else:
            count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    if count == 0:
        log.info("Embedding index is empty, reindexing vault...")
        await reindex_vault(config)
        await reindex_journal(config)

    query_embedding = await embed_text(config, query)
    if not query_embedding:
        return []
    return search_similar_sync(config, query_embedding, top_k, source_type=source_type)


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration (e.g. '12s', '2m 15s', '1h 05m')."""
    sec = max(0, int(round(seconds)))
    if sec < 60:
        return f"{sec}s"
    minutes = sec // 60
    rem_sec = sec % 60
    if minutes < 60:
        return f"{minutes}m {rem_sec:02d}s"
    hours = minutes // 60
    rem_min = minutes % 60
    return f"{hours}h {rem_min:02d}m"


def _format_progress(done: int, total: int, elapsed: float, label: str) -> str:
    """Format reindex progress line with throughput and ETA."""
    pct = (done / total) * 100 if total else 100.0
    rate = (done / elapsed) if elapsed > 0.05 else 0.0
    per_min = rate * 60.0
    elapsed_str = _format_duration(elapsed)

    if done >= total:
        return f"  {label}: {done}/{total} ({pct:.1f}%) | {per_min:.1f} items/min | Total: {elapsed_str}"

    remaining = total - done
    eta_str = _format_duration(remaining / rate) if rate > 0 else "--"
    return f"  {label}: {done}/{total} ({pct:.1f}%) | {per_min:.1f} items/min | Elapsed: {elapsed_str} | ETA: {eta_str}"


async def _reindex_entries(entries, label, config, concurrency: int = 4):
    """Reindex a sequence of (source_id, text, source_type, metadata) tuples.

    Providers with a batch method (fastembed) embed 32 entries per ONNX pass;
    all others embed one entry per task with `concurrency` in flight.
    """
    import asyncio

    # Collect all entries upfront to know total count
    all_entries = list(entries)
    total = len(all_entries)
    if total == 0:
        print(f"  {label}: 0 entries")
        return 0

    t0 = time.monotonic()
    _, provider = _resolve_embedding_provider(config)
    if getattr(provider, "embed_batch", None) is not None:
        chunk_size = 32
        for start in range(0, total, chunk_size):
            chunk = all_entries[start : start + chunk_size]
            texts = [text for _src_id, text, _st, _meta in chunk]
            vectors = await embed_texts(config, texts, concurrency=concurrency)
            for (source_id, text, source_type, _meta), vector in zip(chunk, vectors):
                if vector:
                    index_entry_sync(config, source_id, text, vector, source_type=source_type)
            done = min(start + chunk_size, total)
            elapsed = time.monotonic() - t0
            print(_format_progress(done, total, elapsed, label))
        return total

    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def _embed_one(source_id, text, source_type):
        nonlocal done
        async with sem:
            await index_entry(config, source_id, text, source_type)
        done += 1
        if done % 10 == 0 or done == total:
            elapsed = time.monotonic() - t0
            print(_format_progress(done, total, elapsed, label))

    tasks = [asyncio.create_task(_embed_one(src_id, text, st)) for src_id, text, st, _meta in all_entries]
    await asyncio.gather(*tasks)
    return total


def _iter_journal_entries(config):
    """Yield (source_id, text, source_type, metadata) tuples from journal files."""
    base = config.vault_agent_journal_dir
    if not base.exists():
        return

    md_files = sorted(base.rglob("*.md"))
    for filepath in md_files:
        text = filepath.read_text()
        parts = text.split("\n## ")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            entry = "## " + part if not part.startswith("## ") else part
            rel_path = str(filepath.relative_to(config.vault_root))
            yield rel_path, entry, "journal", {}


def _source_type_for_vault_path(config, filepath):
    """Determine source type based on vault file location."""
    resolved = filepath.resolve()
    try:
        if resolved.is_relative_to(config.vault_agent_journal_dir.resolve()):
            return "journal"
        if resolved.is_relative_to(config.vault_agent_dir.resolve()):
            return "page"
    except (ValueError, OSError):
        pass
    return "user"


def _iter_vault_pages(config):
    """Yield (source_id, text, source_type, metadata) tuples from vault pages.

    Indexes all non-journal vault pages (agent pages + user pages).
    Journal entries are indexed separately via _iter_journal_entries.

    If a page has YAML frontmatter, the embedding text is a composite of
    summary + keywords + tags + body for richer semantic search.
    """
    from .frontmatter import build_composite_text, parse_frontmatter

    vault = config.vault_root
    if not vault.is_dir():
        return

    journal_dir = config.vault_agent_journal_dir
    md_files = sorted(vault.rglob("*.md"))
    for filepath in md_files:
        # Skip journal entries — they're indexed via _iter_journal_entries
        try:
            if filepath.resolve().is_relative_to(journal_dir.resolve()):
                continue
        except (ValueError, OSError):
            pass
        raw_text = filepath.read_text()
        if not raw_text.strip():
            continue
        # Build composite embedding text from frontmatter + body
        metadata, body = parse_frontmatter(raw_text)
        embed_text = build_composite_text(metadata, body)
        rel_path = str(filepath.relative_to(vault))
        source_type = _source_type_for_vault_path(config, filepath)
        yield rel_path, embed_text, source_type, {}


def iter_index_entries(config):
    """Yield every vault entry the embedding index covers: pages and journals.

    The suggested-prompt survey builds its local embedding backend from this
    view of the corpus. Reindexing still calls the page/journal iterators
    directly because it clears and reports each source type separately.
    """
    yield from _iter_vault_pages(config)
    yield from _iter_journal_entries(config)


async def reindex_journal(config, concurrency: int = 4):
    """Rebuild journal entry embeddings."""
    base = config.vault_agent_journal_dir
    if not base.exists():
        log.info("No journal directory found, nothing to index")
        return 0

    if _index_incompatible(config):
        log.error("Skipping journal reindex: the existing index has incompatible dimensions (see warning above).")
        return 0

    # Clear existing journal entries
    deleted = delete_by_source_type(config, "journal")
    if deleted:
        log.info(f"Cleared {deleted} existing journal embedding(s)")

    count = await _reindex_entries(_iter_journal_entries(config), "journal", config, concurrency)
    log.info(f"Reindexed {count} journal entries")
    return count


async def reindex_vault(config, concurrency: int = 4):
    """Rebuild vault page embeddings (agent pages + user pages)."""
    vault = config.vault_root
    if not vault.is_dir():
        log.info("No vault directory found, nothing to index")
        return 0

    if _index_incompatible(config):
        log.error("Skipping vault reindex: the existing index has incompatible dimensions (see warning above).")
        return 0

    # Clear existing page and user entries
    for st in ("page", "user", "wiki"):
        deleted = delete_by_source_type(config, st)
        if deleted:
            log.info(f"Cleared {deleted} existing {st} embedding(s)")

    count = await _reindex_entries(_iter_vault_pages(config), "vault pages", config, concurrency)
    log.info(f"Reindexed {count} vault pages")
    return count


def prune_stale_embeddings(config) -> dict[str, int]:
    """Scan the embedding index and drop rows whose source no longer
    exists or whose source type is excluded from retrieval.

    Hard delete — no soft TTL. Embeddings are deterministic from
    source content, so worst case the operator runs ``make reindex``
    to rebuild from scratch.

    Per source_type:
      - ``page`` / ``user`` / ``wiki`` (legacy) / ``journal``: the
        file at ``vault_root / file_path`` must exist; otherwise the
        row is dropped.
      - ``conversation`` / ``memory``: dropped unconditionally
        (excluded from retrieval per #133 and #305).
      - anything else: kept, with a single warning per unknown type
        — better to leave unknown rows alone than nuke them.

    Returns a counts dict::

        {
            "checked": int,           # total rows scanned
            "dropped_missing": int,   # source file gone
            "dropped_legacy": int,    # excluded source type
            "kept": int,              # still pointing at live content
            "unknown": int,           # unrecognized source_type
        }
    """
    vault_root = config.vault_root
    legacy_types = {"conversation", "memory"}
    file_backed_types = {"page", "user", "wiki", "journal"}

    counts = {
        "checked": 0,
        "dropped_missing": 0,
        "dropped_legacy": 0,
        "kept": 0,
        "unknown": 0,
    }
    unknown_types_logged: set[str] = set()

    with _open_db(config) as conn:
        rows = conn.execute("SELECT id, file_path, source_type FROM memory_embeddings").fetchall()

    for row_id, file_path, source_type in rows:
        counts["checked"] += 1
        if source_type in legacy_types:
            _delete_one_row(config, row_id)
            counts["dropped_legacy"] += 1
            continue
        if source_type in file_backed_types:
            try:
                exists = (vault_root / file_path).exists()
            except OSError:
                exists = False
            if not exists:
                _delete_one_row(config, row_id)
                counts["dropped_missing"] += 1
            else:
                counts["kept"] += 1
            continue
        # Unknown source type — keep, but log once per type.
        counts["unknown"] += 1
        if source_type not in unknown_types_logged:
            log.warning(
                "prune_stale_embeddings: unknown source_type %r — keeping rows of this type",
                source_type,
            )
            unknown_types_logged.add(source_type)

    return counts


def _delete_one_row(config, row_id: int) -> None:
    """Delete a single ``memory_embeddings`` row + its
    ``embeddings_vec`` partner. Used by the prune sweep."""
    with _open_db(config) as conn:
        conn.execute("DELETE FROM memory_embeddings WHERE id = ?", (row_id,))
        conn.execute("DELETE FROM embeddings_vec WHERE rowid = ?", (row_id,))
        conn.commit()


def prune_embeddings_cli():
    """CLI entry point: drop stale rows from the embedding index."""
    import argparse
    import logging

    from .config import load_config

    parser = argparse.ArgumentParser(
        description="Drop stale rows from the DecafClaw embedding index "
        "(missing source files + legacy excluded source types). "
        "See #305.",
    )
    parser.add_argument("--quiet", action="store_true", help="Print only the summary counts (no per-row logs)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()
    db_path = _db_path(config)
    if not db_path.exists():
        print(f"No embedding index at {db_path}; nothing to prune.")
        return

    print(f"Embedding prune sweep — {db_path}\n")
    counts = prune_stale_embeddings(config)
    reclaimed = counts["dropped_missing"] + counts["dropped_legacy"]
    print(f"Scanned {counts['checked']} rows.")
    print(f"  kept:            {counts['kept']:>5} (live vault content)")
    print(f"  dropped_missing: {counts['dropped_missing']:>5} (source files gone)")
    print(f"  dropped_legacy:  {counts['dropped_legacy']:>5} (conversation / memory legacy)")
    print(f"  unknown:         {counts['unknown']:>5} (unrecognized source_type, kept)")
    print()
    print(f"Reclaimed {reclaimed} row(s).")


def reindex_cli():
    """CLI entry point: rebuild the embedding index from all sources (or a specific source)."""
    import argparse
    import asyncio
    import logging

    from .config import load_config

    parser = argparse.ArgumentParser(description="Rebuild DecafClaw embedding index")
    parser.add_argument("--vault", action="store_true", help="Reindex only vault pages")
    parser.add_argument("--journal", action="store_true", help="Reindex only journal entries")
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel embedding calls (default 4)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()
    db_path = _db_path(config)
    subset = args.vault or args.journal

    # Full rebuild: delete existing DB
    if not subset:
        if db_path.exists():
            db_path.unlink()
            print(f"Deleted existing index: {db_path}")

    print(f"Embedding model: {config.embedding.model}")

    async def _run():
        c = args.concurrency
        counts = {}
        if args.vault or not subset:
            counts["vault"] = await reindex_vault(config, concurrency=c)
        if args.journal or not subset:
            counts["journal"] = await reindex_journal(config, concurrency=c)
        return counts

    counts = asyncio.run(_run())
    parts = [f"{v} {k}" for k, v in counts.items()]
    print(f"Done: {' + '.join(parts)} → {db_path}")


def search_cli():
    """CLI entry point: search the embedding index."""
    import argparse
    import asyncio
    import logging

    from .config import load_config

    parser = argparse.ArgumentParser(description="Search DecafClaw embeddings")
    parser.add_argument("query", help="Search query")
    parser.add_argument(
        "--type", choices=["memory", "conversation", "all"], default="all", help="Source type to search (default: all)"
    )
    parser.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    config = load_config()

    source_type = args.type if args.type != "all" else None

    async def _search():
        return await search_similar(config, args.query, top_k=args.top_k, source_type=source_type)

    results = asyncio.run(_search())

    if not results:
        print(f"No results for '{args.query}'")
        return

    print(f"\n{len(results)} results for '{args.query}' (type={args.type}):\n")
    for i, r in enumerate(results):
        sim = f"{r['similarity']:.3f}"
        preview = r["entry_text"][:120].replace("\n", " ")
        print(f"  {i + 1}. [{sim}] ({r['file_path']}) {preview}...")
    print()
