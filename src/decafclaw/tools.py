"""Tool definitions and execution.

Tools are plain functions. The TOOL_DEFINITIONS list is the OpenAI-format
schema passed to the LLM. execute_tool() dispatches by name.
"""

import httpx
import json
import logging
import subprocess

log = logging.getLogger(__name__)


# -- Tool implementations ---------------------------------------------------

def tool_shell(command: str) -> str:
    """Run a shell command and return stdout + stderr."""
    log.info(f"[tool:shell] {command}")
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "[error: command timed out after 30 seconds]"


def tool_read_file(path: str) -> str:
    """Read a file and return its contents."""
    log.info(f"[tool:read_file] {path}")
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"[error: file not found: {path}]"
    except PermissionError:
        return f"[error: permission denied: {path}]"


def tool_web_fetch(url: str) -> str:
    """Fetch a URL and return the response body."""
    log.info(f"[tool:web_fetch] {url}")
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        # Truncate very long responses
        text = resp.text
        if len(text) > 50000:
            text = text[:50000] + "\n\n[truncated at 50000 chars]"
        return text
    except httpx.HTTPError as e:
        return f"[error: {e}]"


# -- Tool registry ----------------------------------------------------------

TOOLS = {
    "shell": tool_shell,
    "read_file": tool_read_file,
    "web_fetch": tool_web_fetch,
}

# OpenAI function-calling format — hand-written so you can see exactly
# what the LLM receives.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Run a shell command and return stdout and stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the local filesystem and return its contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path to read",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL via HTTP GET and return the response body as text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    }
                },
                "required": ["url"],
            },
        },
    },
]


def execute_tool(name: str, arguments: dict) -> str:
    """Execute a tool by name and return the result as a string."""
    fn = TOOLS.get(name)
    if fn is None:
        return f"[error: unknown tool: {name}]"
    try:
        return fn(**arguments)
    except Exception as e:
        return f"[error executing {name}: {e}]"
