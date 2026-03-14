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

    # Tabstack settings
    tabstack_api_key: str = ""
    tabstack_api_url: str = ""  # empty = SDK default (production)

    # Agent settings
    system_prompt: str = (
        "You are a helpful assistant. You have access to tools you can use to help answer questions.\n\n"
        "You have a persistent memory system, distinct from your training data, for storing "
        "context specific to this user and project. This includes user preferences, project "
        "details, and information about your own role and implementation within this project. "
        "At the start of each conversation, use memory_search or memory_recent to recall "
        "relevant context. When you learn something worth remembering, use memory_save. "
        "When asked about your own capabilities or how you operate, search memory for "
        "project-specific context before relying on general knowledge.\n\n"
        "When using tools for information retrieval (memory_search, tabstack_research, etc.), "
        "if an initial query does not yield satisfactory results, immediately attempt broader "
        "or alternative queries following the tool's documented search strategies. Do not "
        "conclude information is absent after a single failed attempt — exhaust reasonable "
        "search variations before informing the user."
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
        data_home=os.getenv("DATA_HOME", Config.data_home),
        agent_id=os.getenv("AGENT_ID", Config.agent_id),
        agent_user_id=os.getenv("AGENT_USER_ID", Config.agent_user_id),
        tabstack_api_key=os.getenv("TABSTACK_API_KEY", ""),
        tabstack_api_url=os.getenv("TABSTACK_API_URL", ""),
        system_prompt=os.getenv("SYSTEM_PROMPT", Config.system_prompt),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "10")),
    )
