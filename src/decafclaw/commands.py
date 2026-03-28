"""User-invokable commands — trigger detection, argument substitution, execution."""

import logging
import re
from dataclasses import dataclass, field

from .skills import SkillInfo, find_command, list_commands

log = logging.getLogger(__name__)


def parse_command_trigger(text: str, prefix: str = "!") -> tuple[str, str] | None:
    """Parse a command trigger from message text.

    Returns (command_name, arguments) if the text starts with the prefix
    followed by a letter, or None if it's a regular message.
    Avoids false positives on "!!! wow" or "/ path".
    """
    if not text.startswith(prefix):
        return None
    rest = text[len(prefix):]
    if not rest or not rest[0].isalpha():
        return None
    parts = rest.split(None, 1)
    command_name = parts[0]
    arguments = parts[1] if len(parts) > 1 else ""
    return command_name, arguments


def substitute_body(body: str, arguments: str = "", skill_dir: str = "") -> str:
    """Substitute placeholders in a skill/schedule body.

    Supported placeholders:
    - $ARGUMENTS — full argument string
    - $0, $1, ... — positional arguments
    - $SKILL_DIR — path to the skill's directory
    """
    # Always replace placeholders, even with empty arguments
    positional = arguments.split() if arguments else []
    has_placeholders = "$ARGUMENTS" in body or re.search(r"\$\d+", body)

    # Replace positional: $0, $1, $2, ...
    def _replace_positional(match):
        idx = int(match.group(1))
        if idx < len(positional):
            return positional[idx]
        return match.group(0)  # leave unreplaced if out of range

    result = re.sub(r"\$(\d+)", _replace_positional, body)

    # Replace $ARGUMENTS with the full string
    result = result.replace("$ARGUMENTS", arguments)

    # Replace $SKILL_DIR with the skill directory path
    if skill_dir:
        result = result.replace("$SKILL_DIR", skill_dir)

    # If no placeholders existed at all and there are arguments, append them
    if not has_placeholders and arguments:
        result = result.rstrip() + f"\n\nARGUMENTS: {arguments}"

    return result


# Backward compat alias
substitute_arguments = substitute_body


def format_help(discovered_skills: list[SkillInfo], prefix: str = "!") -> str:
    """Format the help text listing all available commands."""
    commands = list_commands(discovered_skills)
    has_commands = bool(commands)

    lines = ["**Available commands:**\n"]
    for cmd in commands:
        hint = f" {cmd.argument_hint}" if cmd.argument_hint else ""
        lines.append(f"  `{prefix}{cmd.name}{hint}` — {cmd.description}")

    # Add MCP prompt commands
    mcp_commands = _get_mcp_prompt_commands()
    if mcp_commands:
        if has_commands:
            lines.append("")
        lines.append("**MCP prompts:**\n")
        for cmd_name, desc, args_hint in mcp_commands:
            hint = f" {args_hint}" if args_hint else ""
            lines.append(f"  `{prefix}{cmd_name}{hint}` — {desc}")
        has_commands = True

    if not has_commands:
        return "No commands available."

    lines.append(f"\nType `{prefix}help` for this list.")
    return "\n".join(lines)


# -- MCP prompt commands -------------------------------------------------------


def _get_mcp_prompt_commands() -> list[tuple[str, str, str]]:
    """Get MCP prompts as command tuples: (command_name, description, args_hint).

    Dynamically reads from the live MCP registry.
    """
    from .mcp_client import get_registry

    registry = get_registry()
    if not registry:
        return []

    commands = []
    for server_name, prompt in registry.get_prompts():
        name = getattr(prompt, "name", "")
        desc = getattr(prompt, "description", "") or f"MCP prompt from {server_name}"
        args = getattr(prompt, "arguments", []) or []

        cmd_name = f"mcp__{server_name}__{name}"

        # Build args hint like "<text> [language]"
        hints = []
        for arg in args:
            arg_name = getattr(arg, "name", "") if not isinstance(arg, dict) else arg.get("name", "")
            arg_req = getattr(arg, "required", False) if not isinstance(arg, dict) else arg.get("required", False)
            if arg_req:
                hints.append(f"<{arg_name}>")
            else:
                hints.append(f"[{arg_name}]")
        args_hint = " ".join(hints)

        commands.append((cmd_name, desc, args_hint))

    return sorted(commands)


def _parse_mcp_prompt_command(cmd_name: str) -> tuple[str, str] | None:
    """Parse an MCP prompt command name into (server_name, prompt_name).

    Returns None if the name doesn't match the mcp__<server>__<prompt> pattern.
    """
    if not cmd_name.startswith("mcp__"):
        return None
    rest = cmd_name[5:]  # after "mcp__"
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _parse_positional_args(arguments: str) -> list[str]:
    """Parse positional arguments, respecting quoted strings."""
    import shlex
    if not arguments:
        return []
    try:
        return shlex.split(arguments)
    except ValueError:
        # Unbalanced quotes — fall back to simple split
        return arguments.split()


# -- Centralized command dispatch ----------------------------------------------


@dataclass
class CommandResult:
    """Result of dispatching a command.

    mode values:
    - "not_command": text is not a command, pass through as normal message
    - "help": help text response, display directly
    - "unknown": unknown command, display error directly
    - "fork": command ran in forked context, display response directly
    - "inline": command body substituted, run as agent turn with ctx setup done
    - "error": command failed, display error directly
    """
    mode: str
    text: str = ""
    display_text: str = ""  # short version for archive/display (inline commands)
    skill: SkillInfo | None = None


async def _execute_mcp_prompt_command(ctx, server_name: str, prompt_name: str,
                                       arguments: str, prefix: str = "!") -> CommandResult:
    """Execute an MCP prompt as a user-invokable command."""
    import asyncio

    from .mcp_client import _convert_prompt_response, get_registry

    registry = get_registry()
    if not registry:
        return CommandResult(mode="error", text="No MCP servers configured.")

    state = registry.servers.get(server_name)
    if not state or state.status != "connected" or not state.session:
        return CommandResult(mode="error",
                             text=f"MCP server '{server_name}' is not connected.")

    # Find the prompt in the cached list
    prompt_info = None
    for p in state.prompts:
        if getattr(p, "name", "") == prompt_name:
            prompt_info = p
            break

    if not prompt_info:
        return CommandResult(mode="error",
                             text=f"Prompt '{prompt_name}' not found on server '{server_name}'.")

    # Map positional args to declared argument names
    declared_args = getattr(prompt_info, "arguments", []) or []
    positional = _parse_positional_args(arguments)

    args_dict = {}
    missing_required = []
    for i, arg_def in enumerate(declared_args):
        if isinstance(arg_def, dict):
            arg_name = arg_def.get("name", "")
            arg_req = arg_def.get("required", False)
            arg_desc = arg_def.get("description", "")
        else:
            arg_name = getattr(arg_def, "name", "")
            arg_req = getattr(arg_def, "required", False)
            arg_desc = getattr(arg_def, "description", "")

        if i < len(positional):
            args_dict[arg_name] = positional[i]
        elif arg_req:
            missing_required.append((arg_name, arg_desc))

    if missing_required:
        lines = [f"Missing required argument(s) for `{prompt_name}`:"]
        for arg_name, arg_desc in missing_required:
            desc_str = f": {arg_desc}" if arg_desc else ""
            lines.append(f"  - `{arg_name}`{desc_str}")

        # Show full usage
        hints = []
        for arg_def in declared_args:
            an = getattr(arg_def, "name", "") if not isinstance(arg_def, dict) else arg_def.get("name", "")
            ar = getattr(arg_def, "required", False) if not isinstance(arg_def, dict) else arg_def.get("required", False)
            hints.append(f"<{an}>" if ar else f"[{an}]")
        lines.append(f"\nUsage: `{prefix}mcp__{server_name}__{prompt_name} {' '.join(hints)}`")
        return CommandResult(mode="error", text="\n".join(lines))

    # Call get_prompt on the server
    try:
        timeout_s = state.config.timeout / 1000
        result = await asyncio.wait_for(
            state.session.get_prompt(prompt_name, args_dict or None),
            timeout=timeout_s,
        )
        text = _convert_prompt_response(result)
    except asyncio.TimeoutError:
        return CommandResult(mode="error",
                             text=f"MCP prompt '{prompt_name}' timed out.")
    except Exception as e:
        return CommandResult(mode="error",
                             text=f"Failed to get MCP prompt '{prompt_name}': {e}")

    # Inject as user message with a note
    cmd_display = f"mcp__{server_name}__{prompt_name}"
    injected = (f"The user invoked MCP prompt `{cmd_display}` which returned:\n\n{text}")

    return CommandResult(mode="inline", text=injected,
                         display_text=f"{prefix}{cmd_display} {arguments}".strip())


async def dispatch_command(ctx, text: str, prefixes: list[str] | None = None,
                           ) -> CommandResult:
    """Detect, validate, and execute a command from user text.

    This is the single entry point for all command handling. Both Mattermost
    and web UI call this function. It handles:
    - Command detection (tries each prefix)
    - Help command
    - Unknown command error
    - Fork mode execution (runs the child turn, returns result)
    - Inline mode preparation (substitutes body, pre-activates skills on ctx)

    Args:
        ctx: Runtime context (will be modified for inline commands:
             preapproved_tools, activated_skills, extra_tools set)
        text: Raw user message text
        prefixes: Command prefixes to try (default: ["!", "/"])

    Returns:
        CommandResult with mode and text. Callers should:
        - "not_command": run text as a normal agent turn
        - "help", "unknown", "fork", "error": display result.text directly
        - "inline": run result.text as agent turn (ctx already set up)
    """
    if prefixes is None:
        prefixes = ["!", "/"]

    # Try each prefix
    trigger = None
    matched_prefix = "!"
    for prefix in prefixes:
        trigger = parse_command_trigger(text, prefix=prefix)
        if trigger:
            matched_prefix = prefix
            break

    if not trigger:
        return CommandResult(mode="not_command", text=text)

    cmd_name, cmd_args = trigger

    # Help is special
    if cmd_name == "help":
        discovered = getattr(ctx.config, "discovered_skills", [])
        help_text = format_help(discovered, prefix=matched_prefix)
        return CommandResult(mode="help", text=help_text)

    # Look up the command
    discovered = getattr(ctx.config, "discovered_skills", [])
    skill = find_command(cmd_name, discovered)
    if skill is None:
        # Check if it's an MCP prompt command
        mcp_parsed = _parse_mcp_prompt_command(cmd_name)
        if mcp_parsed:
            return await _execute_mcp_prompt_command(ctx, mcp_parsed[0], mcp_parsed[1], cmd_args,
                                                           prefix=matched_prefix)
        return CommandResult(
            mode="unknown",
            text=f"Unknown command: `{cmd_name}`. Type `{matched_prefix}help` for available commands.",
        )

    # Execute the command
    result = await execute_command(ctx, skill, cmd_args)
    mode, result_text = result

    if mode == "error":
        return CommandResult(mode="error", text=result_text)
    if mode == "fork":
        return CommandResult(mode="fork", text=result_text, skill=skill)

    # Inline mode: ctx is already set up (preapproved_tools, activated skills)
    # display_text is the short version for archive (not the full prompt body)
    display = f"{matched_prefix}{cmd_name}"
    if cmd_args:
        display += f" {cmd_args}"
    return CommandResult(mode="inline", text=result_text,
                         display_text=display, skill=skill)


async def execute_command(ctx, skill: SkillInfo, arguments: str) -> tuple[str, str]:
    """Execute a user-invoked command.

    Sets up the context (preapproved tools, required skills) and either
    runs a fork or returns the substituted body for inline execution.

    Returns (mode, result) where:
    - mode="fork": result is the child agent's response text
    - mode="inline": result is the substituted body to use as the user message
    - mode="error": result is the error message
    """
    from .media import ToolResult as _ToolResult
    from .tools.skill_tools import activate_skill_internal

    # Set pre-approved tools and scoped shell patterns
    ctx.preapproved_tools = set(skill.allowed_tools)
    skill_dir = str(skill.location)
    ctx.preapproved_shell_patterns = [
        p.replace("$SKILL_DIR", skill_dir) for p in skill.shell_patterns
    ]

    # Auto-activate the skill ONLY if it has native tools to register.
    # Shell-based skills don't need activation — the command body IS the prompt.
    # Activating them would add the SKILL.md body as a tool result, duplicating
    # the command body and confusing the model.
    if skill.has_native_tools and skill.name not in ctx.activated_skills:
        result = await activate_skill_internal(ctx, skill)
        if isinstance(result, _ToolResult):
            return "error", result.text

    # Pre-activate required skills (user invoked the command = implicit approval)
    if skill.requires_skills:
        discovered = getattr(ctx.config, "discovered_skills", [])
        skill_map = {s.name: s for s in discovered}
        for req_name in skill.requires_skills:
            if req_name in ctx.activated_skills:
                continue
            req_info = skill_map.get(req_name)
            if req_info:
                try:
                    result = await activate_skill_internal(ctx, req_info)
                    if isinstance(result, _ToolResult):
                        log.error(f"Failed to activate required skill "
                                  f"'{req_name}' for command '{skill.name}': "
                                  f"{result.text}")
                        return "error", result.text
                except Exception as e:
                    log.error(f"Failed to activate required skill "
                              f"'{req_name}' for command '{skill.name}': {e}")

    # Substitute arguments and skill directory into the body
    body = substitute_body(skill.body, arguments,
                           skill_dir=str(skill.location.resolve()))

    if skill.context == "fork":
        from .tools.delegate import _run_child_turn
        # User-invoked commands use the full iteration limit, not the child limit
        result = await _run_child_turn(
            ctx, body, effort=skill.effort or "",
            max_iterations=ctx.config.agent.max_tool_iterations)
        return "fork", result.text if hasattr(result, "text") else str(result)

    # Inline mode: return the substituted body as the user message
    return "inline", body
