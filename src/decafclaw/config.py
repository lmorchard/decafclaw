"""Configuration loaded from config.json, environment variables, and defaults.

Resolution order (first non-empty wins):
  1. Environment variable (systematic name, then alias)
  2. Config file value (data/{agent_id}/config.json)
  3. Dataclass default
"""

import json
import logging
import os
from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any, get_origin

from dotenv import load_dotenv

from .config_types import (
    AgentConfig,
    BackgroundConfig,
    CleanupConfig,
    CompactionConfig,
    EmailConfig,
    EmbeddingConfig,
    HeartbeatConfig,
    HttpConfig,
    LlmConfig,
    MattermostConfig,
    ModelConfig,
    NotificationsConfig,
    ProviderConfig,
    ReflectionConfig,
    RelevanceConfig,
    VaultConfig,
    VaultRetrievalConfig,
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
            # Nested dataclass: recurse to build it from a dict, preserving
            # systematic env var lookup via a derived prefix.
            if (hasattr(field_type, "__dataclass_fields__")
                    and isinstance(json_val, dict)):
                nested_env_prefix = (
                    f"{env_prefix}_{f.name.upper()}" if env_prefix else ""
                )
                kwargs[f.name] = load_sub_config(
                    field_type, json_val, nested_env_prefix,
                )
            # JSON already has correct types for most things
            elif isinstance(json_val, str) and field_type not in (str, "str"):
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
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    heartbeat: HeartbeatConfig = field(default_factory=HeartbeatConfig)
    http: HttpConfig = field(default_factory=HttpConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    skills: dict[str, dict[str, Any]] = field(default_factory=dict)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    model_configs: dict[str, ModelConfig] = field(default_factory=dict)
    default_model: str = ""
    vault_retrieval: VaultRetrievalConfig = field(default_factory=VaultRetrievalConfig)
    relevance: RelevanceConfig = field(default_factory=RelevanceConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    background: BackgroundConfig = field(default_factory=BackgroundConfig)

    # Custom environment variables from config.json "env" section
    env: dict[str, str] = field(default_factory=dict)

    # Runtime-only (not in config file)
    system_prompt: str = ""
    discovered_skills: list = field(default_factory=list)
    always_loaded_skill_tools: set[str] = field(default_factory=set)

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

# ---------------------------------------------------------------------------
# Model resolution (new provider-based system)
# ---------------------------------------------------------------------------

def resolve_model(
    config: Config, name: str = "",
) -> tuple[ProviderConfig, ModelConfig]:
    """Resolve a named model config to its provider + model config.

    Falls back to config.default_model if name is empty.
    Raises KeyError if the model or its provider isn't found.
    """
    model_name = name or config.default_model
    if not model_name:
        raise KeyError("No model name given and no default_model configured")

    if model_name not in config.model_configs:
        available = ", ".join(sorted(config.model_configs.keys())) or "(none)"
        raise KeyError(
            f"Unknown model config '{model_name}'. Available: {available}"
        )

    mc = config.model_configs[model_name]
    if mc.provider not in config.providers:
        available = ", ".join(sorted(config.providers.keys())) or "(none)"
        raise KeyError(
            f"Model '{model_name}' references unknown provider '{mc.provider}'. "
            f"Available: {available}"
        )

    return config.providers[mc.provider], mc


def resolve_streaming(config: "Config", active_model: str = "") -> bool:
    """Resolve whether streaming is enabled for the given model.

    Checks the active model config first, then default_model, then config.llm.
    """
    name = active_model or config.default_model
    if name and name in config.model_configs:
        return config.model_configs[name].streaming
    return config.llm.streaming


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _load_providers(raw: dict) -> dict[str, ProviderConfig]:
    """Parse providers section from config.json into ProviderConfig instances."""
    if not isinstance(raw, dict):
        log.warning("Invalid 'providers' section: expected object, got %s",
                    type(raw).__name__)
        return {}
    result: dict[str, ProviderConfig] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            log.warning("Invalid provider '%s': expected object, got %s",
                        name, type(entry).__name__)
            continue
        result[name] = ProviderConfig(
            type=entry.get("type", ""),
            api_key=entry.get("api_key", ""),
            url=entry.get("url", ""),
            project=entry.get("project", ""),
            region=entry.get("region", ""),
            service_account_file=entry.get("service_account_file", ""),
        )
    return result


def _load_model_configs(raw: dict) -> dict[str, ModelConfig]:
    """Parse model_configs section from config.json into ModelConfig instances."""
    if not isinstance(raw, dict):
        log.warning("Invalid 'model_configs' section: expected object, got %s",
                    type(raw).__name__)
        return {}
    result: dict[str, ModelConfig] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            log.warning("Invalid model config '%s': expected object, got %s",
                        name, type(entry).__name__)
            continue
        result[name] = ModelConfig(
            provider=entry.get("provider", ""),
            model=entry.get("model", ""),
            context_window_size=entry.get("context_window_size", 0),
            timeout=entry.get("timeout", 300),
            streaming=entry.get("streaming", True),
        )
    return result


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

    cleanup = load_sub_config(
        CleanupConfig, file_data.get("cleanup", {}), "CLEANUP")

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
            "max_active_tools": "MAX_ACTIVE_TOOLS",
            "critical_tools": "CRITICAL_TOOLS",
            "child_max_tool_iterations": "CHILD_MAX_TOOL_ITERATIONS",
            "child_timeout_sec": "CHILD_TIMEOUT_SEC",
            "tool_timeout_sec": "TOOL_TIMEOUT_SEC",
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

    reflection = load_sub_config(
        ReflectionConfig, file_data.get("reflection", {}), "REFLECTION")

    vault_retrieval = load_sub_config(
        VaultRetrievalConfig, file_data.get("vault_retrieval", {}), "MEMORY_CONTEXT")

    relevance = load_sub_config(
        RelevanceConfig, file_data.get("relevance", {}), "RELEVANCE")

    vault = load_sub_config(
        VaultConfig, file_data.get("vault", {}), "VAULT")

    notifications = load_sub_config(
        NotificationsConfig, file_data.get("notifications", {}), "NOTIFICATIONS")

    email = load_sub_config(
        EmailConfig, file_data.get("email", {}), "EMAIL")

    background = load_sub_config(
        BackgroundConfig, file_data.get("background", {}), "BACKGROUND")

    # Providers — named provider connection configs
    providers = _load_providers(file_data.get("providers", {}))
    model_configs = _load_model_configs(file_data.get("model_configs", {}))
    default_model = file_data.get("default_model", "")

    # Migration: if no providers/model_configs but old-style llm config exists,
    # auto-generate a "default" openai-compat provider + model config
    from .llm.types import PROVIDER_OPENAI_COMPAT
    if not providers and llm.url:
        providers["default"] = ProviderConfig(
            type=PROVIDER_OPENAI_COMPAT, url=llm.url, api_key=llm.api_key,
        )
        model_configs["default"] = ModelConfig(
            provider="default",
            model=llm.model,
            context_window_size=llm.context_window_size,
            timeout=llm.timeout,
            streaming=llm.streaming,
        )
        if not default_model:
            default_model = "default"

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
        cleanup=cleanup,
        embedding=embedding,
        heartbeat=heartbeat,
        http=http,
        agent=agent,
        skills=skills,
        reflection=reflection,
        providers=providers,
        model_configs=model_configs,
        default_model=default_model,
        vault_retrieval=vault_retrieval,
        relevance=relevance,
        vault=vault,
        notifications=notifications,
        email=email,
        background=background,
        env=env_vars,
        system_prompt=system_prompt,
    )

    # Apply custom env vars (only sets those not already in environment)
    config.apply_env()

    return config


