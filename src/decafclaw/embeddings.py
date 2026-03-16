"""Embedding index — SQLite storage and cosine similarity search."""

import hashlib
import json
import logging
import sqlite3
import struct
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger(__name__)

# Embedding dimension for text-embedding-004
EMBEDDING_DIM = 768


def _db_path(config) -> Path:
    """Path to the embeddings SQLite database."""
    return config.workspace_path / "embeddings.db"


def _get_db(config) -> sqlite3.Connection:
    """Get or create the embeddings database."""
    path = _db_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            entry_hash TEXT NOT NULL UNIQUE,
            entry_text TEXT NOT NULL,
            embedding BLOB NOT NULL,
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
    # Migration: add source_type column to existing DBs
    try:
        conn.execute("ALTER TABLE memory_embeddings ADD COLUMN source_type TEXT NOT NULL DEFAULT 'memory'")
    except sqlite3.OperationalError:
        pass  # column already exists
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


def _serialize_embedding(vec: list[float]) -> bytes:
    """Serialize a float list to bytes for SQLite BLOB storage."""
    return struct.pack(f'{len(vec)}f', *vec)


def _deserialize_embedding(blob: bytes) -> np.ndarray:
    """Deserialize bytes back to a numpy array."""
    n = len(blob) // 4  # 4 bytes per float32
    return np.array(struct.unpack(f'{n}f', blob), dtype=np.float32)


def _entry_hash(text: str) -> str:
    """SHA256 hash of entry text for deduplication."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def embed_text(config, text: str) -> list[float] | None:
    """Call the embedding API and return the vector."""
    url = config.effective_embedding_url
    api_key = config.effective_embedding_api_key

    body = {
        "model": config.embedding_model,
        "input": text,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=body, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        log.error(f"Embedding API call failed: {e}")
        return None


def _check_model(config, conn):
    """Verify the DB was built with the same embedding model. Warns on mismatch."""
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_model'").fetchone()
    if row and row[0] != config.embedding_model:
        log.warning(f"Embedding model mismatch: DB was built with '{row[0]}', "
                    f"config uses '{config.embedding_model}'. "
                    f"Run 'decafclaw-reindex' to rebuild.")


def _set_model(config, conn):
    """Record which embedding model was used."""
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES ('embedding_model', ?)",
        (config.embedding_model,),
    )


def index_entry_sync(config, file_path: str, entry_text: str, embedding: list[float],
                     source_type: str = "memory"):
    """Store an entry and its embedding in the index (sync)."""
    with _open_db(config) as conn:
        _set_model(config, conn)
        conn.execute(
            """INSERT OR IGNORE INTO memory_embeddings
               (file_path, entry_hash, entry_text, embedding, source_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                file_path,
                _entry_hash(entry_text),
                entry_text,
                _serialize_embedding(embedding),
                source_type,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()


def search_similar_sync(config, query_embedding: list[float], top_k: int = 5,
                        source_type: str | None = None) -> list[dict]:
    """Find the top K most similar entries by cosine similarity (sync).

    If source_type is specified, only search that type. Otherwise search all.
    """
    with _open_db(config) as conn:
        _check_model(config, conn)
        if source_type:
            rows = conn.execute(
                "SELECT entry_text, file_path, embedding FROM memory_embeddings WHERE source_type = ?",
                (source_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT entry_text, file_path, embedding FROM memory_embeddings"
            ).fetchall()

    if not rows:
        return []

    query_vec = np.array(query_embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)
    if query_norm == 0:
        return []

    results = []
    for entry_text, file_path, embedding_blob in rows:
        entry_vec = _deserialize_embedding(embedding_blob)
        entry_norm = np.linalg.norm(entry_vec)
        if entry_norm == 0:
            continue
        similarity = float(np.dot(query_vec, entry_vec) / (query_norm * entry_norm))
        results.append({
            "entry_text": entry_text,
            "file_path": file_path,
            "similarity": similarity,
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


def reindex_cli():
    """CLI entry point: rebuild the embedding index from all sources."""
    import asyncio
    import logging

    from .config import load_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    config = load_config()
    db_path = _db_path(config)

    # Delete existing DB to force full rebuild
    if db_path.exists():
        db_path.unlink()
        print(f"Deleted existing index: {db_path}")

    print(f"Embedding model: {config.embedding_model}")

    async def _reindex_all():
        mem_count = await reindex_all(config)
        conv_count = await reindex_conversations(config)
        return mem_count, conv_count

    mem_count, conv_count = asyncio.run(_reindex_all())
    print(f"Done: {mem_count} memory entries + {conv_count} conversation messages → {db_path}")


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
