"""Tests for embedding index operations."""

import struct
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.embeddings import (
    _deserialize_embedding,
    _entry_hash,
    _get_db,
    _serialize_embedding,
    index_entry_sync,
    search_similar_sync,
)


def test_serialize_roundtrip():
    vec = [0.1, 0.2, 0.3, 0.4]
    blob = _serialize_embedding(vec)
    result = _deserialize_embedding(blob)
    assert len(result) == 4
    assert abs(result[0] - 0.1) < 0.001


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
    vec = [1.0] * 768  # simple unit vector
    index_entry_sync(config, "test.md", "test entry about cats", vec)

    # Search with same vector should return it
    results = search_similar_sync(config, vec, top_k=5)
    assert len(results) == 1
    assert "cats" in results[0]["entry_text"]
    assert results[0]["similarity"] > 0.99


def test_source_type_filtering(config):
    """Entries with different source_types can be filtered."""
    vec_mem = [1.0, 0.0] + [0.0] * 766
    vec_conv = [0.0, 1.0] + [0.0] * 766

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
    vec = [1.0] * 768
    index_entry_sync(config, "test.md", "duplicate entry", vec)
    index_entry_sync(config, "test.md", "duplicate entry", vec)

    results = search_similar_sync(config, vec, top_k=5)
    assert len(results) == 1


def test_empty_search(config):
    """Searching empty DB returns empty list."""
    vec = [1.0] * 768
    results = search_similar_sync(config, vec, top_k=5)
    assert results == []


def test_model_sentinel(config):
    """Model name is stored in metadata."""
    vec = [1.0] * 768
    index_entry_sync(config, "test.md", "test", vec)

    import sqlite3
    conn = sqlite3.connect(str(config.workspace_path / "embeddings.db"))
    row = conn.execute("SELECT value FROM metadata WHERE key='embedding_model'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == config.embedding_model
