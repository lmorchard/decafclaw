"""Tests for to-do list operations."""

from decafclaw.todos import todo_add, todo_complete, todo_list, todo_clear


def test_add_and_list(config):
    result = todo_add(config, "test-conv", "First item")
    assert "First item" in result

    result = todo_list(config, "test-conv")
    assert "First item" in result
    assert "[ ]" in result


def test_complete(config):
    todo_add(config, "test-conv", "Item one")
    todo_add(config, "test-conv", "Item two")

    result = todo_complete(config, "test-conv", 1)
    assert "Completed" in result

    result = todo_list(config, "test-conv")
    assert "[x]" in result


def test_complete_invalid_index(config):
    todo_add(config, "test-conv", "Only item")
    result = todo_complete(config, "test-conv", 5)
    assert "error" in result.lower()


def test_clear(config):
    todo_add(config, "test-conv", "Item")
    result = todo_clear(config, "test-conv")
    assert "cleared" in result.lower()

    result = todo_list(config, "test-conv")
    assert "No to-do" in result


def test_empty_list(config):
    result = todo_list(config, "test-conv")
    assert "No to-do" in result


def test_persists_on_disk(config):
    todo_add(config, "test-conv", "Persistent item")

    # Read the file directly
    path = config.workspace_path / "todos" / "test-conv.md"
    assert path.exists()
    content = path.read_text()
    assert "- [ ] Persistent item" in content
