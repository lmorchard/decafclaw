"""Skill activation tool — lazy-loads skills with permission checking."""

import asyncio
import importlib.util
import json
import logging
from pathlib import Path

from ..media import ToolResult
from .confirmation import request_confirmation

log = logging.getLogger(__name__)


def _permissions_path(config) -> Path:
    """Path to the skill permissions file (outside workspace, read-only to agent)."""
    return config.agent_path / "skill_permissions.json"


def _load_permissions(config) -> dict:
    """Load skill permissions from disk. Returns {} if missing or corrupt."""
    path = _permissions_path(config)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read skill permissions: {e}")
        return {}


def _save_permission(config, skill_name: str, value: str) -> None:
    """Save a skill permission. Called by the host-side confirmation handler."""
    path = _permissions_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    perms = _load_permissions(config)
    perms[skill_name] = value
    path.write_text(json.dumps(perms, indent=2) + "\n")
    log.info(f"Saved skill permission: {skill_name}={value}")


def _load_native_tools(skill_info) -> tuple[dict, list, object]:
    """Import tools.py from a skill directory and return (TOOLS, TOOL_DEFINITIONS, module).

    If the module exports a get_tools(ctx) function, it can be retrieved
    via getattr(module, "get_tools", None) by the caller.
    """
    tools_path = skill_info.location / "tools.py"
    spec = importlib.util.spec_from_file_location(
        f"decafclaw_skill_{skill_info.name}", tools_path
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec for {tools_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = getattr(module, "TOOLS", {})
    tool_defs = getattr(module, "TOOL_DEFINITIONS", [])
    return tools, tool_defs, module


async def _call_init(module, config, skill_name: str = "") -> None:
    """Call module.init() with config and optional skill config.

    If the module exports a SkillConfig dataclass, resolve it from
    config.skills[skill_name] + env vars, then call init(config, skill_config).
    Otherwise call init(config) for backward compat.
    """
    init_fn = getattr(module, "init", None)
    if init_fn is None:
        return

    skill_config_cls = getattr(module, "SkillConfig", None)
    if skill_config_cls is not None and skill_name:
        from ..config import load_sub_config

        raw = config.skills.get(skill_name, {})
        prefix = f"SKILLS_{skill_name.upper().replace('-', '_')}"
        skill_config = load_sub_config(skill_config_cls, raw, prefix)
        if asyncio.iscoroutinefunction(init_fn):
            await init_fn(config, skill_config)
        else:
            await asyncio.to_thread(init_fn, config, skill_config)
    else:
        if asyncio.iscoroutinefunction(init_fn):
            await init_fn(config)
        else:
            await asyncio.to_thread(init_fn, config)


async def restore_skills(ctx) -> None:
    """Re-activate skills recorded in ctx.skills.activated, without permission checks.

    Called at the start of each web gateway turn to restore skills that were
    active in a previous turn or server session.
    """
    skill_names = set(ctx.skills.activated)
    if not skill_names:
        return
    discovered = ctx.config.discovered_skills
    skill_map = {s.name: s for s in discovered}
    existing_tools = set(ctx.tools.extra.keys())
    for name in skill_names:
        skill_info = skill_map.get(name)
        if not skill_info or not skill_info.has_native_tools:
            continue
        try:
            tools, tool_defs, module = _load_native_tools(skill_info)
            # Skip if these tools are already loaded (e.g. from persisted skill state)
            if all(t in existing_tools for t in tools):
                log.debug(f"Skill '{name}' tools already loaded, skipping restore")
                continue
            await _call_init(module, ctx.config, name)
            ctx.tools.extra.update(tools)
            ctx.tools.extra_definitions.extend(tool_defs)

            # Register dynamic tool provider if available
            get_tools_fn = getattr(module, "get_tools", None)
            if get_tools_fn:
                ctx.tools.dynamic_providers[name] = get_tools_fn
                ctx.tools.dynamic_provider_names[name] = set(tools.keys())

            log.info(f"Restored skill '{name}' with tools: {list(tools.keys())}")
        except Exception as e:
            log.error(f"Failed to restore skill '{name}': {e}")


async def tool_activate_skill(ctx, name: str) -> str | ToolResult:
    """Activate a skill to make its capabilities available in this conversation."""
    log.info(f"[tool:activate_skill] name={name}")

    # Find the skill in discovered skills
    discovered = ctx.config.discovered_skills
    skill_info = None
    for s in discovered:
        if s.name == name:
            skill_info = s
            break

    if skill_info is None:
        return ToolResult(text=f"[error: skill '{name}' not found. Check Available Skills in your instructions.]")

    # Check if already activated
    activated = ctx.skills.activated
    if name in activated:
        return f"Skill '{name}' is already active."

    # Permission resolution, highest precedence first:
    # 1. User's explicit "deny" in skill_permissions.json — always wins
    # 2. Admin heartbeat turns auto-approve
    # 3. User's explicit "always" permission
    # 4. Bundled skill with `auto-approve: true` frontmatter
    # 5. Interactive confirmation
    is_heartbeat = ctx.user_id == "heartbeat-admin"
    perms = _load_permissions(ctx.config)
    if perms.get(name) == "deny":
        return ToolResult(text=f"[error: activation of skill '{name}' was denied by user]")
    if (not is_heartbeat
            and perms.get(name) != "always"
            and not skill_info.auto_approve):
        # Need confirmation
        approved, always = await _request_skill_confirmation(ctx, name)
        if not approved:
            return ToolResult(text=f"[error: activation of skill '{name}' was denied by user]")
        if always:
            _save_permission(ctx.config, name, "always")

    # Activate the skill (shared logic)
    result = await activate_skill_internal(ctx, skill_info)
    if isinstance(result, ToolResult):
        return result
    return result


async def activate_skill_internal(ctx, skill_info) -> str | ToolResult:
    """Activate a skill: load tools, register on ctx, mark active.

    Shared by tool_activate_skill (with permission checks) and
    command execution (without permission checks). Returns the
    skill body text on success.
    """
    name = skill_info.name
    result_parts = [skill_info.body]

    if skill_info.has_native_tools:
        try:
            tools, tool_defs, module = _load_native_tools(skill_info)
            await _call_init(module, ctx.config, skill_info.name)

            ctx.tools.extra.update(tools)
            ctx.tools.extra_definitions.extend(tool_defs)

            # Register dynamic tool provider if the skill exports get_tools()
            get_tools_fn = getattr(module, "get_tools", None)
            if get_tools_fn:
                ctx.tools.dynamic_providers[name] = get_tools_fn
                # Track the initial static tool names so the first refresh
                # can remove tools that aren't in the dynamic subset
                ctx.tools.dynamic_provider_names[name] = set(tools.keys())
                log.info(f"Registered dynamic tool provider for skill '{name}'")

            tool_names = list(tools.keys())
            result_parts.append(
                f"\n\nThe following tools are now available: {', '.join(tool_names)}"
            )
            log.info(f"Activated native skill '{name}' with tools: {tool_names}")

            # Cache tool names for always-loaded skills so tool_registry
            # can exempt them from deferral
            if skill_info.always_loaded:
                ctx.config.always_loaded_skill_tools = (
                    ctx.config.always_loaded_skill_tools | set(tool_names)
                )

        except Exception as e:
            log.error(f"Failed to load skill '{name}' tools: {e}")
            return ToolResult(text=f"[error: failed to load skill '{name}': {e}]")
    else:
        log.info(f"Activated shell-based skill '{name}'")

    ctx.skills.activated.add(name)
    return "\n".join(result_parts)


async def _request_skill_confirmation(ctx, skill_name: str) -> tuple[bool, bool]:
    """Request user confirmation for skill activation.

    Returns (approved, always) tuple.
    """
    result = await request_confirmation(
        ctx, tool_name="activate_skill",
        command=f"Activate skill: {skill_name}",
        message=f"Activate skill: **{skill_name}**",
        skill_name=skill_name,
    )
    return result.get("approved", False), result.get("always", False)


def tool_refresh_skills(ctx) -> str | ToolResult:
    """Re-discover skills and update the system prompt catalog."""
    log.info("[tool:refresh_skills]")
    from ..agent import invalidate_skill_cache  # deferred: circular dep
    from ..prompts import load_system_prompt
    # Intentional mutation: runtime fields need to update the shared config
    # object that the agent loop holds. dataclasses.replace() would create
    # a disconnected copy.
    config = ctx.config
    config.system_prompt, config.discovered_skills = load_system_prompt(config)
    invalidate_skill_cache(config)
    # List all discovered skills — text-only, native-tools, and user-invocable
    # are all valid activatable skills
    names = [s.name for s in config.discovered_skills]
    return f"Skills refreshed. Available skills: {', '.join(names) or '(none)'}"


SKILL_TOOLS = {
    "activate_skill": tool_activate_skill,
    "refresh_skills": tool_refresh_skills,
}

SKILL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "activate_skill",
            "description": (
                "Activate a skill to make its tools available in this conversation. "
                "You MUST call this before using any skill tools — skill tools do not "
                "exist until activated. Check the Available Skills section in your "
                "instructions for what's available. REQUIRES USER CONFIRMATION unless "
                "previously approved. Once activated, the skill's tools become available "
                "for the rest of this conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name of the skill to activate",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "refresh_skills",
            "description": "Re-scan skill directories and update the available skills catalog. Use when new skills have been added or removed without restarting the agent.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
