"""Configuration loaded from environment variables / .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _parse_bool(value: str, default: bool = False) -> bool:
    """Parse a string to boolean. Returns default if empty/None."""
    if not value:
        return default
    return value.strip().lower() in ("true", "1", "yes")


@dataclass
class Config:
    # LLM settings
    llm_url: str = "http://192.168.0.199:4000/v1/chat/completions"
    llm_model: str = "gemini-2.5-flash"
    llm_api_key: str = "dummy"

    # Mattermost settings
    mattermost_url: str = ""  # e.g. "https://comms.lmorchard.com"
    mattermost_token: str = ""
    mattermost_bot_username: str = ""
    mattermost_ignore_bots: bool = True
    mattermost_ignore_webhooks: bool = False
    mattermost_debounce_ms: int = 1000  # batch messages within this window
    mattermost_cooldown_ms: int = 1000  # min time between agent turns per channel
    mattermost_require_mention: bool = True  # in public/private channels, only respond when @-mentioned
    mattermost_user_rate_limit_ms: int = 500  # min time between messages per user
    mattermost_channel_blocklist: str = ""  # comma-separated channel IDs to ignore
    mattermost_circuit_breaker_max: int = 10  # max agent turns per channel in window
    mattermost_circuit_breaker_window_sec: int = 30  # sliding window for circuit breaker
    mattermost_circuit_breaker_pause_sec: int = 60  # pause duration after breaker trips

    # Workspace settings
    data_home: str = "./data"
    agent_id: str = "decafclaw"
    agent_user_id: str = "user"  # single configured user (temporary)

    @property
    def agent_path(self) -> Path:
        """Admin-level agent directory (read-only to agent)."""
        return Path(self.data_home) / self.agent_id

    @property
    def workspace_path(self) -> Path:
        """Agent read/write sandbox."""
        return Path(self.data_home) / self.agent_id / "workspace"

    # Embedding / semantic search settings
    embedding_model: str = "text-embedding-004"
    embedding_url: str = ""      # default: falls back to llm_url
    embedding_api_key: str = ""  # default: falls back to llm_api_key
    memory_search_strategy: str = "substring"  # substring | semantic

    @property
    def effective_embedding_url(self) -> str:
        return self.embedding_url or self.llm_url.replace("/chat/completions", "/embeddings")

    @property
    def effective_embedding_api_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key

    # Tabstack settings
    tabstack_api_key: str = ""
    tabstack_api_url: str = ""  # empty = SDK default (production)

    # Compaction settings
    compaction_llm_url: str = ""        # default: falls back to llm_url
    compaction_llm_model: str = ""      # default: falls back to llm_model
    compaction_llm_api_key: str = ""    # default: falls back to llm_api_key
    compaction_max_tokens: int = 100000 # compact when prompt_tokens exceeds this
    compaction_llm_max_tokens: int = 0  # compaction LLM's context budget (0 = use compaction_max_tokens)
    compaction_preserve_turns: int = 5  # keep this many recent turns intact

    @property
    def compaction_url(self) -> str:
        return self.compaction_llm_url or self.llm_url

    @property
    def compaction_model(self) -> str:
        return self.compaction_llm_model or self.llm_model

    @property
    def compaction_api_key(self) -> str:
        return self.compaction_llm_api_key or self.llm_api_key

    @property
    def compaction_context_budget(self) -> int:
        return self.compaction_llm_max_tokens or self.compaction_max_tokens

    # Heartbeat settings
    heartbeat_interval: str = "30m"
    heartbeat_user: str = ""
    heartbeat_channel: str = ""
    heartbeat_suppress_ok: bool = True

    # Streaming settings
    llm_streaming: bool = True
    llm_stream_throttle_ms: int = 200

    # Agent settings (system_prompt is assembled from prompt files at startup)
    system_prompt: str = ""
    max_tool_iterations: int = 200
    max_concurrent_tools: int = 5    # max parallel tool calls per model response
    max_message_length: int = 50000  # truncate user messages beyond this (chars)

    # Child agent (delegation) settings
    child_max_tool_iterations: int = 10
    child_timeout_sec: int = 300

    # HTTP server settings
    http_enabled: bool = False
    http_host: str = "0.0.0.0"
    http_port: int = 18880
    http_secret: str = ""
    http_base_url: str = ""  # auto-detected if empty

    @property
    def http_callback_base(self) -> str:
        """Base URL for HTTP callbacks. Auto-detected from host/port if not set."""
        if self.http_base_url:
            return self.http_base_url.rstrip("/")
        return f"http://{self.http_host}:{self.http_port}"

    # Mattermost confirmation settings
    mattermost_enable_emoji_confirms: bool = True  # auto-set to False when http_enabled

    # Claude Code skill settings
    claude_code_model: str = ""  # empty = SDK default
    claude_code_budget_default: float = 2.0
    claude_code_budget_max: float = 10.0
    claude_code_session_timeout: str = "30m"

    # Discovered skills (populated by load_system_prompt at startup)
    discovered_skills: list = field(default_factory=list)


def load_config() -> Config:
    load_dotenv()
    return Config(
        llm_url=os.getenv("LLM_URL", Config.llm_url),
        llm_model=os.getenv("LLM_MODEL", Config.llm_model),
        llm_api_key=os.getenv("LLM_API_KEY", Config.llm_api_key),
        mattermost_url=os.getenv("MATTERMOST_URL", ""),
        mattermost_token=os.getenv("MATTERMOST_TOKEN", ""),
        mattermost_bot_username=os.getenv("MATTERMOST_BOT_USERNAME", ""),
        mattermost_ignore_bots=_parse_bool(os.getenv("MATTERMOST_IGNORE_BOTS", ""), default=True),
        mattermost_ignore_webhooks=_parse_bool(os.getenv("MATTERMOST_IGNORE_WEBHOOKS", ""), default=False),
        mattermost_debounce_ms=int(os.getenv("MATTERMOST_DEBOUNCE_MS", "1000")),
        mattermost_cooldown_ms=int(os.getenv("MATTERMOST_COOLDOWN_MS", "1000")),
        mattermost_require_mention=_parse_bool(os.getenv("MATTERMOST_REQUIRE_MENTION", ""), default=True),
        mattermost_user_rate_limit_ms=int(os.getenv("MATTERMOST_USER_RATE_LIMIT_MS", "500")),
        mattermost_channel_blocklist=os.getenv("MATTERMOST_CHANNEL_BLOCKLIST", ""),
        mattermost_circuit_breaker_max=int(os.getenv("MATTERMOST_CIRCUIT_BREAKER_MAX", "10")),
        mattermost_circuit_breaker_window_sec=int(os.getenv("MATTERMOST_CIRCUIT_BREAKER_WINDOW_SEC", "30")),
        mattermost_circuit_breaker_pause_sec=int(os.getenv("MATTERMOST_CIRCUIT_BREAKER_PAUSE_SEC", "60")),
        compaction_llm_url=os.getenv("COMPACTION_LLM_URL", ""),
        compaction_llm_model=os.getenv("COMPACTION_LLM_MODEL", ""),
        compaction_llm_api_key=os.getenv("COMPACTION_LLM_API_KEY", ""),
        compaction_max_tokens=int(os.getenv("COMPACTION_MAX_TOKENS", "100000")),
        compaction_llm_max_tokens=int(os.getenv("COMPACTION_LLM_MAX_TOKENS", "0")),
        compaction_preserve_turns=int(os.getenv("COMPACTION_PRESERVE_TURNS", "5")),
        embedding_model=os.getenv("EMBEDDING_MODEL", Config.embedding_model),
        embedding_url=os.getenv("EMBEDDING_URL", ""),
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
        memory_search_strategy=os.getenv("MEMORY_SEARCH_STRATEGY", Config.memory_search_strategy),
        data_home=os.getenv("DATA_HOME", Config.data_home),
        agent_id=os.getenv("AGENT_ID", Config.agent_id),
        agent_user_id=os.getenv("AGENT_USER_ID", Config.agent_user_id),
        tabstack_api_key=os.getenv("TABSTACK_API_KEY", ""),
        tabstack_api_url=os.getenv("TABSTACK_API_URL", ""),
        heartbeat_interval=os.getenv("HEARTBEAT_INTERVAL", Config.heartbeat_interval),
        heartbeat_user=os.getenv("HEARTBEAT_USER", ""),
        heartbeat_channel=os.getenv("HEARTBEAT_CHANNEL", ""),
        heartbeat_suppress_ok=_parse_bool(os.getenv("HEARTBEAT_SUPPRESS_OK", ""), default=True),
        llm_streaming=_parse_bool(os.getenv("LLM_STREAMING", ""), default=True),
        llm_stream_throttle_ms=int(os.getenv("LLM_STREAM_THROTTLE_MS", "200")),
        http_enabled=_parse_bool(os.getenv("HTTP_ENABLED", ""), default=False),
        http_host=os.getenv("HTTP_HOST", Config.http_host),
        http_port=int(os.getenv("HTTP_PORT", "18880")),
        http_secret=os.getenv("HTTP_SECRET", ""),
        http_base_url=os.getenv("HTTP_BASE_URL", ""),
        mattermost_enable_emoji_confirms=_parse_bool(
            os.getenv("MATTERMOST_ENABLE_EMOJI_CONFIRMS", ""),
            default=not _parse_bool(os.getenv("HTTP_ENABLED", ""), default=False)),
        claude_code_model=os.getenv("CLAUDE_CODE_MODEL", ""),
        claude_code_budget_default=float(os.getenv("CLAUDE_CODE_BUDGET_DEFAULT", "2.0")),
        claude_code_budget_max=float(os.getenv("CLAUDE_CODE_BUDGET_MAX", "10.0")),
        claude_code_session_timeout=os.getenv("CLAUDE_CODE_SESSION_TIMEOUT", "30m"),
        system_prompt=os.getenv("SYSTEM_PROMPT", Config.system_prompt),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "30")),
        max_concurrent_tools=int(os.getenv("MAX_CONCURRENT_TOOLS", "5")),
        max_message_length=int(os.getenv("MAX_MESSAGE_LENGTH", "50000")),
        child_max_tool_iterations=int(os.getenv("CHILD_MAX_TOOL_ITERATIONS", "10")),
        child_timeout_sec=int(os.getenv("CHILD_TIMEOUT_SEC", "300")),
    )
