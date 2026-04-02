"""Configuration loaded from config.json, environment variables, and defaults.

Resolution order (first non-empty wins):
  1. Environment variable (systematic name, then alias)
  2. Config file value (data/{agent_id}/config.json)
  3. Dataclass default
"""

import json
import logging
import os
from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any, get_origin

from dotenv import load_dotenv

from .config_types import (
    AgentConfig,
    CompactionConfig,
    EmbeddingConfig,
    HeartbeatConfig,
    HttpConfig,
    LlmConfig,
    MattermostConfig,
    MemoryContextConfig,
    ReflectionConfig,
    RelevanceConfig,
    VaultConfig,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_bool(value: str, default: bool = False) -> bool:
    """Parse a string to boolean. Returns default if empty/None."""
    if not value:
        return default
    return value.strip().lower() in ("true", "1", "yes")


def _parse_list(value: str) -> list[str]:
    """Parse env var to list. Try JSON first, fall back to comma-split."""
    value = value.strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except json.JSONDecodeError:
            pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _coerce(value: str, field_type) -> object:
    """Coerce a string value to the target field type."""
    origin = get_origin(field_type)
    if origin is list:
        return _parse_list(value)
    if field_type is bool:
        return _parse_bool(value)
    if field_type is int:
        return int(value)
    if field_type is float:
        return float(value)
    return value


# ---------------------------------------------------------------------------
# Generic sub-config loader
# ---------------------------------------------------------------------------

def load_sub_config(
    dc_class: type,
    json_data: dict,
    env_prefix: str,
    env_aliases: dict[str, str] | None = None,
) -> Any:
    """Build a sub-config dataclass from JSON data + env vars + defaults.

    For each field:
      1. Check env var {ENV_PREFIX}_{FIELD_UPPER} (skipped if prefix empty)
      2. Check env alias from field metadata or env_aliases dict
      3. Check json_data[field_name]
      4. Fall through to dataclass default
    """
    kwargs: dict[str, object] = {}
    aliases = env_aliases or {}

    # Resolve type hints (may be strings due to __future__.annotations)
    import typing
    hints = typing.get_type_hints(dc_class)

    for f in dc_fields(dc_class):
        field_type = hints.get(f.name, f.type)
        env_val = None

        # 1. Systematic env var: PREFIX_FIELDNAME
        if env_prefix:
            env_name = f"{env_prefix}_{f.name.upper()}"
            env_val = os.getenv(env_name)

        # 2. Env alias from field metadata or explicit aliases dict
        if not env_val:
            alias = f.metadata.get("env_alias") or aliases.get(f.name)
            if alias:
                env_val = os.getenv(alias)

        if env_val is not None and env_val != "":
            kwargs[f.name] = _coerce(env_val, field_type)
        elif f.name in json_data:
            json_val = json_data[f.name]
            # JSON already has correct types for most things
            if isinstance(json_val, str) and field_type not in (str, "str"):
                kwargs[f.name] = _coerce(json_val, field_type)
            else:
                kwargs[f.name] = json_val
        # else: use dataclass default

    return dc_class(**kwargs)


# ---------------------------------------------------------------------------
# Top-level Config
# ---------------------------------------------------------------------------

@dataclass
class Config:
    llm: LlmConfig = field(default_factory=LlmConfig)
    mattermost: MattermostConfig = field(default_factory=MattermostConfig)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, dict[str, str]] = field(default_factory=dict)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    memory_context: MemoryContextConfig = field(default_factory=MemoryContextConfig)
    relevance: RelevanceConfig = field(default_factory=RelevanceConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)

    # Custom environment variables from config.json "env" section
    env: dict[str, str] = field(default_factory=dict)

    # Runtime-only (not in config file)
    system_prompt: str = ""
    discovered_skills: list = field(default_factory=list)

    def apply_env(self) -> None:
        """Apply env vars from the config. Only sets vars not already in the environment.

        Priority: real env vars > .env file > config.json env section.
        Call at startup and after config reload.
        """
        for key, value in self.env.items():
            if key not in os.environ:
                os.environ[key] = value

    @property
    def agent_path(self) -> Path:
        """Admin-level agent directory (read-only to agent)."""
        return Path(self.agent.data_home) / self.agent.id

    @property
    def workspace_path(self) -> Path:
        """Agent read/write sandbox."""
        return self.agent_path / "workspace"

    @property
    def http_callback_base(self) -> str:
        """Base URL for HTTP callbacks. Auto-detected from host/port if not set."""
        if self.http.base_url:
            return self.http.base_url.rstrip("/")
        return f"http://{self.http.host}:{self.http.port}"

    @property
    def vault_root(self) -> Path:
        """Vault root directory (configurable, default workspace/vault/)."""
        p = Path(self.vault.vault_path)
        return p if p.is_absolute() else self.agent_path / p

    @property
    def vault_agent_dir(self) -> Path:
        """Agent's home folder within the vault."""
        p = Path(self.vault.agent_folder)
        return p if p.is_absolute() else self.vault_root / p

    @property
    def vault_agent_pages_dir(self) -> Path:
        """Agent's curated wiki pages directory."""
        return self.vault_agent_dir / "pages"

    @property
    def vault_agent_journal_dir(self) -> Path:
        """Agent's daily journal directory."""
        return self.vault_agent_dir / "journal"

    @property
    def tool_context_budget(self) -> int:
        return int(self.compaction.max_tokens * self.agent.tool_context_budget_pct)

    @property
    def compaction_context_budget(self) -> int:
        return self.compaction.llm_max_tokens or self.compaction.max_tokens



# ---------------------------------------------------------------------------
# Effort level resolution
# ---------------------------------------------------------------------------

EFFORT_LEVELS = {"fast", "default", "strong"}


def resolve_effort(config: Config, level: str) -> LlmConfig:
    """Resolve an effort level to a concrete LLM config.

    Merges config.models[level] over config.llm defaults.
    Unknown levels, absent models section, or invalid entries fall back to config.llm.
    """
    entry = config.models.get(level, {})
    if not entry or not isinstance(entry, dict):
        if entry and not isinstance(entry, dict):
            log.warning("Invalid models entry for '%s': expected dict, got %s",
                        level, type(entry).__name__)
        return config.llm
    return replace(
        config.llm,
        model=entry.get("model") or config.llm.model,
        url=entry.get("url") or config.llm.url,
        api_key=entry.get("api_key") or config.llm.api_key,
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_config() -> Config:
    """Load config from defaults → config.json → env vars."""
    load_dotenv()

    # Bootstrap: resolve data_home and agent_id from env only
    data_home = os.getenv("DATA_HOME", AgentConfig.data_home)
    agent_id = os.getenv("AGENT_ID", AgentConfig.id)

    # Load config file if it exists
    config_path = Path(data_home) / agent_id / "config.json"
    file_data: dict = {}
    if config_path.exists():
        try:
            file_data = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load %s: %s", config_path, exc)

    # Build each sub-config
    llm = load_sub_config(
        LlmConfig, file_data.get("llm", {}), "LLM")

    mattermost = load_sub_config(
        MattermostConfig, file_data.get("mattermost", {}), "MATTERMOST",
        env_aliases={"stream_throttle_ms": "LLM_STREAM_THROTTLE_MS"})

    compaction = load_sub_config(
        CompactionConfig, file_data.get("compaction", {}), "COMPACTION",
        env_aliases={
            "url": "COMPACTION_LLM_URL",
            "model": "COMPACTION_LLM_MODEL",
            "api_key": "COMPACTION_LLM_API_KEY",
            "llm_max_tokens": "COMPACTION_LLM_MAX_TOKENS",
        })

    embedding = load_sub_config(
        EmbeddingConfig, file_data.get("embedding", {}), "EMBEDDING",
        env_aliases={"search_strategy": "MEMORY_SEARCH_STRATEGY"})

    heartbeat = load_sub_config(
        HeartbeatConfig, file_data.get("heartbeat", {}), "HEARTBEAT")

    http = load_sub_config(
        HttpConfig, file_data.get("http", {}), "HTTP")

    agent = load_sub_config(
        AgentConfig, file_data.get("agent", {}), "",
        env_aliases={
            "data_home": "DATA_HOME",
            "id": "AGENT_ID",
            "user_id": "AGENT_USER_ID",
            "max_tool_iterations": "MAX_TOOL_ITERATIONS",
            "max_concurrent_tools": "MAX_CONCURRENT_TOOLS",
            "max_message_length": "MAX_MESSAGE_LENGTH",
            "tool_context_budget_pct": "TOOL_CONTEXT_BUDGET_PCT",
            "always_loaded_tools": "ALWAYS_LOADED_TOOLS",
            "child_max_tool_iterations": "CHILD_MAX_TOOL_ITERATIONS",
            "child_timeout_sec": "CHILD_TIMEOUT_SEC",
            "turn_on_new_message": "AGENT_TURN_ON_NEW_MESSAGE",
        })

    # Force bootstrap values — these determine the config file location,
    # so the JSON file must not override them
    agent.data_home = data_home
    agent.id = agent_id

    # Skills — raw dict, resolved per-skill at activation time
    raw_skills = file_data.get("skills", {})
    if not isinstance(raw_skills, dict):
        log.warning(
            "Invalid 'skills' section in config.json: expected an object, "
            "got %s; defaulting to empty dict.",
            type(raw_skills).__name__,
        )
        skills: dict[str, dict[str, Any]] = {}
    else:
        skills = raw_skills

    # Models — effort level to LLM config mapping, raw dict
    raw_models = file_data.get("models", {})
    if not isinstance(raw_models, dict):
        log.warning(
            "Invalid 'models' section in config.json: expected an object, "
            "got %s; defaulting to empty dict.",
            type(raw_models).__name__,
        )
        models: dict[str, dict[str, str]] = {}
    else:
        models = raw_models

    reflection = load_sub_config(
        ReflectionConfig, file_data.get("reflection", {}), "REFLECTION")

    memory_context = load_sub_config(
        MemoryContextConfig, file_data.get("memory_context", {}), "MEMORY_CONTEXT")

    relevance = load_sub_config(
        RelevanceConfig, file_data.get("relevance", {}), "RELEVANCE")

    vault = load_sub_config(
        VaultConfig, file_data.get("vault", {}), "VAULT")

    # Custom env vars from config file
    env_vars: dict[str, str] = {
        str(k): str(v) for k, v in file_data.get("env", {}).items()
    }

    # Runtime-only field
    system_prompt = os.getenv("SYSTEM_PROMPT", "")

    config = Config(
        llm=llm,
        mattermost=mattermost,
        compaction=compaction,
        embedding=embedding,
        heartbeat=heartbeat,
        http=http,
        agent=agent,
        skills=skills,
        models=models,
        reflection=reflection,
        memory_context=memory_context,
        relevance=relevance,
        vault=vault,
        env=env_vars,
        system_prompt=system_prompt,
    )

    # Apply custom env vars (only sets those not already in environment)
    config.apply_env()

    return config


def reload_env(config: Config) -> dict[str, str]:
    """Re-read the env section from config.json and apply new vars.

    Returns dict of newly applied vars (name → value).
    """
    config_path = config.agent_path / "config.json"
    if not config_path.exists():
        return {}
    try:
        file_data = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    new_env = {str(k): str(v) for k, v in file_data.get("env", {}).items()}
    applied: dict[str, str] = {}
    for key, value in new_env.items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    config.env = new_env
    return applied
