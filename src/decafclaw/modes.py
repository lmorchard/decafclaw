from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


@dataclass
class SessionMode:
    """A conversation session mode bundling auto-approval presets and promoted tools."""

    name: str
    description: str
    presets: list[str] = field(default_factory=list)
    promoted_tools: list[str] = field(default_factory=list)


DEFAULT_MODES: dict[str, SessionMode] = {
    "default": SessionMode(
        name="default",
        description="Standard assistance loadout and baseline shell confirmation",
        presets=[],
        promoted_tools=[],
    ),
    "dev": SessionMode(
        name="dev",
        description="Software engineering and pairing",
        presets=["developer", "github"],
        promoted_tools=[
            "shell",
            "workspace_diff",
            "workspace_search",
            "workspace_glob",
            "workspace_list",
            "workspace_edit",
        ],
    ),
    "research": SessionMode(
        name="research",
        description="Deep knowledge and web research",
        presets=[],
        promoted_tools=[
            "web_fetch",
            "vault_search",
            "vault_recent",
            "vault_tags",
            "vault_journal_append",
        ],
    ),
    "admin": SessionMode(
        name="admin",
        description="Agent management and diagnostics",
        presets=[],
        promoted_tools=[
            "admin_read",
            "admin_list",
            "admin_edit",
            "admin_write",
            "mcp_status",
            "health_status",
            "heartbeat_trigger",
        ],
    ),
}


def get_all_modes(config: "Config | None" = None) -> dict[str, SessionMode]:
    """Return all configured modes, starting from DEFAULT_MODES and merging config overrides."""
    modes = {k: replace(v) for k, v in DEFAULT_MODES.items()}
    if config and getattr(config, "modes", None):
        for name, mode in config.modes.items():
            if name != "custom":
                modes[name] = replace(mode)
    return modes


def resolve_active_mode(config: "Config | None" = None, mode_name: str = "") -> SessionMode:
    """Resolve an active mode by name, falling back to 'default' if empty or unknown."""
    all_modes = get_all_modes(config)
    default_mode = all_modes.get("default", DEFAULT_MODES["default"])
    if not mode_name:
        return default_mode
    return all_modes.get(mode_name, default_mode)
