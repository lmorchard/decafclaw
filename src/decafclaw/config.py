"""Configuration loaded from environment variables / .env file."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv


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
    def workspace_path(self) -> Path:
        return Path(self.data_home) / "workspace" / self.agent_id

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

    # Agent settings
    system_prompt: str = (
        "You are DecafClaw, a minimal AI agent built in Python as a learning project. "
        "You connect to Mattermost as a chat bot and have access to tools for web research, "
        "file operations, and persistent memory. Your underlying LLM may vary (Gemini, etc.) "
        "but your identity is DecafClaw — when asked what you are, describe yourself as "
        "DecafClaw, not as the underlying model.\n\n"
        "You have a persistent memory system, distinct from your training data, for storing "
        "context specific to this user and project. This includes user preferences, project "
        "details, and information about your own role and implementation within this project. "
        "At the start of each conversation, use memory_search or memory_recent to recall "
        "relevant context. When you learn something worth remembering, use memory_save. "
        "When asked about your own capabilities or how you operate, search memory for "
        "project-specific context before relying on general knowledge.\n\n"
        "When asked about preferences, prior conversations, or personal details, you MUST "
        "check memory before saying you don't know. For broad questions like 'what do you "
        "know about me', use memory_recent first. For specific topics, use memory_search. "
        "NEVER say you have no information without checking memory first. When searching, "
        "if an initial query does not "
        "yield results, immediately try variations: synonyms, related terms, singular/plural, "
        "and broader categories. Do not conclude information is absent after a single failed "
        "attempt — exhaust reasonable search variations before informing the user.\n\n"
        "When a tool returns results, use them in your response — do not ignore valid "
        "results. If a tool returns an error or is unavailable, try a different tool "
        "or answer from your own knowledge. NEVER say 'tools are unavailable' — instead "
        "either present what you found or explain what you couldn't find specifically."
    )
    max_tool_iterations: int = 10


def load_config() -> Config:
    load_dotenv()
    return Config(
        llm_url=os.getenv("LLM_URL", Config.llm_url),
        llm_model=os.getenv("LLM_MODEL", Config.llm_model),
        llm_api_key=os.getenv("LLM_API_KEY", Config.llm_api_key),
        mattermost_url=os.getenv("MATTERMOST_URL", ""),
        mattermost_token=os.getenv("MATTERMOST_TOKEN", ""),
        mattermost_bot_username=os.getenv("MATTERMOST_BOT_USERNAME", ""),
        mattermost_ignore_bots=os.getenv("MATTERMOST_IGNORE_BOTS", "true").lower() == "true",
        mattermost_ignore_webhooks=os.getenv("MATTERMOST_IGNORE_WEBHOOKS", "false").lower() == "true",
        mattermost_debounce_ms=int(os.getenv("MATTERMOST_DEBOUNCE_MS", "1000")),
        mattermost_cooldown_ms=int(os.getenv("MATTERMOST_COOLDOWN_MS", "1000")),
        mattermost_require_mention=os.getenv("MATTERMOST_REQUIRE_MENTION", "true").lower() == "true",
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
        system_prompt=os.getenv("SYSTEM_PROMPT", Config.system_prompt),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "10")),
    )
