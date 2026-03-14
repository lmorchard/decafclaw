"""Core tools — shell, file, and web operations."""

import httpx
import json
import logging
import subprocess

log = logging.getLogger(__name__)


def tool_shell(ctx, command: str) -> str:
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


def tool_read_file(ctx, path: str) -> str:
    """Read a file and return its contents."""
    log.info(f"[tool:read_file] {path}")
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"[error: file not found: {path}]"
    except PermissionError:
        return f"[error: permission denied: {path}]"


def tool_web_fetch(ctx, url: str) -> str:
    """Fetch a URL and return the raw response body as text."""
    log.info(f"[tool:web_fetch] {url}")
    try:
        resp = httpx.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        text = resp.text
        if len(text) > 50000:
            text = text[:50000] + "\n\n[truncated at 50000 chars]"
        return text
    except httpx.HTTPError as e:
        return f"[error: {e}]"


def tool_debug_context(ctx) -> str:
    """Dump the current conversation context for debugging."""
    log.info("[tool:debug_context]")
    messages = getattr(ctx, "messages", None)
    if messages is None:
        return "[no context available]"

    # Summarize each message
    lines = [f"Total messages: {len(messages)}\n"]
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        # Truncate long content
        preview = content[:200] + "..." if len(content) > 200 else content

        if role == "system":
            lines.append(f"[{i}] system: {preview}")
        elif role == "user":
            lines.append(f"[{i}] user: {preview}")
        elif role == "assistant":
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                lines.append(f"[{i}] assistant: (tool calls: {', '.join(names)})")
            else:
                lines.append(f"[{i}] assistant: {preview}")
        elif role == "tool":
            lines.append(f"[{i}] tool [{tool_call_id}]: {preview}")
        else:
            lines.append(f"[{i}] {role}: {preview}")

    return "\n".join(lines)


CORE_TOOLS = {
    "shell": tool_shell,
    "read_file": tool_read_file,
    "web_fetch": tool_web_fetch,
    "debug_context": tool_debug_context,
}

CORE_TOOL_DEFINITIONS = [
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
            "description": "Fetch raw HTML from a URL via HTTP GET. Use this when you need the original markup or headers. For clean readable content from articles or PDFs, prefer tabstack_extract_markdown instead.",
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
    {
        "type": "function",
        "function": {
            "name": "debug_context",
            "description": "Dump the current conversation context for debugging. Shows all messages the LLM can see, including system prompt, user messages, assistant responses, and tool results. Use when asked to inspect or describe your context. IMPORTANT: Always paste the full output of this tool verbatim in your response — do not summarize or paraphrase it.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
