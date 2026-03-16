"""Claude Code skill — delegate coding tasks to Claude Code as a subagent."""

import logging

log = logging.getLogger(__name__)

# Module state, populated by init()
_config = None


def init(config):
    """Initialize the Claude Code skill. Called by the skill loader on activation."""
    global _config
    _config = config
    log.info("Claude Code skill initialized")


async def tool_claude_code_start(ctx, cwd: str, description: str = "",
                                  model: str = "", budget_usd: float = 0) -> str:
    """Start a new Claude Code session for a working directory."""
    log.info(f"[tool:claude_code_start] cwd={cwd}")
    return "[error: not yet implemented]"


async def tool_claude_code_send(ctx, session_id: str, prompt: str) -> str:
    """Send a prompt to an active Claude Code session."""
    log.info(f"[tool:claude_code_send] session={session_id}")
    return "[error: not yet implemented]"


async def tool_claude_code_stop(ctx, session_id: str) -> str:
    """Stop a Claude Code session and clean up."""
    log.info(f"[tool:claude_code_stop] session={session_id}")
    return "[error: not yet implemented]"


async def tool_claude_code_sessions(ctx) -> str:
    """List active Claude Code sessions."""
    log.info("[tool:claude_code_sessions]")
    return "[error: not yet implemented]"


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
