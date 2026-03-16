"""Claude Code skill — delegate coding tasks to Claude Code as a subagent."""

import logging
import time
from pathlib import Path

from claude_code_sdk import (
    AssistantMessage,
    ClaudeCodeOptions,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

from decafclaw.skills.claude_code.output import SessionLogger
from decafclaw.skills.claude_code.permissions import make_permission_handler
from decafclaw.skills.claude_code.sessions import SessionManager

log = logging.getLogger(__name__)

# Module state, populated by init()
_config = None
_session_manager: SessionManager | None = None


def init(config):
    """Initialize the Claude Code skill. Called by the skill loader on activation."""
    global _config, _session_manager
    _config = config

    # Parse timeout
    from decafclaw.heartbeat import parse_interval
    timeout_sec = parse_interval(config.claude_code_session_timeout) or 1800

    _session_manager = SessionManager(
        timeout_sec=timeout_sec,
        budget_default=config.claude_code_budget_default,
        budget_max=config.claude_code_budget_max,
    )
    log.info(f"Claude Code skill initialized (timeout={timeout_sec}s, "
             f"budget={config.claude_code_budget_default}/{config.claude_code_budget_max})")


def _get_manager() -> SessionManager:
    if _session_manager is None:
        raise RuntimeError("Claude Code skill not initialized")
    return _session_manager


async def tool_claude_code_start(ctx, cwd: str, description: str = "",
                                  model: str = "", budget_usd: float = 0) -> str:
    """Start a new Claude Code session for a working directory."""
    log.info(f"[tool:claude_code_start] cwd={cwd}")
    manager = _get_manager()

    # Validate cwd exists
    if not Path(cwd).is_dir():
        return f"[error: directory not found: {cwd}]"

    try:
        session = manager.create(
            cwd=cwd,
            description=description,
            model=model or None,
            budget_usd=budget_usd if budget_usd > 0 else None,
        )
    except ValueError as e:
        return f"[error: {e}]"

    return (
        f"Claude Code session started.\n"
        f"- **Session ID:** `{session.session_id}`\n"
        f"- **Working directory:** {session.cwd}\n"
        f"- **Budget:** ${session.budget_usd:.2f}\n"
        f"- **Model:** {session.model or (_config and _config.claude_code_model) or '(SDK default)'}\n"
        f"\nUse `claude_code_send` with this session ID to send tasks."
    )


async def tool_claude_code_send(ctx, session_id: str, prompt: str) -> str:
    """Send a prompt to an active Claude Code session."""
    log.info(f"[tool:claude_code_send] session={session_id}")
    manager = _get_manager()

    session = manager.get(session_id)
    if session is None:
        return (
            f"[error: session '{session_id}' not found or expired. "
            f"Start a new session with claude_code_start.]"
        )

    # Check budget
    if session.total_cost_usd >= session.budget_usd:
        return (
            f"[error: session budget exhausted (${session.total_cost_usd:.2f} / "
            f"${session.budget_usd:.2f}). Stop this session and start a new one "
            f"with a higher budget if needed.]"
        )

    # Build options
    model = session.model or (_config.claude_code_model if _config else None) or None
    options = ClaudeCodeOptions(
        cwd=session.cwd,
        model=model,
        can_use_tool=make_permission_handler(ctx, _config),
    )

    # Resume existing session if we have an SDK session ID from a previous send
    if session.sdk_session_id:
        options.resume = session.sdk_session_id
        options.continue_conversation = True

    # Set up logger
    log_dir = _config.workspace_path / "claude-code-logs" if _config else Path("claude-code-logs")
    logger = SessionLogger(log_dir, session.session_id)

    # Stream messages from the SDK
    await ctx.publish("tool_status", tool="claude_code",
                      message=f"Sending to Claude Code ({session.cwd})...")

    try:
        async for message in query(prompt=prompt, options=options):
            logger.log_message(message)

            # Publish progress for Mattermost display
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        # Don't publish every text chunk — just tool usage
                        pass
                    elif isinstance(block, ToolUseBlock):
                        await ctx.publish(
                            "tool_status", tool="claude_code",
                            message=f"Using {block.name}..."
                        )

            elif isinstance(message, ResultMessage):
                # Capture the SDK session ID for resume
                if message.session_id:
                    session.sdk_session_id = message.session_id
                # Update cost tracking
                if message.total_cost_usd is not None:
                    session.total_cost_usd = message.total_cost_usd

    except Exception as e:
        log.error(f"Claude Code SDK error: {e}", exc_info=True)
        return f"[error: Claude Code failed: {e}]"

    # Update session state
    session.send_count += 1
    manager.touch(session_id)

    return logger.build_summary(session_id)


async def tool_claude_code_stop(ctx, session_id: str) -> str:
    """Stop a Claude Code session and clean up."""
    log.info(f"[tool:claude_code_stop] session={session_id}")
    manager = _get_manager()

    session = manager.stop(session_id)
    if session is None:
        return f"[error: session '{session_id}' not found]"

    elapsed = time.monotonic() - session.created_at
    return (
        f"Claude Code session stopped.\n"
        f"- **Session:** `{session_id[:8]}`\n"
        f"- **Working directory:** {session.cwd}\n"
        f"- **Duration:** {elapsed:.0f}s\n"
        f"- **Sends:** {session.send_count}\n"
        f"- **Total cost:** ${session.total_cost_usd:.2f}"
    )


async def tool_claude_code_sessions(ctx) -> str:
    """List active Claude Code sessions."""
    log.info("[tool:claude_code_sessions]")
    manager = _get_manager()

    sessions = manager.list_active()
    if not sessions:
        return "No active Claude Code sessions."

    lines = [f"**Active Claude Code sessions:** ({len(sessions)})\n"]
    now = time.monotonic()
    for s in sessions:
        age = now - s.created_at
        idle = now - s.last_active
        lines.append(
            f"- `{s.session_id}` — {s.cwd}\n"
            f"  {s.description or '(no description)'} | "
            f"age: {age:.0f}s | idle: {idle:.0f}s | "
            f"sends: {s.send_count} | cost: ${s.total_cost_usd:.2f}"
        )
    return "\n".join(lines)


async def shutdown():
    """Close all sessions. Called on skill deactivation."""
    if _session_manager:
        _session_manager.close_all()


TOOLS = {
    "claude_code_start": tool_claude_code_start,
    "claude_code_send": tool_claude_code_send,
    "claude_code_stop": tool_claude_code_stop,
    "claude_code_sessions": tool_claude_code_sessions,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "claude_code_start",
            "description": (
                "Start a new Claude Code session for a working directory. "
                "Only one session per directory. Returns a session ID for use "
                "with claude_code_send."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Path to the project/repository to work in",
                    },
                    "description": {
                        "type": "string",
                        "description": "What this session is for (optional)",
                    },
                    "model": {
                        "type": "string",
                        "description": "Override the Claude model (optional, empty = default)",
                    },
                    "budget_usd": {
                        "type": "number",
                        "description": "Per-session cost limit in USD (optional, 0 = default)",
                    },
                },
                "required": ["cwd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_send",
            "description": (
                "Send a coding task or follow-up to an active Claude Code session. "
                "The session maintains context across sends. Returns a summary of "
                "what Claude Code did."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID from claude_code_start",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The coding task or follow-up message",
                    },
                },
                "required": ["session_id", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_stop",
            "description": "Stop a Claude Code session and free resources. Reports final cost.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Session ID to stop",
                    },
                },
                "required": ["session_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_code_sessions",
            "description": "List all active Claude Code sessions with their IDs, working directories, and cost so far.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
