"""Tests for the literal-secret → ``${VAR}`` migration script (#1032).

scripts/ is not a package, so we load the module by path via
importlib.util.spec_from_file_location (mirroring tests/test_message_types.py
and tests/test_migrate_sidecars.py) and exercise the config-free core function
``migrate_secrets`` directly against tmp_path config/env-store paths.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts/migrate_secrets.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("migrate_secrets", _SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MODULE = _load_module()
migrate_secrets = _MODULE.migrate_secrets


def _write_config(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def test_literal_secret_becomes_env_ref_and_store_entry(tmp_path):
    config_path = _write_config(tmp_path / "config.json", {"llm": {"api_key": "sk-live-abc"}})
    env_path = tmp_path / ".env"

    migrated = migrate_secrets(config_path, env_path, dry_run=False, secret_paths=["llm.api_key"])

    assert len(migrated) == 1
    assert migrated[0]["action"] == "migrate"
    assert migrated[0]["ref"] == "${LLM_API_KEY}"
    # External store gained the value; config.json now holds the ref.
    assert env_path.read_text().strip() == "LLM_API_KEY=sk-live-abc"
    assert json.loads(config_path.read_text()) == {"llm": {"api_key": "${LLM_API_KEY}"}}


def test_migration_is_idempotent(tmp_path):
    config_path = _write_config(tmp_path / "config.json", {"llm": {"api_key": "sk-live-abc"}})
    env_path = tmp_path / ".env"

    first = migrate_secrets(config_path, env_path, dry_run=False, secret_paths=["llm.api_key"])
    second = migrate_secrets(config_path, env_path, dry_run=False, secret_paths=["llm.api_key"])

    assert len(first) == 1
    # Second run: the config now holds the ref, so there is nothing left to migrate.
    assert second == []
    assert json.loads(config_path.read_text()) == {"llm": {"api_key": "${LLM_API_KEY}"}}


def test_dry_run_writes_nothing(tmp_path):
    config_path = _write_config(tmp_path / "config.json", {"llm": {"api_key": "sk-live-abc"}})
    env_path = tmp_path / ".env"

    migrated = migrate_secrets(config_path, env_path, dry_run=True, secret_paths=["llm.api_key"])

    assert len(migrated) == 1
    assert not env_path.exists()
    assert json.loads(config_path.read_text()) == {"llm": {"api_key": "sk-live-abc"}}


def test_ref_already_present_is_skipped(tmp_path):
    config_path = _write_config(tmp_path / "config.json", {"llm": {"api_key": "${LLM_API_KEY}"}})
    env_path = tmp_path / ".env"

    migrated = migrate_secrets(config_path, env_path, dry_run=False, secret_paths=["llm.api_key"])

    assert migrated == []
    assert not env_path.exists()


def test_env_literal_moves_into_external_store(tmp_path):
    config_path = _write_config(tmp_path / "config.json", {"env": {"OPENAI_API_KEY": "sk-live-xyz"}})
    env_path = tmp_path / ".env"

    migrated = migrate_secrets(config_path, env_path, dry_run=False, secret_paths=["env.OPENAI_API_KEY"])

    assert len(migrated) == 1
    assert migrated[0]["ref"] == "${OPENAI_API_KEY}"
    assert env_path.read_text().strip() == "OPENAI_API_KEY=sk-live-xyz"
    # The literal leaves the config env section; the var lives in the store.
    assert json.loads(config_path.read_text()) == {"env": {}}


def test_non_secret_literal_is_untouched(tmp_path):
    config_path = _write_config(
        tmp_path / "config.json", {"llm": {"model": "gemini-2.5-flash", "api_key": "sk-live-abc"}}
    )
    env_path = tmp_path / ".env"

    migrated = migrate_secrets(config_path, env_path, dry_run=False, secret_paths=["llm.api_key"])

    assert len(migrated) == 1
    assert json.loads(config_path.read_text()) == {"llm": {"model": "gemini-2.5-flash", "api_key": "${LLM_API_KEY}"}}
