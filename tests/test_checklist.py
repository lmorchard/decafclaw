"""Tests for checklist backend operations."""

from decafclaw.checklist import (
    checklist_abort,
    checklist_complete_current,
    checklist_create,
    checklist_get_current,
    checklist_status,
)


def test_create_and_status(config):
    items = checklist_create(config, "test-conv", ["Step A", "Step B", "Step C"])
    assert len(items) == 3
    assert items[0]["text"] == "Step A"
    assert not items[0]["done"]

    status = checklist_status(config, "test-conv")
    assert len(status) == 3


def test_get_current(config):
    checklist_create(config, "test-conv", ["Step 1", "Step 2"])
    current = checklist_get_current(config, "test-conv")
    assert current is not None
    assert current["index"] == 1
    assert current["text"] == "Step 1"
    assert current["total"] == 2


def test_complete_current_advances(config):
    checklist_create(config, "test-conv", ["Step 1", "Step 2", "Step 3"])

    next_item = checklist_complete_current(config, "test-conv", note="did it")
    assert next_item is not None
    assert next_item["index"] == 2
    assert next_item["text"] == "Step 2"

    # Verify step 1 is done with note
    status = checklist_status(config, "test-conv")
    assert status[0]["done"] is True
    assert status[0]["note"] == "did it"


def test_complete_all_returns_none(config):
    checklist_create(config, "test-conv", ["Only step"])
    result = checklist_complete_current(config, "test-conv")
    assert result is None  # all done

    # Verify it's complete
    status = checklist_status(config, "test-conv")
    assert all(item["done"] for item in status)


def test_complete_empty_returns_none(config):
    result = checklist_complete_current(config, "test-conv")
    assert result is None


def test_abort_clears_checklist(config):
    checklist_create(config, "test-conv", ["Step 1", "Step 2"])
    checklist_abort(config, "test-conv")

    status = checklist_status(config, "test-conv")
    assert status == []


def test_abort_nonexistent_is_safe(config):
    checklist_abort(config, "test-conv")  # should not raise


def test_create_overwrites_existing(config):
    checklist_create(config, "test-conv", ["Old step"])
    checklist_create(config, "test-conv", ["New A", "New B"])

    status = checklist_status(config, "test-conv")
    assert len(status) == 2
    assert status[0]["text"] == "New A"


def test_persists_on_disk(config):
    checklist_create(config, "test-conv", ["Persistent step"])

    path = config.workspace_path / "todos" / "test-conv.md"
    assert path.exists()
    content = path.read_text()
    assert "- [ ] Persistent step" in content


def test_done_note_persists_on_disk(config):
    checklist_create(config, "test-conv", ["Step 1", "Step 2"])
    checklist_complete_current(config, "test-conv", note="finished this")

    path = config.workspace_path / "todos" / "test-conv.md"
    content = path.read_text()
    assert "- [x] Step 1 [done: finished this]" in content
    assert "- [ ] Step 2" in content


def test_get_current_when_all_done(config):
    checklist_create(config, "test-conv", ["Only"])
    checklist_complete_current(config, "test-conv")

    current = checklist_get_current(config, "test-conv")
    assert current is None


def test_empty_checklist_status(config):
    status = checklist_status(config, "test-conv")
    assert status == []
