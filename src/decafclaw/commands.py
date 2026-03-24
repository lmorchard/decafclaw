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
    if not commands:
        return "No commands available."

    lines = ["**Available commands:**\n"]
    for cmd in commands:
        hint = f" {cmd.argument_hint}" if cmd.argument_hint else ""
        lines.append(f"  `{prefix}{cmd.name}{hint}` — {cmd.description}")
    lines.append(f"\nType `{prefix}help` for this list.")
    return "\n".join(lines)


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
    skill: SkillInfo | None = None


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
    return CommandResult(mode="inline", text=result_text, skill=skill)


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

    # Set pre-approved tools
    ctx.preapproved_tools = set(skill.allowed_tools)

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
    body = substitute_body(skill.body, arguments, skill_dir=str(skill.location))

    if skill.context == "fork":
        from .tools.delegate import _run_child_turn
        # User-invoked commands use the full iteration limit, not the child limit
        response = await _run_child_turn(
            ctx, body, effort=skill.effort or "",
            max_iterations=ctx.config.agent.max_tool_iterations)
        return "fork", response

    # Inline mode: return the substituted body as the user message
    return "inline", body
