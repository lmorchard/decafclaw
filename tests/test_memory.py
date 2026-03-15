"""Tests for memory operations."""

from decafclaw.memory import memory_dir, recent_entries, save_entry, search_entries


def test_memory_dir(config):
    path = memory_dir(config)
    assert "test-agent" in str(path)
    assert "memories" in str(path)


def test_save_and_search(config):
    save_entry(config, "chan", "ch1", "th1",
               ["preference", "food"], "Likes pizza")

    result = search_entries(config, "pizza")
    assert "pizza" in result.lower()
    assert "preference" in result


def test_save_and_recent(config):
    save_entry(config, "chan", "ch1", "",
               ["fact"], "First entry")
    save_entry(config, "chan", "ch1", "",
               ["fact"], "Second entry")

    result = recent_entries(config, n=5)
    assert "First entry" in result
    assert "Second entry" in result


def test_search_no_results(config):
    save_entry(config, "chan", "ch1", "",
               ["test"], "Some content")

    result = search_entries(config, "nonexistent")
    assert "No memories found" in result


def test_search_case_insensitive(config):
    save_entry(config, "chan", "ch1", "",
               ["test"], "Likes PIZZA")

    result = search_entries(config, "pizza")
    assert "PIZZA" in result


def test_search_returns_whole_entry(config):
    """Search should return the entire entry, not just context lines."""
    save_entry(config, "chan", "ch1", "th1",
               ["preference", "drink"], "Likes Boulevardier cocktails")

    result = search_entries(config, "Boulevardier")
    # Should include the tags and channel metadata, not just the matching line
    assert "preference" in result
    assert "drink" in result
    assert "chan" in result


def test_recent_empty(config):
    result = recent_entries(config)
    assert "No memories found" in result


def test_search_by_tag(config):
    save_entry(config, "chan", "ch1", "",
               ["project", "architecture"], "Event bus design")

    result = search_entries(config, "architecture")
    assert "Event bus design" in result
