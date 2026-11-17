"""Tests for generic extension configuration and the extension type registry (#1458)."""

import json
import logging
import os

import pytest

from decafclaw.config import Config, load_config
from decafclaw.config_types import ExtensionConfig
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import (
    BaseExtension,
    clear_extension_types,
    get_extension_type,
    init_extensions,
    list_extension_types,
    register_extension_type,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Prevent .env and ambient MEMORY_* env from leaking into tests."""
    monkeypatch.setattr("decafclaw.config.load_dotenv", lambda **kw: None)
    for key in list(os.environ):
        if key.startswith("MEMORY_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_extension_types()
    yield
    clear_extension_types()


class _RecordingExtension(BaseExtension):
    def __init__(self, name: str, options: dict, config: Config):
        super().__init__(name=name)
        self.options = options
        self.config = config
        self.enabled = False

    def enable(self, bus: EventBus) -> list[str]:
        self.enabled = True
        self.subscription_ids = [bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.noop_hook)]
        return self.subscription_ids

    async def noop_hook(self, ctx, user_message, history=None):
        return None


def _recording_factory(name: str):
    calls: list[tuple[dict, Config]] = []

    def factory(options: dict, config: Config):
        calls.append((options, config))
        return _RecordingExtension(name, options, config)

    factory.calls = calls  # type: ignore[attr-defined]
    return factory


def test_generic_extension_initialization():
    """WHEN config.extensions defines an enabled extension with custom options,
    init_extensions SHALL instantiate it via the registered factory and enable
    its lifecycle hooks on EventBus.
    """
    factory = _recording_factory("demo")
    register_extension_type("demo", factory)

    config = Config()
    config.extensions["demo"] = ExtensionConfig(enabled=True, type="demo", options={"greeting": "hi", "limit": 3})
    bus = EventBus()
    manager = init_extensions(config, bus)

    assert manager.is_enabled("demo")
    assert factory.calls == [({"greeting": "hi", "limit": 3}, config)]
    assert len(bus.get_lifecycle_hooks(TurnLifecycle.PRE_COMPOSE)) == 1


def test_disabled_extension_skipped():
    """WHEN an extension is disabled, init_extensions SHALL NOT instantiate or register it."""
    factory = _recording_factory("off")
    register_extension_type("off", factory)

    config = Config()
    config.extensions["off"] = ExtensionConfig(enabled=False, type="off", options={})
    manager = init_extensions(config, EventBus())

    assert manager.list_extensions() == []
    assert factory.calls == []


def test_legacy_memory_config_backward_compatibility(tmp_path, monkeypatch, caplog):
    """WHEN config.json has a legacy memory block and no extensions block,
    load_config SHALL map it to extensions["mem0"] with equivalent options.
    """
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps(
            {
                "memory": {
                    "provider": "mem0",
                    "qdrant_path": "/tmp/legacy-qdrant",
                    "embedder_model": "BAAI/bge-small-en-v1.5",
                    "llm_base_url": "http://localhost:4100/v1",
                    "llm_model": "legacy-model",
                    "api_key": "legacy-key",
                    "top_k": 9,
                }
            }
        )
    )
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    with caplog.at_level(logging.WARNING, logger="decafclaw.config"):
        config = load_config()

    mem0 = config.extensions["mem0"]
    assert mem0.enabled is True
    assert mem0.type == "mem0"
    assert mem0.options == {
        "qdrant_path": "/tmp/legacy-qdrant",
        "embedder_model": "BAAI/bge-small-en-v1.5",
        "llm_base_url": "http://localhost:4100/v1",
        "llm_model": "legacy-model",
        "api_key": "legacy-key",
        "top_k": 9,
    }
    assert any("extensions.mem0" in rec.message for rec in caplog.records)
    # Migration is in memory only — the file is untouched.
    assert "extensions" not in json.loads((agent_dir / "config.json").read_text())


def test_extension_type_falls_back_to_config_key(tmp_path, monkeypatch):
    """An empty ``type`` resolves the factory from the config key."""
    factory = _recording_factory("bykey")
    register_extension_type("bykey", factory)

    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"extensions": {"bykey": {"options": {"x": 1}}}}))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    config = load_config()
    assert config.extensions["bykey"].type == ""
    manager = init_extensions(config, EventBus())
    assert manager.is_enabled("bykey")


def test_unknown_extension_type_warns_and_skips(caplog):
    config = Config()
    config.extensions["stale"] = ExtensionConfig(enabled=True, type="does-not-exist", options={})

    with caplog.at_level(logging.WARNING, logger="decafclaw.extensions"):
        manager = init_extensions(config, EventBus())

    assert manager.list_extensions() == []
    assert any("Unknown extension type" in rec.message for rec in caplog.records)


def test_factory_exception_warns_and_skips(caplog):
    def boom(options: dict, config: Config):
        raise RuntimeError("bad options")

    register_extension_type("boom", boom)
    config = Config()
    config.extensions["boom"] = ExtensionConfig(enabled=True, type="boom", options={})

    with caplog.at_level(logging.WARNING, logger="decafclaw.extensions"):
        manager = init_extensions(config, EventBus())

    assert manager.list_extensions() == []
    assert any("failed to initialize" in rec.message for rec in caplog.records)


def test_duplicate_registration_warns(caplog):
    factory = _recording_factory("dup")
    register_extension_type("dup", factory)

    with caplog.at_level(logging.WARNING, logger="decafclaw.extensions"):
        register_extension_type("dup", factory)

    assert get_extension_type("dup") is factory
    assert any("Replacing already-registered extension type" in rec.message for rec in caplog.records)


def test_registry_isolation():
    register_extension_type("one", _recording_factory("one"))
    assert list_extension_types() == ["one"]

    clear_extension_types()

    assert list_extension_types() == []
    assert get_extension_type("one") is None


def test_malformed_extension_entry_warns(tmp_path, monkeypatch, caplog):
    """Malformed entries/options degrade to warnings, never a startup crash."""
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps(
            {
                "extensions": {
                    "not-an-object": True,
                    "bad-options": {
                        "type": "bad-options",
                        "options": ["not", "a", "dict"],
                        "mystery_key": 1,
                    },
                }
            }
        )
    )
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    with caplog.at_level(logging.WARNING, logger="decafclaw.config"):
        config = load_config()

    assert "not-an-object" not in config.extensions
    entry = config.extensions["bad-options"]
    assert entry.options == {}
    assert entry.enabled is True
    messages = " ".join(rec.message for rec in caplog.records)
    assert "expected an object" in messages
    assert "unknown key" in messages
    assert "options must be an object" in messages


def test_legacy_memory_ignored_when_extensions_mem0_present(tmp_path, monkeypatch, caplog):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps(
            {
                "memory": {"provider": "mem0", "top_k": 2},
                "extensions": {"mem0": {"type": "mem0", "options": {"top_k": 11}}},
            }
        )
    )
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    with caplog.at_level(logging.WARNING, logger="decafclaw.config"):
        config = load_config()

    assert config.extensions["mem0"].options == {"top_k": 11}
    assert any("ignored because extensions.mem0" in rec.message for rec in caplog.records)


def test_legacy_memory_without_mem0_provider_creates_no_extension(tmp_path, monkeypatch):
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"memory": {"top_k": 4}}))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))

    assert load_config().extensions == {}


def test_legacy_memory_env_only_backward_compatibility(tmp_path, monkeypatch, caplog):
    """WHEN only MEMORY_* env vars enable mem0 (no memory block in config.json),
    load_config SHALL still create extensions["mem0"] from the resolved env values.
    """
    agent_dir = tmp_path / "decafclaw"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({}))
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("MEMORY_PROVIDER", "mem0")
    monkeypatch.setenv("MEMORY_QDRANT_PATH", "/tmp/env-only-qdrant")

    with caplog.at_level(logging.WARNING, logger="decafclaw.config"):
        config = load_config()

    mem0 = config.extensions["mem0"]
    assert mem0.enabled is True
    assert mem0.type == "mem0"
    assert mem0.options["qdrant_path"] == "/tmp/env-only-qdrant"
    assert mem0.options["top_k"] == 5
    assert any("extensions.mem0" in rec.message for rec in caplog.records)
