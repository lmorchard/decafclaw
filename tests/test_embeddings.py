"""Tests for embedding index operations."""

import struct
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.embeddings import (
    _entry_hash,
    _get_db,
    _has_embedding_column,
    delete_by_source_type,
    delete_entries,
    index_entry_sync,
    prune_stale_embeddings,
    search_similar_sync,
)


def test_entry_hash_deterministic():
    h1 = _entry_hash("hello world")
    h2 = _entry_hash("hello world")
    assert h1 == h2


def test_entry_hash_differs():
    h1 = _entry_hash("hello")
    h2 = _entry_hash("world")
    assert h1 != h2


def test_index_and_search(config):
    """Index an entry and find it by similarity."""
    dim = config.embedding.dimensions
    vec = [1.0] * dim  # simple unit vector
    index_entry_sync(config, "test.md", "test entry about cats", vec)

    # Search with same vector should return it
    results = search_similar_sync(config, vec, top_k=5)
    assert len(results) == 1
    assert "cats" in results[0]["entry_text"]
    assert results[0]["similarity"] > 0.99


def test_source_type_filtering(config):
    """Entries with different source_types can be filtered."""
    dim = config.embedding.dimensions
    vec_mem = [1.0, 0.0] + [0.0] * (dim - 2)
    vec_conv = [0.0, 1.0] + [0.0] * (dim - 2)

    index_entry_sync(config, "mem.md", "memory entry", vec_mem, source_type="memory")
    index_entry_sync(config, "conv.jsonl", "conversation entry", vec_conv, source_type="conversation")

    # Search all
    results = search_similar_sync(config, vec_mem, top_k=5)
    assert len(results) == 2

    # Search memory only
    results = search_similar_sync(config, vec_mem, top_k=5, source_type="memory")
    assert len(results) == 1
    assert "memory" in results[0]["entry_text"]

    # Search conversation only
    results = search_similar_sync(config, vec_conv, top_k=5, source_type="conversation")
    assert len(results) == 1
    assert "conversation" in results[0]["entry_text"]


def test_dedup_by_hash(config):
    """Same entry text should not create duplicate entries."""
    vec = [1.0] * config.embedding.dimensions
    index_entry_sync(config, "test.md", "duplicate entry", vec)
    index_entry_sync(config, "test.md", "duplicate entry", vec)

    results = search_similar_sync(config, vec, top_k=5)
    assert len(results) == 1


def test_empty_search(config):
    """Searching empty DB returns empty list."""
    vec = [1.0] * config.embedding.dimensions
    results = search_similar_sync(config, vec, top_k=5)
    assert results == []


def test_vec0_populated_on_insert(config):
    """Inserting an entry also populates the vec0 table."""
    vec = [1.0] * config.embedding.dimensions
    index_entry_sync(config, "test.md", "test entry", vec)

    import sqlite3 as _sqlite3

    import sqlite_vec as _sv
    conn = _sqlite3.connect(str(config.workspace_path / "embeddings.db"))
    conn.enable_load_extension(True)
    _sv.load(conn)
    conn.enable_load_extension(False)
    rows = conn.execute("SELECT rowid FROM embeddings_vec").fetchall()
    conn.close()
    assert len(rows) == 1


def test_vec0_table_exists(config):
    """The vec0 virtual table is created alongside the metadata tables."""
    conn = _get_db(config)
    # vec0 shadow tables appear in sqlite_master
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    conn.close()
    assert "embeddings_vec" in tables


def test_model_sentinel(config):
    """Model name and dimensions are stored in metadata."""
    vec = [1.0] * config.embedding.dimensions
    index_entry_sync(config, "test.md", "test", vec)

    import sqlite3
    conn = sqlite3.connect(str(config.workspace_path / "embeddings.db"))
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_model'").fetchone()
    assert row is not None
    assert row[0] == config.embedding.model
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_dimensions'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == str(config.embedding.dimensions)


def test_fresh_db_no_embedding_column(config):
    """A freshly created DB has no legacy embedding BLOB column."""
    conn = _get_db(config)
    assert not _has_embedding_column(conn)
    conn.close()


def test_migration_from_legacy_db(config):
    """Old DBs with embedding BLOBs get auto-migrated to vec0."""
    import sqlite3 as _sqlite3

    # Create a legacy-schema DB manually
    db_path = config.workspace_path / "embeddings.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE memory_embeddings (
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
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)
    """)
    # Insert a row with a real packed-float32 embedding (768 = legacy default)
    dim = config.embedding.dimensions
    vec = [1.0] * dim
    blob = struct.pack(f'{len(vec)}f', *vec)
    conn.execute(
        "INSERT INTO memory_embeddings (file_path, entry_hash, entry_text, embedding, source_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("test.md", "abc123", "legacy entry about dogs", blob, "memory", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    # Now open via _get_db — should trigger migration
    conn2 = _get_db(config)
    vec_rows = conn2.execute("SELECT rowid FROM embeddings_vec").fetchall()
    conn2.close()
    assert len(vec_rows) == 1

    # Search should find the migrated entry
    results = search_similar_sync(config, vec, top_k=5)
    assert len(results) == 1
    assert "dogs" in results[0]["entry_text"]
    assert results[0]["similarity"] > 0.99


def test_delete_cleans_vec0(config):
    """Deleting entries also removes them from vec0."""
    vec = [1.0] * config.embedding.dimensions
    index_entry_sync(config, "test.md", "entry to delete", vec)

    # Verify it exists
    results = search_similar_sync(config, vec, top_k=5)
    assert len(results) == 1

    # Delete and verify vec0 is also cleaned
    deleted = delete_entries(config, "test.md")
    assert deleted == 1

    results = search_similar_sync(config, vec, top_k=5)
    assert results == []


def test_delete_by_source_type_cleans_vec0(config):
    """delete_by_source_type removes entries from both tables."""
    dim = config.embedding.dimensions
    vec_mem = [1.0, 0.0] + [0.0] * (dim - 2)
    vec_wiki = [0.0, 1.0] + [0.0] * (dim - 2)

    index_entry_sync(config, "mem.md", "memory entry", vec_mem, source_type="memory")
    index_entry_sync(config, "wiki.md", "wiki entry", vec_wiki, source_type="wiki")

    # Delete wiki entries
    deleted = delete_by_source_type(config, "wiki")
    assert deleted == 1

    # Memory entry should still be searchable
    results = search_similar_sync(config, vec_mem, top_k=5)
    assert len(results) == 1
    assert results[0]["source_type"] == "memory"

    # Wiki entry should be gone
    results = search_similar_sync(config, vec_wiki, top_k=5)
    assert len(results) == 1  # only memory entry remains
    assert results[0]["source_type"] == "memory"


# -- Prune stale embeddings (#305) ---------------------------------------------


def _seed_page(config, rel_path, source_type="page", body=None):
    """Create a vault file at vault_root/rel_path and index its embedding.

    `body` defaults to a unique-per-path string so the entry_hash unique
    constraint doesn't collapse multiple rows when tests seed several
    pages without specifying distinct bodies.
    """
    if body is None:
        body = f"content for {rel_path}"
    full = config.vault_root / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(body)
    dim = config.embedding.dimensions
    vec = [1.0] + [0.0] * (dim - 1)
    index_entry_sync(config, rel_path, body, vec, source_type=source_type)


def _seed_orphan(config, rel_path, source_type="page"):
    """Index an embedding whose source file does NOT exist on disk.

    Uses a path-derived body so each call inserts a distinct row even
    when the caller doesn't otherwise differentiate them.
    """
    dim = config.embedding.dimensions
    vec = [0.5] + [0.0] * (dim - 1)
    body = f"ghost for {rel_path} ({source_type})"
    index_entry_sync(config, rel_path, body, vec, source_type=source_type)


class TestPruneStaleEmbeddings:
    def test_drops_missing_files_keeps_live(self, config):
        config.vault_root.mkdir(parents=True, exist_ok=True)
        _seed_page(config, "live.md", source_type="page", body="here")
        _seed_orphan(config, "gone.md", source_type="page")
        _seed_page(config, "agent/journal/2026-04-27.md", source_type="journal", body="j")
        _seed_orphan(config, "agent/journal/2026-01-01.md", source_type="journal")

        counts = prune_stale_embeddings(config)
        assert counts["checked"] == 4
        assert counts["dropped_missing"] == 2
        assert counts["kept"] == 2
        assert counts["dropped_legacy"] == 0

    def test_drops_legacy_source_types_unconditionally(self, config):
        config.vault_root.mkdir(parents=True, exist_ok=True)
        _seed_orphan(config, "anything.md", source_type="conversation")
        _seed_orphan(config, "anything2.md", source_type="memory")
        # Even with a real backing file, conversation/memory should drop.
        _seed_page(config, "real.md", source_type="conversation", body="x")

        counts = prune_stale_embeddings(config)
        assert counts["checked"] == 3
        assert counts["dropped_legacy"] == 3
        assert counts["kept"] == 0

    def test_keeps_unknown_source_types(self, config, caplog):
        config.vault_root.mkdir(parents=True, exist_ok=True)
        _seed_orphan(config, "x.md", source_type="frobnicate")
        _seed_orphan(config, "y.md", source_type="frobnicate")

        counts = prune_stale_embeddings(config)
        assert counts["checked"] == 2
        assert counts["unknown"] == 2
        assert counts["kept"] == 0
        assert counts["dropped_missing"] == 0

    def test_user_and_wiki_legacy_treated_as_file_backed(self, config):
        config.vault_root.mkdir(parents=True, exist_ok=True)
        # `wiki` is the legacy alias for `page`; treated like a page.
        _seed_page(config, "user_page.md", source_type="user", body="u")
        _seed_orphan(config, "old_wiki.md", source_type="wiki")

        counts = prune_stale_embeddings(config)
        assert counts["checked"] == 2
        assert counts["kept"] == 1
        assert counts["dropped_missing"] == 1

    def test_idempotent_second_run_no_op(self, config):
        config.vault_root.mkdir(parents=True, exist_ok=True)
        _seed_orphan(config, "ghost.md", source_type="page")

        first = prune_stale_embeddings(config)
        assert first["dropped_missing"] == 1
        # Second run sees no rows to drop.
        second = prune_stale_embeddings(config)
        assert second == {
            "checked": 0, "dropped_missing": 0, "dropped_legacy": 0,
            "kept": 0, "unknown": 0,
        }

    def test_empty_db_returns_zeros(self, config):
        counts = prune_stale_embeddings(config)
        assert counts == {
            "checked": 0, "dropped_missing": 0, "dropped_legacy": 0,
            "kept": 0, "unknown": 0,
        }

    def test_dropped_rows_remove_from_vec0(self, config):
        """Pruned rows are removed from the vec0 table too — search
        should not surface them."""
        config.vault_root.mkdir(parents=True, exist_ok=True)
        _seed_page(config, "live.md", source_type="page", body="live")
        _seed_orphan(config, "gone.md", source_type="page")

        prune_stale_embeddings(config)

        # Search now returns only the live row.
        dim = config.embedding.dimensions
        results = search_similar_sync(config, [1.0] + [0.0] * (dim - 1), top_k=10)
        paths = [r["file_path"] for r in results]
        assert "live.md" in paths
        assert "gone.md" not in paths
