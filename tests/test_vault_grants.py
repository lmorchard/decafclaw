"""Tests for vault grant sidecar I/O — `decafclaw.skills.vault._grants`."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.skills.vault import _grants


@pytest.fixture
def grants_config(tmp_path):
    """Minimal config with a workspace_path and vault_root for grants tests.

    Uses SimpleNamespace rather than the full Config to keep tests fast and
    independent of other config concerns. Mirrors the canvas test fixture.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault_root = workspace / "vault"
    vault_root.mkdir()
    return SimpleNamespace(
        workspace_path=workspace,
        vault_root=vault_root,
    )


class TestNormalizeFolder:
    def test_strips_leading_slash(self):
        assert _grants.normalize_folder("/creative") == "creative/"

    def test_enforces_trailing_slash(self):
        assert _grants.normalize_folder("creative") == "creative/"
        assert _grants.normalize_folder("creative/") == "creative/"

    def test_rejects_dotdot(self):
        assert _grants.normalize_folder("../etc") == ""
        assert _grants.normalize_folder("foo/../bar") == ""

    def test_rejects_empty(self):
        assert _grants.normalize_folder("") == ""
        assert _grants.normalize_folder("   ") == ""

    def test_rejects_non_string(self):
        assert _grants.normalize_folder(None) == ""  # type: ignore[arg-type]
        assert _grants.normalize_folder(42) == ""  # type: ignore[arg-type]

    def test_preserves_nested_path(self):
        assert _grants.normalize_folder("creative/in-progress") == "creative/in-progress/"

    def test_warn_on_invalid_logs_for_non_empty(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="decafclaw.skills.vault._grants"):
            assert _grants.normalize_folder("../etc", warn_on_invalid=True) == ""
        assert any("Skipping invalid vault folder entry" in r.message
                   for r in caplog.records)

    def test_warn_on_invalid_silent_for_empty(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="decafclaw.skills.vault._grants"):
            assert _grants.normalize_folder("", warn_on_invalid=True) == ""
            assert _grants.normalize_folder("   ", warn_on_invalid=True) == ""
        assert not any("Skipping invalid vault folder entry" in r.message
                       for r in caplog.records)

    def test_warn_off_silent_for_invalid(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="decafclaw.skills.vault._grants"):
            assert _grants.normalize_folder("../etc") == ""
        assert not any("Skipping invalid vault folder entry" in r.message
                       for r in caplog.records)


class TestGrantsSidecarPath:
    def test_basic(self, grants_config):
        path = _grants._grants_sidecar_path(grants_config, "abc123")
        expected = grants_config.workspace_path / "conversations" / "abc123.vault_grants.json"
        assert path == expected.resolve()

    def test_rejects_path_traversal_in_conv_id(self, grants_config):
        bad = _grants._grants_sidecar_path(grants_config, "../etc/passwd")
        assert bad.name == "_invalid.vault_grants.json"

    def test_rejects_slash_in_conv_id(self, grants_config):
        bad = _grants._grants_sidecar_path(grants_config, "foo/bar")
        assert bad.name == "_invalid.vault_grants.json"

    def test_rejects_backslash_in_conv_id(self, grants_config):
        bad = _grants._grants_sidecar_path(grants_config, "foo\\bar")
        assert bad.name == "_invalid.vault_grants.json"

    def test_returns_invalid_path_for_empty_conv_id(self, grants_config):
        bad = _grants._grants_sidecar_path(grants_config, "")
        assert bad.name == "_invalid.vault_grants.json"


class TestReadAddGrants:
    def test_empty_when_no_sidecar(self, grants_config):
        assert _grants.read_grants(grants_config, "nope") == set()

    def test_empty_when_conv_id_blank(self, grants_config):
        assert _grants.read_grants(grants_config, "") == set()

    def test_add_then_read_roundtrip(self, grants_config):
        assert _grants.add_grant(grants_config, "conv1", "creative") is True
        assert _grants.read_grants(grants_config, "conv1") == {"creative/"}

    def test_add_normalizes_input(self, grants_config):
        # Leading slash + missing trailing slash both get normalized.
        assert _grants.add_grant(grants_config, "conv1", "/foo") is True
        assert _grants.read_grants(grants_config, "conv1") == {"foo/"}

    def test_add_dedup(self, grants_config):
        _grants.add_grant(grants_config, "conv1", "creative/")
        _grants.add_grant(grants_config, "conv1", "creative")
        _grants.add_grant(grants_config, "conv1", "/creative/")
        assert _grants.read_grants(grants_config, "conv1") == {"creative/"}

    def test_add_multiple_grants(self, grants_config):
        _grants.add_grant(grants_config, "conv1", "creative/")
        _grants.add_grant(grants_config, "conv1", "notes/")
        assert _grants.read_grants(grants_config, "conv1") == {"creative/", "notes/"}

    def test_add_blank_conv_id_returns_false(self, grants_config):
        assert _grants.add_grant(grants_config, "", "creative") is False

    def test_add_invalid_folder_returns_false(self, grants_config):
        assert _grants.add_grant(grants_config, "conv1", "../etc") is False
        assert _grants.read_grants(grants_config, "conv1") == set()

    def test_add_empty_folder_returns_false(self, grants_config):
        assert _grants.add_grant(grants_config, "conv1", "") is False
        assert _grants.read_grants(grants_config, "conv1") == set()

    def test_corrupt_sidecar_treated_as_empty(self, grants_config):
        path = _grants._grants_sidecar_path(grants_config, "corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")
        assert _grants.read_grants(grants_config, "corrupt") == set()

    def test_filters_invalid_entries_on_read(self, grants_config):
        """Manually written sidecars with malformed entries are filtered out.

        Critical: an empty-string entry would normalize to "" and act as a
        wildcard prefix in is_path_in_grants, allowing every path through.
        """
        path = _grants._grants_sidecar_path(grants_config, "manual")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "folders": ["", "..", "../escape", "good/", 42, None],
        }))
        assert _grants.read_grants(grants_config, "manual") == {"good/"}

    def test_atomic_write_no_partial_tmp(self, grants_config):
        """Successful write goes through tmp file + rename — no leftover .tmp."""
        _grants.add_grant(grants_config, "conv1", "creative/")
        path = _grants._grants_sidecar_path(grants_config, "conv1")
        assert not path.with_suffix(".json.tmp").exists()
        # Verify the JSON payload shape on disk.
        data = json.loads(path.read_text())
        assert data == {"schema_version": 1, "folders": ["creative/"]}

    def test_legacy_sidecar_without_schema_version_still_reads(self, grants_config):
        """Sidecars written before schema_version was added should still load."""
        path = _grants._grants_sidecar_path(grants_config, "conv-legacy")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"folders": ["creative/"]}))
        assert _grants.read_grants(grants_config, "conv-legacy") == {"creative/"}


class TestIsPathInGrants:
    def test_path_under_granted_folder(self, grants_config):
        _grants.add_grant(grants_config, "conv1", "creative/")
        target = grants_config.vault_root / "creative" / "foo.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        assert _grants.is_path_in_grants(grants_config, "conv1", target) is True

    def test_path_under_nested_grant(self, grants_config):
        _grants.add_grant(grants_config, "conv1", "creative/in-progress/")
        target = grants_config.vault_root / "creative" / "in-progress" / "bar.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        assert _grants.is_path_in_grants(grants_config, "conv1", target) is True

    def test_path_not_under_any_grant(self, grants_config):
        _grants.add_grant(grants_config, "conv1", "creative/")
        target = grants_config.vault_root / "notes" / "bar.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        assert _grants.is_path_in_grants(grants_config, "conv1", target) is False

    def test_no_grants_returns_false(self, grants_config):
        target = grants_config.vault_root / "creative" / "foo.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        assert _grants.is_path_in_grants(grants_config, "conv1", target) is False

    def test_path_outside_vault_returns_false(self, grants_config, tmp_path):
        _grants.add_grant(grants_config, "conv1", "creative/")
        outside = tmp_path / "elsewhere" / "foo.md"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("x")
        assert _grants.is_path_in_grants(grants_config, "conv1", outside) is False

    def test_blank_conv_id_returns_false(self, grants_config):
        target = grants_config.vault_root / "creative" / "foo.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
        assert _grants.is_path_in_grants(grants_config, "", target) is False
