"""Configuration sub-dataclasses grouped by concern.

Each dataclass represents a config group (llm, mattermost, etc.).
Field metadata supports:
  - secret: True — masked in `config show` unless --reveal
  - env_alias: "NAME" — alternative env var (checked after systematic name)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields


@dataclass
class LlmConfig:
    url: str = "http://192.168.0.199:4000/v1/chat/completions"
    model: str = "gemini-2.5-flash"
    api_key: str = field(default="dummy", metadata={"secret": True})
    streaming: bool = True
    context_window_size: int = 0  # 0 = not specified, fall back to compaction_max_tokens


@dataclass
class MattermostConfig:
    url: str = ""
    token: str = field(default="", metadata={"secret": True})
    bot_username: str = ""
    ignore_bots: bool = True
    ignore_webhooks: bool = False
    debounce_ms: int = 1000
    cooldown_ms: int = 1000
    require_mention: bool = True
    user_rate_limit_ms: int = 500
    channel_blocklist: list[str] = field(default_factory=list)
    circuit_breaker_max: int = 10
    circuit_breaker_window_sec: int = 30
    circuit_breaker_pause_sec: int = 60
    enable_emoji_confirms: bool = True
    stream_throttle_ms: int = 200


@dataclass
class CompactionConfig:
    url: str = ""       # empty = resolve from llm via resolved()
    model: str = ""     # empty = resolve from llm via resolved()
    api_key: str = field(default="", metadata={"secret": True})
    max_tokens: int = 100000
    llm_max_tokens: int = 0  # 0 = use max_tokens
    preserve_turns: int = 5
    memory_sweep_enabled: bool = True

    def resolved(self, config) -> CompactionConfig:
        """Return copy with empty fields filled from config.llm."""
        return replace(self,
            url=self.url or config.llm.url,
            model=self.model or config.llm.model,
            api_key=self.api_key or config.llm.api_key,
        )


@dataclass
class EmbeddingConfig:
    model: str = "text-embedding-004"
    url: str = ""       # empty = resolve from llm via resolved()
    api_key: str = field(default="", metadata={"secret": True})
    search_strategy: str = "substring"
    dimensions: int = 768

    def resolved(self, config) -> EmbeddingConfig:
        """Return copy with empty fields filled from config.llm."""
        return replace(self,
            url=self.url or config.llm.url.replace(
                "/chat/completions", "/embeddings"),
            api_key=self.api_key or config.llm.api_key,
        )


@dataclass
class HeartbeatConfig:
    interval: str = "30m"
    user: str = ""
    channel: str = ""
    suppress_ok: bool = True


@dataclass
class HttpConfig:
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 18880
    secret: str = field(default="", metadata={"secret": True})
    base_url: str = ""
    max_upload_bytes: int = 100 * 1024 * 1024  # 100 MB


@dataclass
class AgentConfig:
    data_home: str = "./data"
    id: str = "decafclaw"
    user_id: str = "user"
    max_tool_iterations: int = 200
    max_concurrent_tools: int = 5
    max_message_length: int = 50000
    tool_context_budget_pct: float = 0.10
    always_loaded_tools: list[str] = field(default_factory=list)
    child_max_tool_iterations: int = 10
    child_timeout_sec: int = 300
    turn_on_new_message: str = "queue"  # "queue" or "cancel"
    show_context_status: bool = True


@dataclass
class ReflectionConfig:
    enabled: bool = True
    url: str = ""       # empty = resolve from llm
    model: str = ""     # empty = resolve from llm
    api_key: str = field(default="", metadata={"secret": True})
    max_retries: int = 2
    visibility: str = "hidden"  # hidden | visible | debug
    max_tool_result_len: int = 2000  # max chars per tool result shown to judge

    def resolved(self, config) -> ReflectionConfig:
        """Return copy with empty url/model/api_key filled from config.llm."""
        return replace(self,
            url=self.url or config.llm.url,
            model=self.model or config.llm.model,
            api_key=self.api_key or config.llm.api_key,
        )



@dataclass
class VaultConfig:
    vault_path: str = "workspace/vault/"
    agent_folder: str = "agent/"


@dataclass
class VaultRetrievalConfig:
    enabled: bool = True
    similarity_threshold: float = 0.3
    max_results: int = 5
    max_tokens: int = 500
    show_in_ui: bool = True


@dataclass
class RelevanceConfig:
    w_similarity: float = 0.5
    w_recency: float = 0.3
    w_importance: float = 0.2
    recency_decay_rate: float = 0.99  # per-hour exponential decay
    min_composite_score: float = 0.65  # candidates below this are dropped
    graph_expansion_enabled: bool = True
    graph_expansion_similarity_discount: float = 0.7


def is_secret(dc_class: type, field_name: str) -> bool:
    """Check if a dataclass field is marked as secret."""
    for f in dc_fields(dc_class):
        if f.name == field_name:
            return f.metadata.get("secret", False)
    return False


def get_env_alias(dc_class: type, field_name: str) -> str | None:
    """Get the env var alias for a dataclass field, if any."""
    for f in dc_fields(dc_class):
        if f.name == field_name:
            return f.metadata.get("env_alias")
    return None
