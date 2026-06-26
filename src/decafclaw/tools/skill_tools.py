"""Skill activation tool — lazy-loads skills with permission checking."""

import asyncio
import importlib.util
import inspect
import json
import logging
from pathlib import Path

from ..media import ToolResult
from ..skills import CheckResult, validate_skill_md
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


def _rejection_display_path(config, path: Path) -> str:
    """Show a rejected SKILL.md path relative to a meaningful root.

    For skills outside workspace/agent roots (e.g. absolute
    extra_skill_paths entries) we redact to the trailing
    <skill-dir>/SKILL.md segments rather than echo the full host path
    into refresh_skills output.
    """
    for root in (config.workspace_path, config.agent_path):
        try:
            return str(path.relative_to(root))
        except ValueError:
            continue
    return str(Path(*path.parts[-2:])) if len(path.parts) >= 2 else str(path)


def _lint_tools_py(skill_dir: Path) -> list[CheckResult]:
    """tools.py-specific checks for skill_validate.

    Returns [] for a text-only skill (no tools.py and no stray entrypoint).
    Imports tools.py to surface SyntaxError / NameError / ImportError —
    the same exec_module path activation uses — and introspects (does NOT
    call) get_tools' signature.
    """
    checks: list[CheckResult] = []
    tools_py = skill_dir / "tools.py"

    if not tools_py.exists():
        stray = skill_dir / "main.py"
        if stray.exists():
            checks.append(CheckResult(
                "tools_filename", False,
                "found main.py — native tools must live in 'tools.py'; rename it",
            ))
        return checks

    checks.append(CheckResult("tools_filename", True, "tools.py present"))

    try:
        spec = importlib.util.spec_from_file_location(
            f"decafclaw_skill_validate_{skill_dir.name}", tools_py
        )
        if spec is None or spec.loader is None:
            checks.append(CheckResult(
                "tools_import", False, "could not create an import spec for tools.py",
            ))
            return checks
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:
        checks.append(CheckResult(
            "tools_import", False,
            f"tools.py failed to import: {type(exc).__name__}: {exc}",
        ))
        return checks

    checks.append(CheckResult("tools_import", True, "tools.py imports cleanly"))

    get_tools = getattr(module, "get_tools", None)
    has_static = hasattr(module, "TOOLS") or hasattr(module, "TOOL_DEFINITIONS")
    if get_tools is not None:
        try:
            params = list(inspect.signature(get_tools).parameters.values())
            accepts_ctx = any(
                p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL)
                for p in params
            )
        except (TypeError, ValueError) as exc:
            checks.append(CheckResult(
                "get_tools_signature", False,
                f"could not inspect get_tools signature: {exc}",
            ))
            return checks
        if accepts_ctx:
            checks.append(CheckResult(
                "get_tools_signature", True, "get_tools(ctx) accepts a ctx parameter",
            ))
        else:
            checks.append(CheckResult(
                "get_tools_signature", False,
                "get_tools must accept ctx as its first parameter: "
                "def get_tools(ctx) -> (dict, list)",
            ))
    elif has_static:
        checks.append(CheckResult(
            "tools_exports", True, "exports TOOLS / TOOL_DEFINITIONS",
        ))
    else:
        checks.append(CheckResult(
            "tools_exports", False,
            "tools.py exports neither get_tools(ctx) nor TOOLS / TOOL_DEFINITIONS",
        ))
    return checks


def _render_validation(path: str, checks: list[CheckResult]) -> ToolResult:
    """Render a checklist of CheckResults as a ToolResult (text + data)."""
    ok = all(c.passed for c in checks)
    header = "PASS" if ok else "FAIL"
    lines = [f"skill_validate '{path}': {header}", ""]
    for c in checks:
        lines.append(f"  {'[x]' if c.passed else '[ ]'} {c.name}: {c.message}")
    if not ok:
        lines.append("")
        lines.append(
            "Fix the unchecked items, then run skill_validate again "
            "(or refresh_skills to load it)."
        )
    return ToolResult(
        text="\n".join(lines),
        data={
            "path": path,
            "ok": ok,
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message}
                for c in checks
            ],
        },
    )


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
    # 3. Trusted tier (bundled / admin / extra) — placement is trust
    # 4. User's explicit "always" permission
    # 5. Skill with `auto-approve: true` frontmatter
    # 6. Interactive confirmation
    # Trust by placement: bundled/admin/extra skills are pre-trusted
    # because the user opted them in by editing source, placing files,
    # or editing config. Workspace skills could be agent-authored, so
    # they still require explicit confirmation.
    is_heartbeat = ctx.user_id == "heartbeat-admin"
    is_trusted_tier = skill_info.trust_tier != "workspace"
    perms = _load_permissions(ctx.config)
    if perms.get(name) == "deny":
        return ToolResult(text=f"[error: activation of skill '{name}' was denied by user]")
    if (not is_heartbeat
            and not is_trusted_tier
            and perms.get(name) != "always"
            and not skill_info.auto_approve):
        # Need confirmation (workspace tier only at this point)
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
    # Substitute $SKILL_DIR in the body so the LLM sees usable paths.
    # The command and schedule paths do the same via commands.substitute_body
    # (commands.py:417 / schedules.py:235), both using .resolve() so the LLM
    # always gets an absolute path regardless of how data_home was configured.
    # activate_skill needs to match so skills loaded via extra_skill_paths
    # (where the location isn't a conventional guess) work consistently.
    body = skill_info.body.replace("$SKILL_DIR", str(skill_info.location.resolve()))
    result_parts = [body]

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


def tool_skill_validate(ctx, path: str) -> ToolResult:
    """Pre-flight validate a single workspace skill directory."""
    log.info(f"[tool:skill_validate] {path}")
    workspace = ctx.config.workspace_path.resolve()
    target = (workspace / path).resolve()
    if not target.is_relative_to(workspace):
        return ToolResult(text=f"[error: path '{path}' is outside the workspace]")

    skill_dir = target.parent if target.name == "SKILL.md" else target
    if not skill_dir.is_dir():
        return ToolResult(
            text=f"[error: '{path}' is not a directory in the workspace]"
        )

    checks: list[CheckResult] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        checks.append(CheckResult(
            "skill_md_present", False,
            "no SKILL.md here — a skill needs skills/<name>/SKILL.md",
        ))
        return _render_validation(path, checks)
    checks.append(CheckResult("skill_md_present", True, "SKILL.md present"))

    # Discovery-level checks — shared source of truth with refresh_skills.
    checks.extend(validate_skill_md(skill_md).checks)
    # tools.py checks run regardless of frontmatter validity (filesystem-based).
    checks.extend(_lint_tools_py(skill_dir))

    return _render_validation(path, checks)


def tool_refresh_skills(ctx) -> str | ToolResult:
    """Re-discover skills and update the system prompt catalog."""
    log.info("[tool:refresh_skills]")
    from ..prompts import load_system_prompt
    from ..tool_definitions import invalidate_skill_cache  # deferred: circular dep
    # Intentional mutation: runtime fields need to update the shared config
    # object that the agent loop holds. dataclasses.replace() would create
    # a disconnected copy.
    config = ctx.config
    from ..skills import build_skill_tool_owners
    rejections: list = []
    config.system_prompt, config.discovered_skills = load_system_prompt(
        config, rejections=rejections
    )
    config.skill_tool_owners = build_skill_tool_owners(config.discovered_skills)
    invalidate_skill_cache(config)
    # List all discovered skills — text-only, native-tools, and user-invocable
    # are all valid activatable skills
    names = [s.name for s in config.discovered_skills]
    text = f"Skills refreshed. Available skills: {', '.join(names) or '(none)'}"
    if rejections:
        text += "\nRejected (found but not loaded):\n" + "\n".join(
            f"  - {_rejection_display_path(config, r.path)} — {r.reason}"
            for r in rejections
        )
    return text


SKILL_TOOLS = {
    "activate_skill": tool_activate_skill,
    "refresh_skills": tool_refresh_skills,
    "skill_validate": tool_skill_validate,
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
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "skill_validate",
            "description": (
                "Validate a workspace skill directory BEFORE it loads, and get the "
                "specific reasons it would be rejected. Checks SKILL.md frontmatter "
                "(must have name + description), that native tools live in tools.py "
                "(NOT main.py), that tools.py imports without error, and that it "
                "exports get_tools(ctx) or TOOLS/TOOL_DEFINITIONS. Use this when a "
                "skill you authored isn't appearing, or before refresh_skills, "
                "instead of guessing. Takes a workspace-relative path like "
                "'skills/my-skill'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Workspace-relative path to the skill directory "
                            "(or its SKILL.md), e.g. 'skills/my-skill'."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
]
