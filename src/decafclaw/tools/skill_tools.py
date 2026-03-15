"""Skill activation tool — lazy-loads skills with permission checking."""

import asyncio
import importlib.util
import json
import logging
from pathlib import Path

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


def _save_permission(config, skill_name: str, value: str):
    """Save a skill permission. Called by the host-side confirmation handler."""
    path = _permissions_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    perms = _load_permissions(config)
    perms[skill_name] = value
    path.write_text(json.dumps(perms, indent=2) + "\n")
    log.info(f"Saved skill permission: {skill_name}={value}")


def _load_native_tools(skill_info):
    """Import tools.py from a skill directory and return (TOOLS, TOOL_DEFINITIONS, module)."""
    tools_path = skill_info.location / "tools.py"
    spec = importlib.util.spec_from_file_location(
        f"decafclaw_skill_{skill_info.name}", tools_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tools = getattr(module, "TOOLS", {})
    tool_defs = getattr(module, "TOOL_DEFINITIONS", [])
    return tools, tool_defs, module


async def _call_init(module, config):
    """Call module.init(config) if it exists, handling sync or async."""
    init_fn = getattr(module, "init", None)
    if init_fn is None:
        return
    if asyncio.iscoroutinefunction(init_fn):
        await init_fn(config)
    else:
        await asyncio.to_thread(init_fn, config)


async def tool_activate_skill(ctx, name: str) -> str:
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
        return f"[error: skill '{name}' not found. Check Available Skills in your instructions.]"

    # Check if already activated
    activated = getattr(ctx, "activated_skills", set())
    if name in activated:
        return f"Skill '{name}' is already active."

    # Check permissions
    perms = _load_permissions(ctx.config)
    if perms.get(name) != "always":
        # Need confirmation
        approved, always = await _request_confirmation(ctx, name)
        if not approved:
            return f"[error: activation of skill '{name}' was denied by user]"
        if always:
            _save_permission(ctx.config, name, "always")

    # Activate the skill
    result_parts = [skill_info.body]

    if skill_info.has_native_tools:
        try:
            tools, tool_defs, module = _load_native_tools(skill_info)
            await _call_init(module, ctx.config)

            # Register on context
            if not hasattr(ctx, "extra_tools"):
                ctx.extra_tools = {}
            ctx.extra_tools.update(tools)

            if not hasattr(ctx, "extra_tool_definitions"):
                ctx.extra_tool_definitions = []
            ctx.extra_tool_definitions.extend(tool_defs)

            tool_names = list(tools.keys())
            result_parts.append(
                f"\n\nThe following tools are now available: {', '.join(tool_names)}"
            )
            log.info(f"Activated native skill '{name}' with tools: {tool_names}")
        except Exception as e:
            log.error(f"Failed to load skill '{name}' tools: {e}")
            return f"[error: failed to load skill '{name}': {e}]"
    else:
        log.info(f"Activated shell-based skill '{name}'")

    # Mark as activated
    if not hasattr(ctx, "activated_skills"):
        ctx.activated_skills = set()
    ctx.activated_skills.add(name)

    return "\n".join(result_parts)


async def _request_confirmation(ctx, skill_name: str) -> tuple[bool, bool]:
    """Request user confirmation for skill activation.

    Returns (approved, always) tuple.
    """
    confirm_event = asyncio.Event()
    confirm_result = {"approved": False, "always": False}

    def on_confirm(event):
        if (event.get("type") == "tool_confirm_response"
                and event.get("context_id") == ctx.context_id
                and event.get("tool") == "activate_skill"):
            confirm_result["approved"] = event.get("approved", False)
            confirm_result["always"] = event.get("always", False)
            confirm_event.set()

    sub_id = ctx.event_bus.subscribe(on_confirm)
    try:
        await ctx.publish(
            "tool_confirm_request",
            tool="activate_skill",
            command=f"Activate skill: {skill_name}",
            skill_name=skill_name,
            message=f"Activate skill: **{skill_name}**",
        )
        try:
            await asyncio.wait_for(confirm_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            log.info(f"Skill activation confirmation timed out for '{skill_name}'")
            return False, False
    finally:
        ctx.event_bus.unsubscribe(sub_id)

    return confirm_result["approved"], confirm_result["always"]


def tool_refresh_skills(ctx) -> str:
    """Re-discover skills and update the system prompt catalog."""
    log.info("[tool:refresh_skills]")
    from ..prompts import load_system_prompt
    config = ctx.config
    config.system_prompt, config.discovered_skills = load_system_prompt(config)
    skill_names = [s.name for s in config.discovered_skills]
    return f"Skills refreshed. Available skills: {', '.join(skill_names) or '(none)'}"


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
