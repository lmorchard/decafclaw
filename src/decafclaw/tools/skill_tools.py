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
    """Import tools.py from a skill directory and return (TOOLS, TOOL_DEFINITIONS, module)."""
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
    discovered = getattr(ctx.config, "discovered_skills", [])
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
            log.info(f"Restored skill '{name}' with tools: {list(tools.keys())}")
        except Exception as e:
            log.error(f"Failed to restore skill '{name}': {e}")


async def tool_activate_skill(ctx, name: str) -> str | ToolResult:
    """Activate a skill to make its capabilities available in this conversation."""
    log.info(f"[tool:activate_skill] name={name}")

    # Find the skill in discovered skills
    discovered = getattr(ctx.config, "discovered_skills", [])
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

    # Check permissions — admin heartbeat turns auto-approve (admin-authored)
    is_heartbeat = ctx.user_id == "heartbeat-admin"
    perms = _load_permissions(ctx.config)
    if not is_heartbeat and perms.get(name) != "always":
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

            tool_names = list(tools.keys())
            result_parts.append(
                f"\n\nThe following tools are now available: {', '.join(tool_names)}"
            )
            shutdown_fn = getattr(module, "shutdown", None)
            if shutdown_fn:
                if not hasattr(ctx, "_skill_shutdown_hooks"):
                    ctx._skill_shutdown_hooks = {}
                ctx._skill_shutdown_hooks[name] = shutdown_fn
            log.info(f"Activated native skill '{name}' with tools: {tool_names}")

            # Cache tool names for always-loaded skills so tool_registry
            # can exempt them from deferral
            if getattr(skill_info, "always_loaded", False):
                cached = getattr(ctx.config, "_always_loaded_skill_tools", set())
                ctx.config._always_loaded_skill_tools = cached | set(tool_names)

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
    config = ctx.config
    config.system_prompt, config.discovered_skills = load_system_prompt(config)
    invalidate_skill_cache(config)
    # Only list skills that appear in the catalog (not command-only or scheduled-only)
    activatable = [
        s.name for s in config.discovered_skills
        if s.has_native_tools or (not s.user_invocable and not s.schedule)
    ]
    return f"Skills refreshed. Available skills: {', '.join(activatable) or '(none)'}"


SKILL_TOOLS = {
    "activate_skill": tool_activate_skill,
    "refresh_skills": tool_refresh_skills,
}

SKILL_TOOL_DEFINITIONS = [
    {
        "type": "function",
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
