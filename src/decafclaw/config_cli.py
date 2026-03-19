"""CLI tool for config inspection and editing.

Usage:
    python -m decafclaw config show [group] [--reveal]
    python -m decafclaw config get <path>
    python -m decafclaw config set <path> <value>
"""

import argparse
import json
import sys
from dataclasses import fields

from .config import Config, load_config


def _print_group(prefix: str, dc_instance, reveal: bool) -> None:
    """Print all fields of a dataclass with dotted prefix."""
    for f in fields(dc_instance):
        value = getattr(dc_instance, f.name)
        if hasattr(value, "__dataclass_fields__"):
            _print_group(f"{prefix}.{f.name}", value, reveal)
            continue
        display = _format_value(value, f, reveal)
        print(f"{prefix}.{f.name} = {display}")


def _format_value(value, field_info, reveal: bool) -> str:
    if not reveal and field_info.metadata.get("secret") and value:
        return "****"
    if isinstance(value, list):
        return json.dumps(value)
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _resolve_field(config: Config, path: str):
    """Walk dotted path and return (parent_obj, field_info, value)."""
    parts = path.split(".")
    obj = config
    for part in parts[:-1]:
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    if not hasattr(obj, "__dataclass_fields__"):
        return None
    for f in fields(obj):
        if f.name == parts[-1]:
            return (obj, f, getattr(obj, f.name))
    return None


def _coerce_cli_value(field_info, raw: str):
    """Coerce a CLI string value based on the field's type."""
    field_type = field_info.type
    # Handle string type annotations
    if field_type in (bool, "bool"):
        return raw.strip().lower() in ("true", "1", "yes")
    if field_type in (int, "int"):
        return int(raw)
    if field_type in (float, "float"):
        return float(raw)
    if field_type in ("list[str]",) or (
        hasattr(field_type, "__origin__") and field_type.__origin__ is list
    ):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return [s.strip() for s in raw.split(",") if s.strip()]
    return raw


def cmd_show(args) -> None:
    """Show resolved config, optionally filtered by group."""
    config = load_config()

    valid_groups = set()
    for group_field in fields(config):
        if group_field.name in ("system_prompt", "discovered_skills"):
            continue
        group = getattr(config, group_field.name)
        if hasattr(group, "__dataclass_fields__"):
            valid_groups.add(group_field.name)
            if args.group and group_field.name != args.group:
                continue
            _print_group(group_field.name, group, args.reveal)

    # Show env section (treated as secrets by default)
    valid_groups.add("env")
    if not args.group or args.group == "env":
        for key in sorted(config.env):
            display = config.env[key] if args.reveal else "****"
            print(f"env.{key} = {display}")

    if args.group and args.group not in valid_groups:
        print(f"Unknown group: {args.group}", file=sys.stderr)
        sys.exit(1)


def cmd_get(args) -> None:
    """Get a single config value."""
    config = load_config()
    # Handle env.* paths specially
    if args.path.startswith("env."):
        key = args.path[4:]
        if key in config.env:
            print(config.env[key])
        else:
            print(f"Unknown config path: {args.path}", file=sys.stderr)
            sys.exit(1)
        return
    resolved = _resolve_field(config, args.path)
    if resolved is None:
        print(f"Unknown config path: {args.path}", file=sys.stderr)
        sys.exit(1)
    _, _, value = resolved
    if isinstance(value, list):
        print(json.dumps(value))
    elif isinstance(value, bool):
        print(str(value).lower())
    else:
        print(value)


def cmd_set(args) -> None:
    """Set a config value in config.json."""
    config = load_config()
    config_path = config.agent_path / "config.json"

    # env.* paths are freeform — always strings, no validation needed
    is_env = args.path.startswith("env.")
    if not is_env:
        resolved = _resolve_field(config, args.path)
        if resolved is None:
            print(f"Unknown config path: {args.path}", file=sys.stderr)
            sys.exit(1)
        _, field_info, _ = resolved
        value = _coerce_cli_value(field_info, args.value)
    else:
        value = args.value

    # Load existing file or start fresh
    if config_path.exists():
        file_data = json.loads(config_path.read_text())
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        file_data = {}

    # Set in nested dict, validating intermediate keys are dicts
    parts = args.path.split(".")
    d = file_data
    for part in parts[:-1]:
        if part not in d:
            d[part] = {}
        elif not isinstance(d[part], dict):
            print(
                f"Cannot set {args.path}: key '{part}' is not a JSON object",
                file=sys.stderr,
            )
            sys.exit(1)
        d = d[part]
    d[parts[-1]] = value

    config_path.write_text(json.dumps(file_data, indent=2) + "\n")
    print(f"Set {args.path} = {json.dumps(value) if isinstance(value, list) else value}")


# Mapping of env var names → JSON config paths
_ENV_TO_PATH: dict[str, str] = {
    # llm
    "LLM_URL": "llm.url", "LLM_MODEL": "llm.model",
    "LLM_API_KEY": "llm.api_key", "LLM_STREAMING": "llm.streaming",
    # mattermost
    "MATTERMOST_URL": "mattermost.url", "MATTERMOST_TOKEN": "mattermost.token",
    "MATTERMOST_BOT_USERNAME": "mattermost.bot_username",
    "MATTERMOST_IGNORE_BOTS": "mattermost.ignore_bots",
    "MATTERMOST_IGNORE_WEBHOOKS": "mattermost.ignore_webhooks",
    "MATTERMOST_DEBOUNCE_MS": "mattermost.debounce_ms",
    "MATTERMOST_COOLDOWN_MS": "mattermost.cooldown_ms",
    "MATTERMOST_REQUIRE_MENTION": "mattermost.require_mention",
    "MATTERMOST_USER_RATE_LIMIT_MS": "mattermost.user_rate_limit_ms",
    "MATTERMOST_CHANNEL_BLOCKLIST": "mattermost.channel_blocklist",
    "MATTERMOST_CIRCUIT_BREAKER_MAX": "mattermost.circuit_breaker_max",
    "MATTERMOST_CIRCUIT_BREAKER_WINDOW_SEC": "mattermost.circuit_breaker_window_sec",
    "MATTERMOST_CIRCUIT_BREAKER_PAUSE_SEC": "mattermost.circuit_breaker_pause_sec",
    "MATTERMOST_ENABLE_EMOJI_CONFIRMS": "mattermost.enable_emoji_confirms",
    "LLM_STREAM_THROTTLE_MS": "mattermost.stream_throttle_ms",
    "MATTERMOST_STREAM_THROTTLE_MS": "mattermost.stream_throttle_ms",
    # compaction
    "COMPACTION_LLM_URL": "compaction.url", "COMPACTION_LLM_MODEL": "compaction.model",
    "COMPACTION_LLM_API_KEY": "compaction.api_key",
    "COMPACTION_MAX_TOKENS": "compaction.max_tokens",
    "COMPACTION_LLM_MAX_TOKENS": "compaction.llm_max_tokens",
    "COMPACTION_PRESERVE_TURNS": "compaction.preserve_turns",
    # embedding
    "EMBEDDING_MODEL": "embedding.model", "EMBEDDING_URL": "embedding.url",
    "EMBEDDING_API_KEY": "embedding.api_key",
    "MEMORY_SEARCH_STRATEGY": "embedding.search_strategy",
    # heartbeat
    "HEARTBEAT_INTERVAL": "heartbeat.interval", "HEARTBEAT_USER": "heartbeat.user",
    "HEARTBEAT_CHANNEL": "heartbeat.channel",
    "HEARTBEAT_SUPPRESS_OK": "heartbeat.suppress_ok",
    # http
    "HTTP_ENABLED": "http.enabled", "HTTP_HOST": "http.host",
    "HTTP_PORT": "http.port", "HTTP_SECRET": "http.secret",
    "HTTP_BASE_URL": "http.base_url",
    # agent (DATA_HOME and AGENT_ID excluded — they're bootstrap-only,
    # determined by env vars, not the config file)
    "AGENT_USER_ID": "agent.user_id",
    "MAX_TOOL_ITERATIONS": "agent.max_tool_iterations",
    "MAX_CONCURRENT_TOOLS": "agent.max_concurrent_tools",
    "MAX_MESSAGE_LENGTH": "agent.max_message_length",
    "TOOL_CONTEXT_BUDGET_PCT": "agent.tool_context_budget_pct",
    "ALWAYS_LOADED_TOOLS": "agent.always_loaded_tools",
    "CHILD_MAX_TOOL_ITERATIONS": "agent.child_max_tool_iterations",
    "CHILD_TIMEOUT_SEC": "agent.child_timeout_sec",
    # skills
    "TABSTACK_API_KEY": "skills.tabstack.api_key",
    "TABSTACK_API_URL": "skills.tabstack.api_url",
    "CLAUDE_CODE_MODEL": "skills.claude_code.model",
    "CLAUDE_CODE_BUDGET_DEFAULT": "skills.claude_code.budget_default",
    "CLAUDE_CODE_BUDGET_MAX": "skills.claude_code.budget_max",
    "CLAUDE_CODE_SESSION_TIMEOUT": "skills.claude_code.session_timeout",
}


def cmd_import_env(args) -> None:
    """Convert .env file to config.json."""
    from pathlib import Path

    env_path = Path(args.file)
    if not env_path.exists():
        print(f"File not found: {env_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    config_path = config.agent_path / "config.json"

    # Load existing config.json or start fresh
    if config_path.exists():
        file_data = json.loads(config_path.read_text())
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        file_data = {}

    # Parse .env file
    imported = 0
    env_imported = 0
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]

        path = _ENV_TO_PATH.get(key)
        if path is not None:
            # Known config field — resolve type and set at the mapped path
            resolved = _resolve_field(config, path)
            if resolved is not None:
                _, field_info, _ = resolved
                coerced = _coerce_cli_value(field_info, value)
                parts = path.split(".")
                d = file_data
                for part in parts[:-1]:
                    d = d.setdefault(part, {})
                d[parts[-1]] = coerced
                imported += 1
                continue

        # Unknown var → import into env section
        env_section = file_data.setdefault("env", {})
        env_section[key] = value
        env_imported += 1

    config_path.write_text(json.dumps(file_data, indent=2) + "\n")
    parts = [f"Imported {imported} settings"]
    if env_imported:
        parts.append(f"{env_imported} env vars")
    print(f"{' + '.join(parts)} to {config_path}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="decafclaw config")
    sub = parser.add_subparsers(dest="command")

    show_p = sub.add_parser("show", help="Show resolved config values")
    show_p.add_argument("group", nargs="?", help="Filter by config group")
    show_p.add_argument("--reveal", action="store_true",
                        help="Show secret values (masked by default)")

    get_p = sub.add_parser("get", help="Get a single config value")
    get_p.add_argument("path", help="Dotted path (e.g. mattermost.url)")

    set_p = sub.add_parser("set", help="Set a value in config.json")
    set_p.add_argument("path", help="Dotted path (e.g. mattermost.url)")
    set_p.add_argument("value", help="Value to set")

    import_p = sub.add_parser("import", help="Import settings from .env file")
    import_p.add_argument("file", nargs="?", default=".env",
                          help="Path to .env file (default: .env)")

    args = parser.parse_args()
    if args.command == "show":
        cmd_show(args)
    elif args.command == "get":
        cmd_get(args)
    elif args.command == "set":
        cmd_set(args)
    elif args.command == "import":
        cmd_import_env(args)
    else:
        parser.print_help()
