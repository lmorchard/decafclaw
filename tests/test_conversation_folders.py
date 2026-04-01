"""Tests for ConversationFolderIndex."""

import pytest

from decafclaw.web.conversation_folders import ConversationFolderIndex


@pytest.fixture
def folder_index(config):
    return ConversationFolderIndex(config, "testuser")


class TestCreateFolder:
    @pytest.mark.asyncio
    async def test_create_folder(self, folder_index):
        ok, err = await folder_index.create_folder("projects")
        assert ok
        assert err == ""
        folders = await folder_index.list_folders()
        assert "projects" in folders

    @pytest.mark.asyncio
    async def test_create_nested_folder_auto_creates_parents(self, folder_index):
        ok, err = await folder_index.create_folder("projects/bot-redesign")
        assert ok
        # Parent should exist too
        folders = await folder_index.list_folders()
        assert "projects" in folders
        nested = await folder_index.list_folders("projects")
        assert "bot-redesign" in nested

    @pytest.mark.asyncio
    async def test_create_duplicate_folder_fails(self, folder_index):
        await folder_index.create_folder("projects")
        ok, err = await folder_index.create_folder("projects")
        assert not ok
        assert "already exists" in err

    @pytest.mark.asyncio
    async def test_reject_underscore_prefix(self, folder_index):
        ok, err = await folder_index.create_folder("_system")
        assert not ok
        assert "reserved" in err

    @pytest.mark.asyncio
    async def test_reject_underscore_prefix_nested(self, folder_index):
        ok, err = await folder_index.create_folder("projects/_hidden")
        assert not ok
        assert "reserved" in err

    @pytest.mark.asyncio
    async def test_reject_path_traversal(self, folder_index):
        ok, err = await folder_index.create_folder("../escape")
        assert not ok
        assert ".." in err

    @pytest.mark.asyncio
    async def test_reject_leading_slash(self, folder_index):
        ok, err = await folder_index.create_folder("/absolute")
        assert not ok

    @pytest.mark.asyncio
    async def test_reject_empty_segments(self, folder_index):
        ok, err = await folder_index.create_folder("projects//nested")
        assert not ok
        assert "empty" in err


class TestDeleteFolder:
    @pytest.mark.asyncio
    async def test_delete_empty_folder(self, folder_index):
        await folder_index.create_folder("empty")
        ok, err = await folder_index.delete_folder("empty")
        assert ok
        folders = await folder_index.list_folders()
        assert "empty" not in folders

    @pytest.mark.asyncio
    async def test_delete_nonexistent_folder(self, folder_index):
        ok, err = await folder_index.delete_folder("nope")
        assert not ok
        assert "not found" in err

    @pytest.mark.asyncio
    async def test_delete_folder_with_conversations(self, folder_index):
        await folder_index.create_folder("active")
        await folder_index.set_folder("conv-1", "active")
        ok, err = await folder_index.delete_folder("active")
        assert not ok
        assert "contains conversations" in err

    @pytest.mark.asyncio
    async def test_delete_folder_with_subfolders(self, folder_index):
        await folder_index.create_folder("parent/child")
        ok, err = await folder_index.delete_folder("parent")
        assert not ok
        assert "contains subfolders" in err


class TestRenameFolder:
    @pytest.mark.asyncio
    async def test_rename_folder(self, folder_index):
        await folder_index.create_folder("old")
        await folder_index.set_folder("conv-1", "old")
        ok, err = await folder_index.rename_folder("old", "new")
        assert ok
        folders = await folder_index.list_folders()
        assert "new" in folders
        assert "old" not in folders
        assert await folder_index.get_folder("conv-1") == "new"

    @pytest.mark.asyncio
    async def test_rename_folder_with_children(self, folder_index):
        await folder_index.create_folder("old/child")
        await folder_index.set_folder("conv-1", "old/child")
        ok, err = await folder_index.rename_folder("old", "new")
        assert ok
        folders = await folder_index.list_folders()
        assert "new" in folders
        nested = await folder_index.list_folders("new")
        assert "child" in nested
        assert await folder_index.get_folder("conv-1") == "new/child"

    @pytest.mark.asyncio
    async def test_rename_nonexistent_folder(self, folder_index):
        ok, err = await folder_index.rename_folder("nope", "new")
        assert not ok
        assert "not found" in err

    @pytest.mark.asyncio
    async def test_rename_to_reserved_prefix(self, folder_index):
        await folder_index.create_folder("old")
        ok, err = await folder_index.rename_folder("old", "_reserved")
        assert not ok
        assert "reserved" in err

    @pytest.mark.asyncio
    async def test_rename_merge_on_collision(self, folder_index):
        await folder_index.create_folder("src")
        await folder_index.create_folder("dst")
        await folder_index.set_folder("conv-1", "src")
        await folder_index.set_folder("conv-2", "dst")
        ok, err = await folder_index.rename_folder("src", "dst")
        assert ok
        # Both conversations should be in dst
        assert await folder_index.get_folder("conv-1") == "dst"
        assert await folder_index.get_folder("conv-2") == "dst"
        folders = await folder_index.list_folders()
        assert "dst" in folders
        assert "src" not in folders


class TestAssignments:
    @pytest.mark.asyncio
    async def test_set_and_get_folder(self, folder_index):
        await folder_index.create_folder("projects")
        ok, err = await folder_index.set_folder("conv-1", "projects")
        assert ok
        assert await folder_index.get_folder("conv-1") == "projects"

    @pytest.mark.asyncio
    async def test_set_to_nonexistent_folder(self, folder_index):
        ok, err = await folder_index.set_folder("conv-1", "nope")
        assert not ok
        assert "does not exist" in err

    @pytest.mark.asyncio
    async def test_set_to_top_level(self, folder_index):
        await folder_index.create_folder("projects")
        await folder_index.set_folder("conv-1", "projects")
        ok, err = await folder_index.set_folder("conv-1", "")
        assert ok
        assert await folder_index.get_folder("conv-1") == ""

    @pytest.mark.asyncio
    async def test_remove_assignment(self, folder_index):
        await folder_index.create_folder("projects")
        await folder_index.set_folder("conv-1", "projects")
        await folder_index.remove_assignment("conv-1")
        assert await folder_index.get_folder("conv-1") == ""

    @pytest.mark.asyncio
    async def test_remove_nonexistent_assignment(self, folder_index):
        # Should not raise
        await folder_index.remove_assignment("nonexistent")

    @pytest.mark.asyncio
    async def test_get_unassigned_conversation(self, folder_index):
        assert await folder_index.get_folder("nonexistent") == ""

    @pytest.mark.asyncio
    async def test_list_conversations_in_folder(self, folder_index):
        await folder_index.create_folder("projects")
        await folder_index.set_folder("conv-1", "projects")
        await folder_index.set_folder("conv-2", "projects")
        convs = await folder_index.list_conversations_in_folder("projects")
        assert set(convs) == {"conv-1", "conv-2"}

    @pytest.mark.asyncio
    async def test_get_all_assignments(self, folder_index):
        await folder_index.create_folder("a")
        await folder_index.create_folder("b")
        await folder_index.set_folder("conv-1", "a")
        await folder_index.set_folder("conv-2", "b")
        assignments = await folder_index.get_all_assignments()
        assert assignments == {"conv-1": "a", "conv-2": "b"}


class TestListFolders:
    @pytest.mark.asyncio
    async def test_list_empty(self, folder_index):
        folders = await folder_index.list_folders()
        assert folders == []

    @pytest.mark.asyncio
    async def test_list_top_level(self, folder_index):
        await folder_index.create_folder("alpha")
        await folder_index.create_folder("beta")
        await folder_index.create_folder("alpha/child")
        folders = await folder_index.list_folders()
        assert folders == ["alpha", "beta"]

    @pytest.mark.asyncio
    async def test_list_nested(self, folder_index):
        await folder_index.create_folder("parent/child1")
        await folder_index.create_folder("parent/child2")
        folders = await folder_index.list_folders("parent")
        assert folders == ["child1", "child2"]

    @pytest.mark.asyncio
    async def test_list_nonexistent_parent(self, folder_index):
        folders = await folder_index.list_folders("nope")
        assert folders == []


class TestPersistence:
    @pytest.mark.asyncio
    async def test_data_persists_across_instances(self, config):
        idx1 = ConversationFolderIndex(config, "testuser")
        await idx1.create_folder("projects")
        await idx1.set_folder("conv-1", "projects")

        idx2 = ConversationFolderIndex(config, "testuser")
        assert await idx2.get_folder("conv-1") == "projects"
        folders = await idx2.list_folders()
        assert "projects" in folders
