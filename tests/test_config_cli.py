"""Tests for the config CLI tool."""

import json
import os

import pytest

from decafclaw.config_cli import cmd_get, cmd_import_env, cmd_set, cmd_show


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Prevent .env file from leaking into tests."""
    monkeypatch.setattr("decafclaw.config.load_dotenv", lambda **kw: None)
    for key in list(os.environ):
        if any(key.startswith(p) for p in (
            "LLM_", "MATTERMOST_", "COMPACTION_", "EMBEDDING_",
            "HEARTBEAT_", "HTTP_", "TABSTACK_", "CLAUDE_CODE_",
            "SKILLS_", "MEMORY_SEARCH", "SYSTEM_PROMPT",
        )):
            monkeypatch.delenv(key, raising=False)


class _Args:
    """Simple namespace for argparse-like args."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_show_masks_secrets(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_show(_Args(group="llm", reveal=False))
    out = capsys.readouterr().out
    assert "****" in out
    assert "llm.url" in out


def test_show_reveal(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_show(_Args(group="llm", reveal=True))
    out = capsys.readouterr().out
    assert "****" not in out
    assert "llm.api_key = dummy" in out


def test_show_group_filter(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_show(_Args(group="heartbeat", reveal=False))
    out = capsys.readouterr().out
    assert "heartbeat.interval" in out
    assert "llm." not in out


def test_show_top_level_scalar_fields(capsys, monkeypatch, tmp_path):
    """Top-level scalar/list fields appear in unfiltered output (issue #431)."""
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_show(_Args(group=None, reveal=False))
    out = capsys.readouterr().out
    # Scalars and lists that are NOT nested dataclasses used to be dropped.
    assert "default_model = " in out
    assert "extra_skill_paths = " in out
    assert "skills_always_loaded = " in out


def test_show_excludes_runtime_only_fields(capsys, monkeypatch, tmp_path):
    """Runtime-only (non-config-file) fields stay out of `show` output."""
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_show(_Args(group=None, reveal=False))
    out = capsys.readouterr().out
    assert "system_prompt" not in out
    assert "discovered_skills" not in out
    assert "always_loaded_skill_tools" not in out
    assert "skill_tool_owners" not in out


def test_show_providers_masks_api_key(capsys, monkeypatch, tmp_path):
    """providers dict prints per-entry fields, masking api_key by default."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({
        "providers": {
            "openai": {"type": "openai", "api_key": "sk-secret-123"},
        },
    }))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_show(_Args(group=None, reveal=False))
    out = capsys.readouterr().out
    assert "providers.openai.type = openai" in out
    assert "providers.openai.api_key = ****" in out
    assert "sk-secret-123" not in out


def test_show_providers_reveal(capsys, monkeypatch, tmp_path):
    """--reveal unmasks provider secrets."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({
        "providers": {
            "openai": {"type": "openai", "api_key": "sk-secret-123"},
        },
    }))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_show(_Args(group="providers", reveal=True))
    out = capsys.readouterr().out
    assert "providers.openai.api_key = sk-secret-123" in out


def test_show_model_configs(capsys, monkeypatch, tmp_path):
    """model_configs dict prints per-entry fields."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({
        "model_configs": {
            "flash": {"provider": "vertex", "model": "gemini-2.5-flash"},
        },
    }))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_show(_Args(group="model_configs", reveal=False))
    out = capsys.readouterr().out
    assert "model_configs.flash.provider = vertex" in out
    assert "model_configs.flash.model = gemini-2.5-flash" in out


def test_show_top_level_field_filter(capsys, monkeypatch, tmp_path):
    """A top-level scalar name is a valid `show` filter, not an unknown group."""
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_show(_Args(group="default_model", reveal=False))
    out = capsys.readouterr().out
    assert "default_model = " in out
    assert "llm." not in out


def test_get(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    cmd_get(_Args(path="llm.model"))
    out = capsys.readouterr().out.strip()
    assert out == "gemini-2.5-flash"


def test_get_unknown_path(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    (tmp_path / "decafclaw").mkdir()
    with pytest.raises(SystemExit):
        cmd_get(_Args(path="nonexistent.field"))


def test_set_creates_file(monkeypatch, tmp_path):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_set(_Args(path="llm.model", value="new-model"))
    config_file = agent_dir / "config.json"
    assert config_file.exists()
    data = json.loads(config_file.read_text())
    assert data["llm"]["model"] == "new-model"


def test_set_preserves_existing(monkeypatch, tmp_path):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    config_file = agent_dir / "config.json"
    config_file.write_text(json.dumps({"llm": {"url": "http://existing"}}))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_set(_Args(path="llm.model", value="added-model"))
    data = json.loads(config_file.read_text())
    assert data["llm"]["url"] == "http://existing"
    assert data["llm"]["model"] == "added-model"


def test_set_bool_coercion(monkeypatch, tmp_path):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_set(_Args(path="mattermost.require_mention", value="false"))
    data = json.loads((agent_dir / "config.json").read_text())
    assert data["mattermost"]["require_mention"] is False


def test_set_int_coercion(monkeypatch, tmp_path):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    cmd_set(_Args(path="mattermost.debounce_ms", value="2000"))
    data = json.loads((agent_dir / "config.json").read_text())
    assert data["mattermost"]["debounce_ms"] == 2000


def test_import_env(capsys, monkeypatch, tmp_path):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    env_file = tmp_path / "test.env"
    env_file.write_text(
        '# Comment\n'
        'LLM_MODEL=test-model\n'
        'MATTERMOST_URL=https://mm.test.com\n'
        'HTTP_PORT=9999\n'
        'CUSTOM_API_KEY=secret123\n'
    )
    cmd_import_env(_Args(file=str(env_file)))
    data = json.loads((agent_dir / "config.json").read_text())
    assert data["llm"]["model"] == "test-model"
    assert data["mattermost"]["url"] == "https://mm.test.com"
    assert data["http"]["port"] == 9999
    # Unknown vars go to env section
    assert data["env"]["CUSTOM_API_KEY"] == "secret123"
    out = capsys.readouterr().out
    assert "3 settings" in out
    assert "1 env vars" in out
