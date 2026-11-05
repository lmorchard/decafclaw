"""Tests for the make-dev watchfiles filter's config handling (#149).

watchfiles itself is only installed under the `dev` extra, so these tests
stub the module before importing decafclaw._dev_filter and exercise the
filter's own path matching. The restart classification logic it delegates
to is tested in test_config_watcher.py via ConfigFileClassifier.
"""

import importlib
import json
import sys
import types

import pytest


@pytest.fixture
def dev_filter(monkeypatch):
    """Import decafclaw._dev_filter with a stubbed watchfiles module."""
    stub = types.ModuleType("watchfiles")

    class _StubPythonFilter:
        def __init__(self):
            self.matched_nothing = True

        def __call__(self, change, path):
            return False

    stub.PythonFilter = _StubPythonFilter
    monkeypatch.setitem(sys.modules, "watchfiles", stub)
    module = importlib.import_module("decafclaw._dev_filter")
    return module.DevFilter()


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_filter_matches_widget_and_config_paths_only(dev_filter, tmp_path):
    """Non-.py paths must be widget or agent-config files; everything else drops."""
    config_path = tmp_path / "data" / "test-agent" / "config.json"
    _write(config_path, {"llm": {"model": "m"}})
    assert dev_filter.__call__(None, str(config_path)) is False  # first sighting: cache only

    _write(config_path, {"http": {"enabled": True}})
    assert dev_filter.__call__(None, str(config_path)) is True  # agent config, unsafe delta
    widget = str(tmp_path / "src" / "decafclaw" / "web" / "static" / "widgets" / "foo" / "widget.json")
    assert dev_filter.__call__(None, widget) is True  # bundled widget
    archive = str(tmp_path / "data" / "test-agent" / "workspace" / "conversations" / "x.jsonl")
    assert dev_filter.__call__(None, archive) is False  # conversation archive
    vault = str(tmp_path / "data" / "test-agent" / "workspace" / "vault" / "Page.md")
    assert dev_filter.__call__(None, vault) is False  # vault page


def test_filter_restart_required_config_change(dev_filter, tmp_path):
    """An http-section delta restarts the dev server."""
    config_path = tmp_path / "data" / "test-agent" / "config.json"
    _write(config_path, {"llm": {"model": "custom-model"}})

    assert dev_filter.__call__(None, str(config_path)) is False  # first sighting: cache only
    assert dev_filter.__call__(None, str(config_path)) is False  # no change

    _write(config_path, {"http": {"enabled": True}})
    assert dev_filter.__call__(None, str(config_path)) is True  # unsafe delta


def test_filter_safe_config_change_does_not_restart(dev_filter, tmp_path):
    """A safe-only delta must not restart — the watcher applies it in place."""
    config_path = tmp_path / "data" / "test-agent" / "config.json"
    _write(config_path, {})

    assert dev_filter.__call__(None, str(config_path)) is False  # first sighting
    _write(config_path, {"llm": {"model": "custom-model"}})
    assert dev_filter.__call__(None, str(config_path)) is False


def test_filter_mcp_servers_change_restarts(dev_filter, tmp_path):
    """Any mcp_servers.json change is restart-required."""
    mcp_path = tmp_path / "data" / "test-agent" / "mcp_servers.json"
    _write(mcp_path, {"mcpServers": {}})

    assert dev_filter.__call__(None, str(mcp_path)) is False  # first sighting
    _write(mcp_path, {"mcpServers": {"x": {"command": "echo"}}})
    assert dev_filter.__call__(None, str(mcp_path)) is True


def test_filter_torn_write_does_not_restart(dev_filter, tmp_path):
    """Unparseable JSON (torn/partial write) never restarts the dev server."""
    config_path = tmp_path / "data" / "test-agent" / "config.json"
    _write(config_path, {"http": {"enabled": True}})
    assert dev_filter.__call__(None, str(config_path)) is False  # first sighting

    config_path.write_text('{"http": {"en')
    assert dev_filter.__call__(None, str(config_path)) is False
