"""Tests for memory operations."""

from decafclaw.memory import save_entry, search_entries, recent_entries, memory_dir


def test_memory_dir(config):
    path = memory_dir(config, "testuser")
    assert "test-agent" in str(path)
    assert "testuser" in str(path)


def test_save_and_search(config):
    save_entry(config, "testuser", "chan", "ch1", "th1",
               ["preference", "food"], "Likes pizza")

    result = search_entries(config, "testuser", "pizza")
    assert "pizza" in result.lower()
    assert "preference" in result


def test_save_and_recent(config):
    save_entry(config, "testuser", "chan", "ch1", "",
               ["fact"], "First entry")
    save_entry(config, "testuser", "chan", "ch1", "",
               ["fact"], "Second entry")

    result = recent_entries(config, "testuser", n=5)
    assert "First entry" in result
    assert "Second entry" in result


def test_search_no_results(config):
    save_entry(config, "testuser", "chan", "ch1", "",
               ["test"], "Some content")

    result = search_entries(config, "testuser", "nonexistent")
    assert "No memories found" in result


def test_search_case_insensitive(config):
    save_entry(config, "testuser", "chan", "ch1", "",
               ["test"], "Likes PIZZA")

    result = search_entries(config, "testuser", "pizza")
    assert "PIZZA" in result


def test_recent_empty(config):
    result = recent_entries(config, "testuser")
    assert "No memories found" in result


def test_no_user_id(config):
    result = save_entry(config, "", "chan", "ch1", "", ["test"], "content")
    assert "error" in result.lower()

    result = search_entries(config, "", "test")
    assert "error" in result.lower()
