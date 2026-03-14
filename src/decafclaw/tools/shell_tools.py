"""Shell tool with confirmation — requires user approval before execution."""

import asyncio
import logging
import subprocess

log = logging.getLogger(__name__)


async def tool_shell(ctx, command: str) -> str:
    """Run a shell command after user confirmation."""
    log.info(f"[tool:shell] requesting confirmation for: {command}")

    # Publish confirmation request
    confirm_event = asyncio.Event()
    confirm_result = {"approved": False}

    def on_confirm(event):
        if (event.get("type") == "tool_confirm_response"
                and event.get("context_id") == ctx.context_id
                and event.get("tool") == "shell"):
            confirm_result["approved"] = event.get("approved", False)
            confirm_event.set()

    sub_id = ctx.event_bus.subscribe(on_confirm)
    try:
        await ctx.publish("tool_confirm_request",
                          tool="shell",
                          command=command,
                          message=f"Shell command: `{command}`")

        # Wait for confirmation (timeout after 60 seconds)
        try:
            await asyncio.wait_for(confirm_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            log.info(f"[tool:shell] confirmation timed out for: {command}")
            return "[error: shell command timed out waiting for confirmation]"
    finally:
        ctx.event_bus.unsubscribe(sub_id)

    if not confirm_result["approved"]:
        log.info(f"[tool:shell] command denied: {command}")
        return "[error: shell command was denied by user]"

    # Execute the command
    log.info(f"[tool:shell] executing approved command: {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30,
            cwd=str(ctx.config.workspace_path),
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "[error: command timed out after 30 seconds]"


SHELL_TOOLS = {
    "shell": tool_shell,
}

SHELL_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "Run a shell command. REQUIRES USER CONFIRMATION before execution. "
                "The command runs in the workspace directory. Use for tasks that "
                "need system interaction: checking disk space, running scripts, "
                "installing packages, etc. The user will see the command and must "
                "approve it before it runs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
]
