"""Tests for embedding index operations."""

import logging
import struct
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

import decafclaw.embeddings as embeddings_module
from decafclaw.embeddings import (
    _entry_hash,
    _fts_match_query,
    _get_db,
    _has_embedding_column,
    _open_db,
    delete_by_source_type,
    delete_entries,
    index_entry_sync,
    prune_stale_embeddings,
    search_fts_sync,
    search_similar_sync,
)

# Captured before the autouse ``mock_embed_text`` fixture can patch them, so
# the fastembed routing tests exercise the real implementation rather than
# the suite-wide stub.
_REAL_EMBED_TEXT = embeddings_module.embed_text
_REAL_EMBED_TEXTS = embeddings_module.embed_texts

_FAKE_MODEL_DIMS = {
    "BAAI/bge-small-en-v1.5": 384,
    "fake/tiny": 4,
}


def _fake_vector(text: str, dim: int) -> list[float]:
    seed = sum(ord(char) for char in text) % 89 + 1
    return [float((seed * (i + 1)) % 97) / 97.0 for i in range(dim)]


class _FakeTextEmbedding:
    """Drop-in stand-in for ``fastembed.TextEmbedding`` — no weights, no network."""

    embed_calls: list[list[str]] = []

    def __init__(self, model_name: str, **_kwargs):
        if model_name not in _FAKE_MODEL_DIMS:
            raise ValueError(f"unsupported fake model {model_name!r}")
        self.model_name = model_name
        self.dim = _FAKE_MODEL_DIMS[model_name]

    def embed(self, texts, **_kwargs):
        texts = list(texts)
        type(self).embed_calls.append(texts)
        for text in texts:
            yield _fake_vector(text, self.dim)

    @staticmethod
    def list_supported_models():
        return [{"model": name, "dim": dim} for name, dim in _FAKE_MODEL_DIMS.items()]


@pytest.fixture
def fake_fastembed(monkeypatch):
    """Inject a fake ``fastembed`` module and reset its derived-dimension cache."""
    from decafclaw.llm import clear_providers

    _FakeTextEmbedding.embed_calls = []
    module = types.ModuleType("fastembed")
    module.TextEmbedding = _FakeTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    embeddings_module._fastembed_model_dimensions.cache_clear()
    yield _FakeTextEmbedding
    embeddings_module._fastembed_model_dimensions.cache_clear()
    clear_providers()


def _configure_fastembed(config, model: str = "BAAI/bge-small-en-v1.5"):
    config.embedding.provider = "fastembed"
    config.embedding.model = model
    config.embedding.search_strategy = "semantic"


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
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
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
    blob = struct.pack(f"{len(vec)}f", *vec)
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
            "checked": 0,
            "dropped_missing": 0,
            "dropped_legacy": 0,
            "kept": 0,
            "unknown": 0,
        }

    def test_empty_db_returns_zeros(self, config):
        counts = prune_stale_embeddings(config)
        assert counts == {
            "checked": 0,
            "dropped_missing": 0,
            "dropped_legacy": 0,
            "kept": 0,
            "unknown": 0,
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


# -- Local fastembed provider (#1459) -----------------------------------------


async def test_fastembed_embed_text(config, fake_fastembed):
    """A configured fastembed provider embeds locally with the model's dimension."""
    from decafclaw.llm import init_providers

    _configure_fastembed(config)
    init_providers(config)

    vector = await _REAL_EMBED_TEXT(config, "hello world")

    assert vector == _fake_vector("hello world", 384)
    assert len(vector) == 384  # BAAI/bge-small-en-v1.5


async def test_fastembed_vault_reindex(config, fake_fastembed):
    """Vault reindex uses the provider's batch path and records derived dimensions."""
    from decafclaw.llm import init_providers

    _configure_fastembed(config)
    init_providers(config)

    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (pages_dir / f"page-{i}.md").write_text(f"# Page {i}\n\nbody number {i}\n")

    count = await embeddings_module.reindex_vault(config)

    assert count == 3
    # One batch call for the whole vault, not one embed_text per entry.
    assert fake_fastembed.embed_calls
    assert sum(len(call) for call in fake_fastembed.embed_calls) == 3

    with embeddings_module._open_db(config) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 3
        assert embeddings_module._vec_table_dimensions(conn) == 384
        assert embeddings_module._stored_dimensions(conn) == 384


async def test_fastembed_resolves_without_init_providers(config, fake_fastembed):
    """CLI entry points never call init_providers(); fastembed still embeds.

    Regression for the empty-registry ``KeyError`` that made ``make reindex``
    rebuild an empty index while reporting success.
    """
    from decafclaw.llm import clear_providers

    clear_providers()
    _configure_fastembed(config)

    assert await _REAL_EMBED_TEXT(config, "hello local") == _fake_vector("hello local", 384)


async def test_fastembed_reindex_without_init_providers(config, fake_fastembed):
    """A reindex with no prior provider init writes rows, not an empty index."""
    from decafclaw.llm import clear_providers

    clear_providers()
    _configure_fastembed(config)

    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (pages_dir / f"cli-page-{i}.md").write_text(f"# CLI Page {i}\n\nlocal body {i}\n")

    assert await embeddings_module.reindex_vault(config) == 2

    with embeddings_module._open_db(config) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 2
        assert embeddings_module._vec_table_dimensions(conn) == 384
        assert embeddings_module._stored_dimensions(conn) == 384


async def test_fastembed_unavailable_does_not_fall_back_to_cloud(config, monkeypatch, caplog):
    """A missing library logs an actionable error and never calls the remote endpoint."""
    import httpx

    from decafclaw.llm import clear_providers, init_providers

    class _BoomClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("legacy remote embedding endpoint must not be called")

    monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)
    monkeypatch.setitem(sys.modules, "fastembed", None)
    _configure_fastembed(config)
    init_providers(config)

    with caplog.at_level(logging.ERROR):
        assert await _REAL_EMBED_TEXT(config, "private note") is None
    assert "not installed" in caplog.text
    assert "pip install fastembed" in caplog.text

    # After the registry is cleared, the provider is resolved lazily again and
    # still refuses to touch the remote endpoint.
    clear_providers()
    caplog.clear()
    with caplog.at_level(logging.ERROR):
        assert await _REAL_EMBED_TEXT(config, "private note") is None
    assert "not installed" in caplog.text


async def test_fastembed_refuses_remote_fallback_when_unresolved(config, monkeypatch, caplog):
    """Defense in depth: an unresolved fastembed provider must not reach the cloud."""
    monkeypatch.setattr(embeddings_module, "_resolve_embedding_provider", lambda _config: ("fastembed", None))

    with caplog.at_level(logging.ERROR):
        assert await _REAL_EMBED_TEXT(config, "private note") is None
    assert "Refusing to fall back" in caplog.text


async def test_embed_texts_gathers_for_providers_without_batch(config, monkeypatch):
    """Non-fastembed providers keep the per-text concurrency path."""
    from decafclaw.llm.registry import register_provider

    class _Provider:
        async def embed(self, model, text, **_kwargs):
            return [float(len(text))]

    # Undo the suite-wide embed_text stub for this test: embed_texts calls the
    # module-global embed_text, which must be the real one here.
    monkeypatch.setattr(embeddings_module, "embed_text", _REAL_EMBED_TEXT)
    register_provider("unit-provider", _Provider())
    config.embedding.provider = "unit-provider"

    assert await _REAL_EMBED_TEXTS(config, ["a", "bb"]) == [[1.0], [2.0]]
    assert await _REAL_EMBED_TEXTS(config, []) == []


def test_embedding_dimension_mismatch_warning(config, fake_fastembed, caplog):
    """A 768-dim index searched with fastembed's 384-dim vectors warns once."""
    dim = config.embedding.dimensions
    assert dim == 768
    index_entry_sync(config, "note.md", "hello", [1.0] + [0.0] * (dim - 1), source_type="page")

    _configure_fastembed(config)  # BAAI/bge-small-en-v1.5 -> 384 dims
    query = [1.0] + [0.0] * 383

    with caplog.at_level(logging.WARNING):
        results = search_similar_sync(config, query)

    assert results == []  # no sqlite error from the vec0 MATCH
    assert caplog.text.count("Embedding dimensions mismatch") == 1
    assert "768" in caplog.text
    assert "384" in caplog.text
    assert "make reindex" in caplog.text
    assert "decafclaw-reindex" in caplog.text


def test_embedding_dimension_mismatch_warning_generic_provider(config, caplog):
    """The default-provider path also warns once and returns no results."""
    dim = config.embedding.dimensions
    index_entry_sync(config, "note.md", "hello", [1.0] + [0.0] * (dim - 1), source_type="page")

    config.embedding.dimensions = 4
    with caplog.at_level(logging.WARNING):
        results = search_similar_sync(config, [1.0, 0.0, 0.0, 0.0])

    assert results == []
    assert caplog.text.count("Embedding dimensions mismatch") == 1
    assert "make reindex" in caplog.text
    assert "decafclaw-reindex" in caplog.text


async def test_partial_reindex_skips_on_dimension_mismatch(config, caplog):
    """Partial reindex keeps the old index instead of mixing dimensions."""
    dim = config.embedding.dimensions
    index_entry_sync(config, "note.md", "hello", [1.0] + [0.0] * (dim - 1), source_type="page")

    pages_dir = config.vault_agent_pages_dir
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "new.md").write_text("# New\n\nfresh page\n")

    config.embedding.dimensions = 4
    with caplog.at_level(logging.WARNING):
        assert await embeddings_module.reindex_vault(config) == 0

    assert "make reindex" in caplog.text
    with embeddings_module._open_db(config) as conn:
        assert conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0] == 1


def test_format_duration():
    """Verify human duration formatting."""
    assert embeddings_module._format_duration(5) == "5s"
    assert embeddings_module._format_duration(65) == "1m 05s"
    assert embeddings_module._format_duration(3665) == "1h 01m"


def test_format_progress():
    """Verify progress line format with throughput and ETA."""
    line = embeddings_module._format_progress(50, 100, 10.0, "vault pages")
    assert "vault pages: 50/100 (50.0%)" in line
    assert "300.0 items/min" in line
    assert "Elapsed: 10s" in line
    assert "ETA: 10s" in line

    completed_line = embeddings_module._format_progress(100, 100, 20.0, "vault pages")
    assert "vault pages: 100/100 (100.0%)" in completed_line
    assert "Total: 20s" in completed_line


# -- FTS5 lexical index (#1816) -----------------------------------------------


def _index_page(config, path: str, text: str, source_type: str = "page") -> None:
    index_entry_sync(
        config,
        path,
        text,
        [1.0] * config.embedding.dimensions,
        source_type=source_type,
    )


def test_fts_indexes_entries_and_matches_exact_token(config):
    _index_page(config, "pages/ports.md", "The daemon listens on port 32848 for admin traffic.")
    _index_page(config, "pages/networking.md", "Generic networking concepts and routing notes.")

    results = search_fts_sync(config, "32848", top_k=5)
    assert [r["file_path"] for r in results] == ["pages/ports.md"]
    assert isinstance(results[0]["id"], int)
    assert results[0]["bm25"] < 0


def test_fts_matches_hyphenated_identifier_tokens(config):
    _index_page(config, "pages/hosts.md", "myriad-docker runs the companion services.")
    _index_page(config, "pages/other.md", "Docker is a container runtime.")

    results = search_fts_sync(config, "myriad-docker", top_k=5)
    assert results[0]["file_path"] == "pages/hosts.md"


def test_fts_match_query_sanitizes_punctuation():
    query = _fts_match_query('port: 32848 "quoted" --no-token #1805')
    assert query == '"port" OR "32848" OR "quoted" OR "no" OR "token" OR "1805"'


def test_fts_match_query_empty_input():
    assert _fts_match_query("!!! ...") == ""


def test_fts_empty_query_returns_no_results(config):
    assert search_fts_sync(config, "!!! ...") == []


def test_fts_delete_entries_removes_rows(config):
    _index_page(config, "pages/ports.md", "The daemon listens on port 32848.")

    assert delete_entries(config, "pages/ports.md") == 1
    assert search_fts_sync(config, "32848") == []


def test_fts_delete_by_source_type_removes_rows(config):
    _index_page(config, "pages/ports.md", "The daemon listens on port 32848.")

    assert delete_by_source_type(config, "page") == 1
    assert search_fts_sync(config, "32848") == []


def test_fts_backfills_rows_when_table_is_empty(config):
    _index_page(config, "pages/ports.md", "The daemon listens on port 32848.")

    with _open_db(config) as conn:
        conn.execute("DELETE FROM fts_pages")
        conn.commit()

    results = search_fts_sync(config, "32848")
    assert [r["file_path"] for r in results] == ["pages/ports.md"]


def test_fts_search_filters_source_type(config):
    _index_page(config, "pages/ports.md", "The daemon listens on port 32848.", source_type="page")
    _index_page(config, "journal/2026-01-01.md", "## Ports\n\nOpened port 32848.", source_type="journal")

    results = search_fts_sync(config, "32848", source_type="journal")
    assert [r["file_path"] for r in results] == ["journal/2026-01-01.md"]


def test_search_similar_results_include_row_id(config):
    _index_page(config, "pages/ports.md", "The daemon listens on port 32848.")

    results = search_similar_sync(config, [1.0] * config.embedding.dimensions, top_k=5)
    assert isinstance(results[0]["id"], int)
