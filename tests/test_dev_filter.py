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
def dev_filter_module(monkeypatch):
    """Import decafclaw._dev_filter with a stubbed watchfiles module."""
    stub = types.ModuleType("watchfiles")

    class _StubPythonFilter:
        def __init__(self):
            self.matched_nothing = True

        def __call__(self, change, path):
            return False

    stub.PythonFilter = _StubPythonFilter
    monkeypatch.setitem(sys.modules, "watchfiles", stub)
    return importlib.import_module("decafclaw._dev_filter")


@pytest.fixture
def agent_env(monkeypatch, tmp_path):
    """Point DATA_HOME/AGENT_ID at a scratch agent dir; skip .env loading."""
    monkeypatch.setenv("DECACLAW_LOAD_DOTENV", "0")
    monkeypatch.setenv("DATA_HOME", str(tmp_path / "data-home"))
    monkeypatch.setenv("AGENT_ID", "test-agent")
    return tmp_path / "data-home" / "test-agent"


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_first_unsafe_change_restarts(dev_filter_module, agent_env):
    """Regression (#149 review): the filter must compare the first edit after
    startup against the files as the server loaded them. The old
    first-sighting-only-cached behavior let the in-process watcher stop the
    server with no supervisor restart — a dead dev session."""
    config_path = agent_env / "config.json"
    _write(config_path, {"mattermost": {"url": "https://mm.example"}})
    dev_filter = dev_filter_module.DevFilter()

    _write(config_path, {"mattermost": {"url": "https://other.example"}})
    assert dev_filter.__call__(None, str(config_path)) is True


def test_safe_first_change_does_not_restart(dev_filter_module, agent_env):
    """A safe-only delta must not restart — the watcher applies it in place."""
    config_path = agent_env / "config.json"
    _write(config_path, {"llm": {"model": "start-model"}})
    dev_filter = dev_filter_module.DevFilter()

    _write(config_path, {"llm": {"model": "custom-model"}})
    assert dev_filter.__call__(None, str(config_path)) is False


def test_mcp_servers_first_change_restarts(dev_filter_module, agent_env):
    """Any mcp_servers.json change is restart-required, including the first."""
    mcp_path = agent_env / "mcp_servers.json"
    _write(mcp_path, {"mcpServers": {}})
    dev_filter = dev_filter_module.DevFilter()

    _write(mcp_path, {"mcpServers": {"x": {"command": "echo"}}})
    assert dev_filter.__call__(None, str(mcp_path)) is True


def test_created_config_with_unsafe_section_restarts(dev_filter_module, agent_env):
    """A config.json appearing after startup with a restart-required section
    restarts: the starting process loaded defaults, so this is a real delta."""
    dev_filter = dev_filter_module.DevFilter()  # no config files yet

    config_path = agent_env / "config.json"
    _write(config_path, {"http": {"enabled": True}})
    assert dev_filter.__call__(None, str(config_path)) is True


def test_torn_write_does_not_restart(dev_filter_module, agent_env):
    """Unparseable JSON (torn/partial write) never restarts the dev server."""
    config_path = agent_env / "config.json"
    _write(config_path, {"http": {"enabled": True}})
    dev_filter = dev_filter_module.DevFilter()

    config_path.write_text('{"http": {"en')
    assert dev_filter.__call__(None, str(config_path)) is False


def test_only_the_resolved_agent_config_paths_match(dev_filter_module, agent_env, tmp_path):
    """Exact-path matching (#149 review): a config.json outside the resolved
    agent dir never matches, even under a literal data/ directory."""
    dev_filter = dev_filter_module.DevFilter()

    stray = tmp_path / "data" / "other-agent" / "config.json"
    _write(stray, {"http": {"enabled": True}})
    assert dev_filter.__call__(None, str(stray)) is False

    # Non-config files under the agent dir still drop, widgets still pass.
    archive = str(agent_env / "workspace" / "conversations" / "x.jsonl")
    assert dev_filter.__call__(None, archive) is False
    widget = str(tmp_path / "src" / "decafclaw" / "web" / "static" / "widgets" / "foo" / "widget.json")
    assert dev_filter.__call__(None, widget) is True


def test_deleted_config_file_restarts(dev_filter_module, agent_env):
    """A config file vanishing mid-run is a change worth restarting for."""
    config_path = agent_env / "config.json"
    _write(config_path, {"mattermost": {"url": "https://mm.example"}})
    dev_filter = dev_filter_module.DevFilter()

    config_path.unlink()
    assert dev_filter.__call__(None, str(config_path)) is True
