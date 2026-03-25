"""Embedding index — SQLite storage and sqlite-vec cosine similarity search."""

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx
import sqlite_vec

log = logging.getLogger(__name__)



def _db_path(config) -> Path:
    """Path to the embeddings SQLite database."""
    return config.workspace_path / "embeddings.db"


def _has_embedding_column(conn) -> bool:
    """Check if memory_embeddings has the legacy embedding BLOB column."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(memory_embeddings)").fetchall()]
    return "embedding" in cols


def _get_db(config) -> sqlite3.Connection:
    """Get or create the embeddings database."""
    path = _db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
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
    except sqlite3.OperationalError:
        pass  # column already exists
    dim = config.embedding.dimensions
    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS embeddings_vec USING vec0(
            embedding float[{dim}] distance_metric=cosine
        )
    """)
    # Migration: copy legacy embedding BLOBs into vec0 table.
    # Uses NOT EXISTS so it's idempotent — safe to retry after partial migration.
    if _has_embedding_column(conn):
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


async def embed_text(config, text: str) -> list[float] | None:
    """Call the embedding API and return the vector."""
    ec = config.embedding.resolved(config)

    body = {
        "model": ec.model,
        "input": text,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ec.api_key}",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(ec.url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        log.error(f"Embedding API call failed: {e}")
        return None


def _check_model(config, conn):
    """Verify the DB was built with the same embedding model/dimensions. Warns on mismatch."""
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_model'").fetchone()
    if row and row[0] != config.embedding.model:
        log.warning(f"Embedding model mismatch: DB was built with '{row[0]}', "
                    f"config uses '{config.embedding.model}'. "
                    f"Run 'decafclaw-reindex' to rebuild.")
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_dimensions'").fetchone()
    if row and int(row[0]) != config.embedding.dimensions:
        log.warning(f"Embedding dimensions mismatch: DB was built with {row[0]}, "
                    f"config uses {config.embedding.dimensions}. "
                    f"Run 'decafclaw-reindex' to rebuild.")


def _set_model(config, conn):
    """Record which embedding model and dimensions were used."""
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedding_model', ?)",
        (config.embedding.model,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedding_dimensions', ?)",
        (str(config.embedding.dimensions),),
    )


def index_entry_sync(config, file_path: str, entry_text: str, embedding: list[float],
                     source_type: str = "memory"):
    """Store an entry and its embedding in the index (sync)."""
    with _open_db(config) as conn:
        _set_model(config, conn)
        entry_hash = _entry_hash(entry_text)
        now = datetime.now().isoformat()
        if _has_embedding_column(conn):
            # Legacy schema: pass empty blob for NOT NULL embedding column
            cursor = conn.execute(
                """INSERT OR IGNORE INTO memory_embeddings
                   (file_path, entry_hash, entry_text, embedding, source_type, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (file_path, entry_hash, entry_text, b'', source_type, now),
            )
        else:
            # Clean schema: no embedding column
            cursor = conn.execute(
                """INSERT OR IGNORE INTO memory_embeddings
                   (file_path, entry_hash, entry_text, source_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (file_path, entry_hash, entry_text, source_type, now),
            )
        if cursor.lastrowid:
            conn.execute(
                "INSERT INTO embeddings_vec(rowid, embedding) VALUES (?, ?)",
                (cursor.lastrowid, sqlite_vec.serialize_float32(embedding)),
            )
        conn.commit()


def delete_entries(config, file_path: str, source_type: str | None = None) -> int:
    """Delete embeddings entries by file_path (and optionally source_type).

    Returns the number of rows deleted.
    """
    with _open_db(config) as conn:
        if source_type:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM memory_embeddings WHERE file_path = ? AND source_type = ?",
                (file_path, source_type),
            ).fetchall()]
            conn.execute(
                "DELETE FROM memory_embeddings WHERE file_path = ? AND source_type = ?",
                (file_path, source_type),
            )
        else:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM memory_embeddings WHERE file_path = ?",
                (file_path,),
            ).fetchall()]
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
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM memory_embeddings WHERE source_type = ?",
            (source_type,),
        ).fetchall()]
        conn.execute(
            "DELETE FROM memory_embeddings WHERE source_type = ?",
            (source_type,),
        )
        conn.executemany("DELETE FROM embeddings_vec WHERE rowid = ?", [(i,) for i in ids])
        conn.commit()
        return len(ids)


def search_similar_sync(config, query_embedding: list[float], top_k: int = 5,
                        source_type: str | None = None) -> list[dict]:
    """Find the top K most similar entries by cosine similarity (sync).

    If source_type is specified, only search that type. Otherwise search all.
    Uses sqlite-vec's vec0 virtual table for SIMD-accelerated cosine distance.
    """
    with _open_db(config) as conn:
        _check_model(config, conn)

        query_vec = sqlite_vec.serialize_float32(query_embedding)

        # Over-fetch to allow for source_type filtering and wiki boost reranking
        fetch_k = top_k * 3

        rows = conn.execute("""
            SELECT m.entry_text, m.file_path, m.source_type, v.distance
            FROM (
                SELECT rowid, distance
                FROM embeddings_vec
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
            ) v
            JOIN memory_embeddings m ON m.id = v.rowid
        """, (query_vec, fetch_k)).fetchall()

    if not rows:
        return []

    # Score boost for curated wiki content
    WIKI_BOOST = 1.2

    results = []
    for entry_text, file_path, row_source_type, distance in rows:
        if source_type and row_source_type != source_type:
            continue
        similarity = 1.0 - distance
        if row_source_type == "wiki":
            similarity *= WIKI_BOOST
        results.append({
            "entry_text": entry_text,
            "file_path": file_path,
            "similarity": similarity,
            "source_type": row_source_type,
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


async def index_entry(config, file_path: str, entry_text: str, source_type: str = "memory"):
    """Embed and index an entry (async)."""
    embedding = await embed_text(config, entry_text)
    if embedding:
        index_entry_sync(config, file_path, entry_text, embedding, source_type=source_type)
        log.debug(f"Indexed {source_type} entry from {file_path}")


async def search_similar(config, query: str, top_k: int = 5,
                         source_type: str | None = None) -> list[dict]:
    """Embed a query and find similar entries (async).

    If source_type is specified, only search that type.
    If the memory index is empty but memory files exist, reindex first.
    """
    # Check if index needs building (only for memory type)
    if source_type is None or source_type == "memory":
        with _open_db(config) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM memory_embeddings WHERE source_type = 'memory'"
            ).fetchone()[0]

        if count == 0:
            log.info("Embedding index is empty, reindexing memories...")
            await reindex_all(config)

    query_embedding = await embed_text(config, query)
    if not query_embedding:
        return []
    return search_similar_sync(config, query_embedding, top_k, source_type=source_type)


async def reindex_all(config):
    """Rebuild the entire index from markdown memory files."""
    from .memory import memory_dir

    base = memory_dir(config)
    if not base.exists():
        log.info("No memory directory found, nothing to index")
        return 0

    md_files = sorted(base.rglob("*.md"))
    count = 0
    for fi, filepath in enumerate(md_files):
        text = filepath.read_text()
        parts = text.split("\n## ")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            entry = "## " + part if not part.startswith("## ") else part
            await index_entry(config, str(filepath.relative_to(base)), entry)
            count += 1
            if count % 10 == 0:
                print(f"  memories: {count} entries ({fi + 1}/{len(md_files)} files)...", flush=True)

    log.info(f"Reindexed {count} entries from {len(md_files)} files")
    return count


async def reindex_conversations(config):
    """Rebuild conversation embeddings from JSONL archive files."""
    conv_dir = config.workspace_path / "conversations"
    if not conv_dir.exists():
        log.info("No conversations directory found, nothing to index")
        return 0

    import json as _json
    jsonl_files = sorted(conv_dir.glob("*.jsonl"))
    count = 0
    for fi, filepath in enumerate(jsonl_files):
        conv_id = filepath.stem
        for line in filepath.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            msg = _json.loads(line)
            if msg.get("role") in ("user", "assistant"):
                content = msg.get("content", "")
                if content and len(content) > 20:
                    role = msg.get("role", "unknown")
                    entry_text = f"{role}: {content}"
                    await index_entry(config, conv_id, entry_text, source_type="conversation")
                    count += 1
                    if count % 10 == 0:
                        print(f"  conversations: {count} messages ({fi + 1}/{len(jsonl_files)} files)...", flush=True)

    log.info(f"Reindexed {count} conversation messages from {len(jsonl_files)} files")
    return count


async def reindex_wiki(config):
    """Rebuild wiki page embeddings."""
    wiki_dir = config.workspace_path / "wiki"
    if not wiki_dir.is_dir():
        log.info("No wiki directory found, nothing to index")
        return 0

    # Clear existing wiki entries to avoid stale rows
    deleted = delete_by_source_type(config, "wiki")
    if deleted:
        log.info(f"Cleared {deleted} existing wiki embedding(s)")

    md_files = sorted(wiki_dir.rglob("*.md"))
    count = 0
    for fi, filepath in enumerate(md_files):
        text = filepath.read_text().strip()
        if not text:
            continue
        rel_path = str(filepath.relative_to(config.workspace_path))
        await index_entry(config, rel_path, text, source_type="wiki")
        count += 1
        if count % 10 == 0:
            print(f"  wiki: {count} pages ({fi + 1}/{len(md_files)} files)...", flush=True)

    log.info(f"Reindexed {count} wiki pages from {len(md_files)} files")
    return count


def reindex_cli():
    """CLI entry point: rebuild the embedding index from all sources (or a specific source)."""
    import argparse
    import asyncio
    import logging

    from .config import load_config

    parser = argparse.ArgumentParser(description="Rebuild DecafClaw embedding index")
    parser.add_argument("--wiki", action="store_true", help="Reindex only wiki pages")
    parser.add_argument("--memory", action="store_true", help="Reindex only memories")
    parser.add_argument("--conversations", action="store_true", help="Reindex only conversations")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()
    db_path = _db_path(config)
    subset = args.wiki or args.memory or args.conversations

    # Full rebuild: delete existing DB
    if not subset:
        if db_path.exists():
            db_path.unlink()
            print(f"Deleted existing index: {db_path}")

    print(f"Embedding model: {config.embedding.model}")

    async def _run():
        counts = {}
        if args.wiki or not subset:
            counts["wiki"] = await reindex_wiki(config)
        if args.memory or not subset:
            counts["memory"] = await reindex_all(config)
        if args.conversations or not subset:
            counts["conversations"] = await reindex_conversations(config)
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
    parser.add_argument("--type", choices=["memory", "conversation", "all"], default="all",
                        help="Source type to search (default: all)")
    parser.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    config = load_config()

    source_type = args.type if args.type != "all" else None

    async def _search():
        return await search_similar(config, args.query, top_k=args.top_k,
                                     source_type=source_type)

    results = asyncio.run(_search())

    if not results:
        print(f"No results for '{args.query}'")
        return

    print(f"\n{len(results)} results for '{args.query}' (type={args.type}):\n")
    for i, r in enumerate(results):
        sim = f"{r['similarity']:.3f}"
        preview = r['entry_text'][:120].replace('\n', ' ')
        print(f"  {i+1}. [{sim}] ({r['file_path']}) {preview}...")
    print()
