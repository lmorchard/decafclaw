"""Tests for the config loader (JSON file + env vars + defaults)."""

import json
import os

import pytest

from decafclaw.config import Config, load_config
from decafclaw.config_types import (
    AgentConfig,
    CompactionConfig,
    EmbeddingConfig,
    LlmConfig,
    MattermostConfig,
    is_secret,
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Prevent .env file from leaking into tests."""
    monkeypatch.setattr("decafclaw.config.load_dotenv", lambda **kw: None)
    # Clear common env vars that .env may have set
    for key in list(os.environ):
        if any(key.startswith(p) for p in (
            "LLM_", "MATTERMOST_", "COMPACTION_", "EMBEDDING_",
            "HEARTBEAT_", "HTTP_", "TABSTACK_", "CLAUDE_CODE_",
            "SKILLS_", "MEMORY_SEARCH", "SYSTEM_PROMPT",
            "NOTIFICATIONS_", "EMAIL_",
        )):
            monkeypatch.delenv(key, raising=False)


class TestDefaults:
    def test_default_config(self):
        """Config() with no args gives expected defaults."""
        c = Config()
        assert c.llm.url == "http://192.168.0.199:4000/v1/chat/completions"
        assert c.llm.model == "gemini-2.5-flash"
        assert c.llm.streaming is True
        assert c.mattermost.url == ""
        assert c.mattermost.channel_blocklist == []
        assert c.agent.data_home == "./data"
        assert c.agent.id == "decafclaw"
        assert c.agent.critical_tools == []
        assert c.agent.preemptive_search.enabled is True
        assert c.agent.preemptive_search.max_matches == 10
        assert c.compaction.max_tokens == 100000
        assert c.embedding.search_strategy == "substring"

    def test_notifications_defaults(self):
        c = Config()
        assert c.notifications.retention_days == 30
        assert c.notifications.poll_interval_sec == 30
        # Channel adapters default to disabled with no recipient.
        assert c.notifications.channels.mattermost_dm.enabled is False
        assert c.notifications.channels.mattermost_dm.recipient_username == ""
        assert c.notifications.channels.mattermost_dm.min_priority == "high"
        # Email channel has its own list of recipients as the trust boundary.
        assert c.notifications.channels.email.enabled is False
        assert c.notifications.channels.email.recipient_addresses == []
        assert c.notifications.channels.email.min_priority == "high"

    def test_email_defaults(self):
        c = Config()
        assert c.email.enabled is False
        assert c.email.smtp_host == ""
        assert c.email.smtp_port == 587
        assert c.email.use_tls is True
        assert c.email.sender_address == ""
        assert c.email.allowed_recipients == []
        assert c.email.max_attachment_bytes == 10 * 1024 * 1024

    def test_derived_properties(self):
        c = Config(agent=AgentConfig(data_home="/tmp/test", id="mybot"))
        assert str(c.agent_path) == "/tmp/test/mybot"
        assert str(c.workspace_path) == "/tmp/test/mybot/workspace"

    def test_tool_context_budget(self):
        c = Config(
            compaction=CompactionConfig(max_tokens=100000),
            agent=AgentConfig(tool_context_budget_pct=0.10),
        )
        assert c.tool_context_budget == 10000

    def test_compaction_context_budget(self):
        c = Config(compaction=CompactionConfig(max_tokens=100000, llm_max_tokens=50000))
        assert c.compaction_context_budget == 50000

    def test_compaction_context_budget_fallback(self):
        c = Config(compaction=CompactionConfig(max_tokens=100000, llm_max_tokens=0))
        assert c.compaction_context_budget == 100000


class TestJsonFileLoading:
    def test_loads_from_json(self, tmp_path, monkeypatch):
        """Config file values are loaded."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "llm": {"model": "test-model"},
            "mattermost": {"url": "https://mm.test.com"},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        # Clear env vars that .env may have set so JSON values win
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("MATTERMOST_URL", raising=False)
        c = load_config()
        assert c.llm.model == "test-model"
        assert c.mattermost.url == "https://mm.test.com"

    def test_loads_nested_dataclass(self, tmp_path, monkeypatch):
        """Nested dataclass fields (e.g. agent.preemptive_search) load from JSON."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "agent": {
                "preemptive_search": {"enabled": False, "max_matches": 5},
            },
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        assert c.agent.preemptive_search.enabled is False
        assert c.agent.preemptive_search.max_matches == 5

    def test_loads_notifications_from_json(self, tmp_path, monkeypatch):
        """NotificationsConfig fields load from JSON."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "notifications": {"retention_days": 7, "poll_interval_sec": 60},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        assert c.notifications.retention_days == 7
        assert c.notifications.poll_interval_sec == 60

    def test_loads_email_from_json(self, tmp_path, monkeypatch):
        """EmailConfig loads from JSON."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "email": {
                "enabled": True,
                "smtp_host": "smtp.fastmail.com",
                "smtp_port": 587,
                "smtp_username": "bot@example.com",
                "smtp_password": "app-password",
                "sender_address": "bot@example.com",
                "allowed_recipients": ["admin@example.com", "@team.example.com"],
                "max_attachment_bytes": 5000000,
            },
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        assert c.email.enabled is True
        assert c.email.smtp_host == "smtp.fastmail.com"
        assert c.email.smtp_port == 587
        assert c.email.sender_address == "bot@example.com"
        assert c.email.allowed_recipients == [
            "admin@example.com", "@team.example.com",
        ]
        assert c.email.max_attachment_bytes == 5000000

    def test_loads_email_channel_from_json(self, tmp_path, monkeypatch):
        """EmailChannelConfig loads via nested channels recursion."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "notifications": {
                "channels": {
                    "email": {
                        "enabled": True,
                        "recipient_addresses": ["ops@example.com"],
                        "min_priority": "normal",
                    },
                },
            },
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        email_ch = c.notifications.channels.email
        assert email_ch.enabled is True
        assert email_ch.recipient_addresses == ["ops@example.com"]
        assert email_ch.min_priority == "normal"

    def test_loads_nested_channels_from_json(self, tmp_path, monkeypatch):
        """Nested channel config loads via the recursion in load_sub_config."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "notifications": {
                "channels": {
                    "mattermost_dm": {
                        "enabled": True,
                        "recipient_username": "les",
                        "min_priority": "normal",
                    },
                },
            },
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        assert c.notifications.channels.mattermost_dm.enabled is True
        assert c.notifications.channels.mattermost_dm.recipient_username == "les"
        assert c.notifications.channels.mattermost_dm.min_priority == "normal"

    def test_missing_file_uses_defaults(self, tmp_path, monkeypatch):
        """Missing config file gracefully falls back to defaults."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.delenv("LLM_MODEL", raising=False)
        c = load_config()
        assert c.llm.model == "gemini-2.5-flash"


class TestEnvVarOverride:
    def test_env_overrides_file(self, tmp_path, monkeypatch):
        """Env vars take priority over config file."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        config_file = agent_dir / "config.json"
        config_file.write_text(json.dumps({
            "llm": {"model": "from-file"},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.setenv("LLM_MODEL", "from-env")
        c = load_config()
        assert c.llm.model == "from-env"


class TestListFields:
    def test_comma_separated(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.setenv("MATTERMOST_CHANNEL_BLOCKLIST", "a,b,c")
        c = load_config()
        assert c.mattermost.channel_blocklist == ["a", "b", "c"]

    def test_json_array(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.setenv("MATTERMOST_CHANNEL_BLOCKLIST", '["x","y"]')
        c = load_config()
        assert c.mattermost.channel_blocklist == ["x", "y"]

    def test_empty_string(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.setenv("MATTERMOST_CHANNEL_BLOCKLIST", "")
        c = load_config()
        assert c.mattermost.channel_blocklist == []

    def test_list_from_json_file(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        (agent_dir / "config.json").write_text(json.dumps({
            "mattermost": {"channel_blocklist": ["id1", "id2"]},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.delenv("MATTERMOST_CHANNEL_BLOCKLIST", raising=False)
        c = load_config()
        assert c.mattermost.channel_blocklist == ["id1", "id2"]


class TestFallbackResolution:
    def test_compaction_resolved(self):
        """CompactionConfig.resolved() fills from llm."""
        c = Config(
            llm=LlmConfig(url="http://llm", model="llm-model", api_key="llm-key"),
            compaction=CompactionConfig(),  # all empty
        )
        cc = c.compaction.resolved(c)
        assert cc.url == "http://llm"
        assert cc.model == "llm-model"
        assert cc.api_key == "llm-key"

    def test_compaction_override(self):
        """Explicit compaction values are preserved."""
        c = Config(
            llm=LlmConfig(url="http://llm", model="llm-model"),
            compaction=CompactionConfig(url="http://compact", model="compact-model"),
        )
        cc = c.compaction.resolved(c)
        assert cc.url == "http://compact"
        assert cc.model == "compact-model"

    def test_embedding_resolved(self):
        """EmbeddingConfig.resolved() derives URL from llm."""
        c = Config(
            llm=LlmConfig(
                url="http://llm/v1/chat/completions",
                api_key="llm-key",
            ),
            embedding=EmbeddingConfig(),
        )
        ec = c.embedding.resolved(c)
        assert ec.url == "http://llm/v1/embeddings"
        assert ec.api_key == "llm-key"

    def test_embedding_override(self):
        c = Config(
            llm=LlmConfig(url="http://llm/v1/chat/completions"),
            embedding=EmbeddingConfig(url="http://custom-embed"),
        )
        ec = c.embedding.resolved(c)
        assert ec.url == "http://custom-embed"


class TestBootstrapOrder:
    def test_data_home_from_env_not_file(self, tmp_path, monkeypatch):
        """data_home comes from env, not from config file."""
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        (agent_dir / "config.json").write_text(json.dumps({
            "agent": {"data_home": "/should/be/ignored"},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        c = load_config()
        # The file's data_home is ignored because DATA_HOME env var wins
        # (and DATA_HOME is checked first during bootstrap)
        assert c.agent.data_home == str(tmp_path)


class TestSecretMetadata:
    def test_llm_api_key_is_secret(self):
        assert is_secret(LlmConfig, "api_key") is True

    def test_llm_model_not_secret(self):
        assert is_secret(LlmConfig, "model") is False

    def test_mattermost_token_is_secret(self):
        assert is_secret(MattermostConfig, "token") is True

    def test_compaction_api_key_is_secret(self):
        assert is_secret(CompactionConfig, "api_key") is True

    def test_embedding_api_key_is_secret(self):
        assert is_secret(EmbeddingConfig, "api_key") is True


class TestEnvSection:
    def test_env_loaded_from_json(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        (agent_dir / "config.json").write_text(json.dumps({
            "env": {"MY_CUSTOM_VAR": "hello", "ANOTHER": "world"},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.delenv("MY_CUSTOM_VAR", raising=False)
        monkeypatch.delenv("ANOTHER", raising=False)
        c = load_config()
        assert c.env == {"MY_CUSTOM_VAR": "hello", "ANOTHER": "world"}
        assert os.environ["MY_CUSTOM_VAR"] == "hello"
        assert os.environ["ANOTHER"] == "world"

    def test_env_does_not_override_existing(self, tmp_path, monkeypatch):
        agent_dir = tmp_path / "decafclaw"
        agent_dir.mkdir()
        (agent_dir / "config.json").write_text(json.dumps({
            "env": {"EXISTING_VAR": "from-config"},
        }))
        monkeypatch.setenv("DATA_HOME", str(tmp_path))
        monkeypatch.setenv("EXISTING_VAR", "from-env")
        load_config()
        assert os.environ["EXISTING_VAR"] == "from-env"

    def test_apply_env_idempotent(self):
        c = Config(env={"TEST_APPLY": "val"})
        os.environ.pop("TEST_APPLY", None)
        c.apply_env()
        assert os.environ["TEST_APPLY"] == "val"
        # Second call shouldn't fail
        c.apply_env()
        assert os.environ["TEST_APPLY"] == "val"
        os.environ.pop("TEST_APPLY", None)


class TestCompatRemoved:
    def test_old_flat_names_raise(self):
        """Old flat names should raise AttributeError."""
        c = Config()
        with pytest.raises(AttributeError):
            _ = c.mattermost_url
        with pytest.raises(AttributeError):
            _ = c.llm_model
        with pytest.raises(AttributeError):
            _ = c.data_home
