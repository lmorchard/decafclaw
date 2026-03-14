"""Configuration loaded from environment variables / .env file."""

import os
from dataclasses import dataclass, field
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

    # Tabstack settings
    tabstack_api_key: str = ""
    tabstack_api_url: str = ""  # empty = SDK default (production)

    # Agent settings
    system_prompt: str = "You are a helpful assistant. You have access to tools you can use to help answer questions."
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
        tabstack_api_key=os.getenv("TABSTACK_API_KEY", ""),
        tabstack_api_url=os.getenv("TABSTACK_API_URL", ""),
        system_prompt=os.getenv("SYSTEM_PROMPT", Config.system_prompt),
        max_tool_iterations=int(os.getenv("MAX_TOOL_ITERATIONS", "10")),
    )
