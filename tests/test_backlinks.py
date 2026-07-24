"""Tests for the persistent backlink index (#197 Phase 4).

Replaces the brute-force rglob scan in ``tool_vault_backlinks`` with a
persistent JSON index at ``{workspace}/backlinks.json``: ``page -> [inbound
linker pages]``. Provides ``inbound_count`` for Phase 5's importance
formula, and an incremental ``update_for_page`` so a single edit doesn't
require rescanning the whole vault.
"""

import json

import pytest

from decafclaw.backlinks import (
    _index_path,
    inbound_count,
    load_index,
    make_backlinks_subscriber,
    rebuild_index,
    update_for_page,
)


@pytest.fixture
def vault_dir(config):
    """Create the vault directory."""
    d = config.vault_root
    d.mkdir(parents=True, exist_ok=True)
    return d


class TestRebuildIndex:
    def test_builds_inbound_map(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("# A\n\nLinks to [[PageB]] and [[PageC]].")
        (vault_dir / "PageB.md").write_text("# B\n\nLinks to [[PageC]].")
        (vault_dir / "PageC.md").write_text("# C\n\nNo outbound links.")

        index = rebuild_index(config)

        assert index["PageB.md"] == ["PageA.md"]
        assert index["PageC.md"] == ["PageA.md", "PageB.md"]
        assert "PageA.md" not in index  # nothing links to A

    def test_persists_human_readable_json(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("# A\n\nLinks to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")

        rebuild_index(config)

        path = _index_path(config)
        assert path.exists()
        raw = path.read_text(encoding="utf-8")
        assert "\n" in raw  # indented, not a single-line blob
        data = json.loads(raw)
        assert data["PageB.md"] == ["PageA.md"]

    def test_case_insensitive_link_resolution(self, config, vault_dir):
        """A lowercase [[target]] link should still resolve to Target.md."""
        (vault_dir / "Target.md").write_text("# Target")
        (vault_dir / "Linker.md").write_text("See [[target]] for details.")

        index = rebuild_index(config)

        assert index["Target.md"] == ["Linker.md"]

    def test_skips_self_links(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageA]] itself.")

        index = rebuild_index(config)

        assert index == {}

    def test_ignores_dangling_links(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[NoSuchPage]].")

        index = rebuild_index(config)

        assert index == {}

    def test_missing_vault_dir_returns_empty(self, config):
        # config.vault_root deliberately not created
        assert rebuild_index(config) == {}


class TestLoadIndex:
    def test_rebuilds_lazily_when_missing(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        assert not _index_path(config).exists()

        index = load_index(config)

        assert index["PageB.md"] == ["PageA.md"]
        assert _index_path(config).exists()

    def test_reads_persisted_index_without_rescanning(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        rebuild_index(config)

        # Mutate the page on disk without calling rebuild_index/update_for_page.
        # load_index must return the stale persisted data, not re-scan.
        (vault_dir / "PageA.md").write_text("No links now.")

        index = load_index(config)

        assert index["PageB.md"] == ["PageA.md"]

    def test_corrupt_json_rebuilds(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        path = _index_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")

        index = load_index(config)

        assert index["PageB.md"] == ["PageA.md"]


class TestInboundCount:
    def test_counts_distinct_linkers(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageC]].")
        (vault_dir / "PageB.md").write_text("Links to [[PageC]].")
        (vault_dir / "PageC.md").write_text("# C")
        rebuild_index(config)

        assert inbound_count(config, "PageC") == 2
        assert inbound_count(config, "PageA") == 0

    def test_unknown_page_returns_zero(self, config, vault_dir):
        assert inbound_count(config, "DoesNotExist") == 0

    def test_missing_index_rebuilds_lazily(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        assert not _index_path(config).exists()

        assert inbound_count(config, "PageB") == 1


class TestUpdateForPage:
    def test_incremental_add_link(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("# A\n\nNo links yet.")
        (vault_dir / "PageB.md").write_text("# B")
        rebuild_index(config)
        assert inbound_count(config, "PageB") == 0

        (vault_dir / "PageA.md").write_text("# A\n\nNow linking to [[PageB]].")
        update_for_page(config, "PageA")

        index = load_index(config)
        assert index["PageB.md"] == ["PageA.md"]
        assert inbound_count(config, "PageB") == 1

    def test_incremental_remove_link(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        rebuild_index(config)
        assert inbound_count(config, "PageB") == 1

        (vault_dir / "PageA.md").write_text("No more links.")
        update_for_page(config, "PageA")

        index = load_index(config)
        assert "PageB.md" not in index

    def test_incremental_does_not_touch_unrelated_pages(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageC]].")
        (vault_dir / "PageB.md").write_text("Links to [[PageC]].")
        (vault_dir / "PageC.md").write_text("# C")
        rebuild_index(config)

        (vault_dir / "PageA.md").write_text("No more links.")
        update_for_page(config, "PageA")

        index = load_index(config)
        assert index["PageC.md"] == ["PageB.md"]

    def test_builds_index_if_missing(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        assert not _index_path(config).exists()

        update_for_page(config, "PageA")

        index = load_index(config)
        assert index["PageB.md"] == ["PageA.md"]

    def test_handles_deleted_page(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("Links to [[PageB]].")
        (vault_dir / "PageB.md").write_text("# B")
        rebuild_index(config)
        assert inbound_count(config, "PageB") == 1

        (vault_dir / "PageA.md").unlink()
        update_for_page(config, "PageA.md")

        index = load_index(config)
        assert "PageB.md" not in index

    def test_unresolvable_page_is_fail_open(self, config, vault_dir):
        # Should not raise even though the page never existed.
        update_for_page(config, "NeverExisted")
        assert load_index(config) == {}


class TestBacklinksSubscriber:
    @pytest.mark.asyncio
    async def test_updates_index_on_vault_changed(self, config, vault_dir):
        (vault_dir / "PageA.md").write_text("# A")
        (vault_dir / "PageB.md").write_text("# B")
        rebuild_index(config)
        (vault_dir / "PageA.md").write_text("Now links to [[PageB]].")

        handler = make_backlinks_subscriber(config)
        await handler({"type": "vault_changed", "kind": "update", "path": "PageA.md"})

        assert inbound_count(config, "PageB") == 1

    @pytest.mark.asyncio
    async def test_ignores_other_event_types(self, config, vault_dir):
        handler = make_backlinks_subscriber(config)
        await handler({"type": "tool_end"})  # must not raise

    @pytest.mark.asyncio
    async def test_ignores_empty_path(self, config, vault_dir):
        handler = make_backlinks_subscriber(config)
        # Must not raise on multi-file ops with no single path.
        await handler({"type": "vault_changed", "kind": "delete", "path": ""})

    @pytest.mark.asyncio
    async def test_fail_open_on_bad_event(self, config, vault_dir):
        handler = make_backlinks_subscriber(config)
        await handler({"type": "vault_changed"})  # missing "path" key entirely
