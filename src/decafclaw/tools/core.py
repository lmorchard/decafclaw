"""Core tools — web fetch, debug, time, context stats, ask_user_multiple_choice."""

import json
import logging

import httpx

from ..media import ToolResult, WidgetRequest
from ..util import estimate_tokens

log = logging.getLogger(__name__)


async def tool_web_fetch(ctx, url: str) -> str | ToolResult:
    """Fetch a URL and return the raw response body as text."""
    log.info(f"[tool:web_fetch] {url}")
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text
        if len(text) > 50000:
            text = text[:50000] + "\n\n[truncated at 50000 chars]"
        return text
    except httpx.HTTPError as e:
        return ToolResult(text=f"[error: {e}]")


def tool_debug_context(ctx) -> str | ToolResult:
    """Dump the current conversation context for debugging."""
    log.info("[tool:debug_context]")
    messages = ctx.messages
    if messages is None:
        return "[no context available]"

    # Build full dump with message details
    lines = [f"Total messages: {len(messages)}\n"]
    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls")
        tool_call_id = msg.get("tool_call_id")

        if role == "system":
            lines.append(f"[{i}] system: ({len(content)} chars)")
        elif role == "user":
            lines.append(f"[{i}] user: {content[:200]}{'...' if len(content) > 200 else ''}")
        elif role == "assistant":
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                lines.append(f"[{i}] assistant: (tool calls: {', '.join(names)})")
            else:
                lines.append(f"[{i}] assistant: {content[:200]}{'...' if len(content) > 200 else ''}")
        elif role == "tool":
            lines.append(f"[{i}] tool [{tool_call_id}]: ({len(content)} chars)")
        else:
            lines.append(f"[{i}] {role}: {content[:200]}{'...' if len(content) > 200 else ''}")

    # Build the full LLM context: messages + tool definitions
    from . import TOOL_DEFINITIONS
    extra_tool_defs = ctx.tools.extra_definitions
    all_tool_defs = TOOL_DEFINITIONS + extra_tool_defs

    tool_names = [t["function"]["name"] for t in all_tool_defs]
    lines.append(f"\nTool definitions ({len(all_tool_defs)}): {', '.join(tool_names)}")
    summary = "\n".join(lines)

    full_context = {
        "messages": messages,
        "tools": all_tool_defs,
    }

    # Write full context (no truncation) to workspace file
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    dump_path = workspace / "debug_context.json"
    dump_path.write_text(json.dumps(full_context, indent=2, default=str))

    # Write system prompt as a separate readable file
    system_msg = next((m for m in messages if m.get("role") == "system"), None)
    if system_msg:
        prompt_path = workspace / "debug_system_prompt.md"
        prompt_path.write_text(system_msg.get("content", ""))

    # Also write the summary
    summary_path = workspace / "debug_context_summary.txt"
    summary_path.write_text(summary)

    summary_text = (
        f"{summary}\n\n"
        f"Full context written to workspace/debug_context.json ({dump_path.stat().st_size} bytes)\n"
        f"Summary written to workspace/debug_context_summary.txt"
    )

    # Return media attachments for the JSON and prompt files
    media = [
        {
            "type": "file",
            "filename": "debug_context.json",
            "data": dump_path.read_bytes(),
            "content_type": "application/json",
        },
    ]
    if system_msg:
        media.append({
            "type": "file",
            "filename": "debug_system_prompt.md",
            "data": (system_msg.get("content", "")).encode(),
            "content_type": "text/markdown",
        })

    return ToolResult(text=summary_text, media=media)



def tool_current_time(ctx) -> str | ToolResult:
    """Return the current date and time."""
    from datetime import datetime
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S (%A)")


async def tool_wait(ctx, seconds: int = 30) -> str | ToolResult:
    """Wait for the specified number of seconds before returning.

    Use this when waiting for a background process or external operation
    to complete. Avoids burning iterations polling in a tight loop.
    Maximum wait time is 300 seconds (5 minutes).
    """
    import asyncio
    max_wait = 300
    seconds = max(1, min(seconds, max_wait))
    log.info(f"[tool:wait] sleeping {seconds}s")
    await asyncio.sleep(seconds)
    return f"Waited {seconds} seconds."


def _normalize_multiple_choice_options(options: list) -> list[dict] | None:
    """Normalize mixed options into the widget's data_schema shape.

    Accepts each option as a bare string (used as both value and label)
    or as a dict with both ``value`` and ``label`` (description
    optional). Returns None if the list is empty or contains an
    unusable entry. The dict form requires both fields to match the
    tool-definition schema's ``required: ["value", "label"]``.
    """
    if not options:
        return None
    out: list[dict] = []
    for opt in options:
        if isinstance(opt, str):
            out.append({"value": opt, "label": opt})
        elif isinstance(opt, dict):
            value = opt.get("value")
            label = opt.get("label")
            if not value or not label:
                return None
            entry = {"value": str(value), "label": str(label)}
            desc = opt.get("description")
            if desc:
                entry["description"] = str(desc)
            out.append(entry)
        else:
            return None
    return out


def _default_multiple_choice_callback(options: list[dict],
                                       allow_multiple: bool):
    """Build the default ``on_response`` callback for ask_user_multiple_choice.

    Single: returns ``"User selected: <label>"``; multi: comma-joins
    the labels of selected values. Looks up labels from the option list
    so the inject text reads well even when callers use distinct
    value/label pairs.
    """
    by_value = {o["value"]: o["label"] for o in options}

    def _cb(data: dict) -> str:
        selected = data.get("selected")
        if allow_multiple:
            values = selected if isinstance(selected, list) else []
            labels = [by_value.get(v, v) for v in values]
            if not labels:
                return "User selected nothing."
            return "User selected: " + ", ".join(labels)
        value = selected if isinstance(selected, str) else ""
        if not value:
            return "User did not select an option."
        return f"User selected: {by_value.get(value, value)}"

    return _cb


async def tool_ask_user_multiple_choice(ctx, prompt: str, options: list,
                                        allow_multiple: bool = False) -> ToolResult:
    """Pause the turn and ask the user to pick from a fixed list of options."""
    log.info(f"[tool:ask_user_multiple_choice] prompt={prompt!r} "
             f"options={len(options)} allow_multiple={allow_multiple}")
    if not prompt or not prompt.strip():
        return ToolResult(
            text="[error: ask_user_multiple_choice requires a non-empty prompt]")
    normalized = _normalize_multiple_choice_options(options)
    if normalized is None:
        return ToolResult(
            text="[error: ask_user_multiple_choice needs at least one option; "
                 "each must be a string or {value, label} dict]")
    widget_data = {
        "prompt": prompt,
        "options": normalized,
        "allow_multiple": bool(allow_multiple),
    }
    widget = WidgetRequest(
        widget_type="multiple_choice",
        data=widget_data,
        on_response=_default_multiple_choice_callback(normalized,
                                                      bool(allow_multiple)),
    )
    short = (f"ask: {len(normalized)} option(s)"
             + (" (multi)" if allow_multiple else ""))
    return ToolResult(
        text=f"[awaiting user response: {prompt}]",
        display_short_text=short,
        widget=widget,
        end_turn=True,
    )


def _normalize_text_input_fields(fields: list | None) -> list[dict] | None:
    """Normalize the fields argument into the widget's data_schema shape.

    Accepts None (caller provides default), bare strings (used as both
    key and title-cased label), or dicts. Dict form requires both
    ``key`` and ``label``. Returns None on any bad entry, on duplicate
    keys, or when given None.
    """
    if fields is None:
        return None
    out: list[dict] = []
    seen_keys: set[str] = set()
    for f in fields:
        if isinstance(f, str):
            key = f.strip()
            if not key or key in seen_keys:
                return None
            entry: dict = {"key": key, "label": key.replace("_", " ").title()}
        elif isinstance(f, dict):
            raw_key = f.get("key")
            label = f.get("label")
            if not raw_key or not label:
                return None
            key = str(raw_key).strip()
            if not key or key in seen_keys:
                return None
            entry = {"key": key, "label": str(label)}
            for opt in ("placeholder", "default"):
                v = f.get(opt)
                if isinstance(v, str):
                    entry[opt] = v
            for opt in ("multiline", "required"):
                v = f.get(opt)
                if isinstance(v, bool):
                    entry[opt] = v
            ml = f.get("max_length")
            if isinstance(ml, int) and ml > 0:
                entry["max_length"] = ml
        else:
            return None
        seen_keys.add(entry["key"])
        out.append(entry)
    return out


def _default_text_input_callback(field_keys: list[str]):
    """Build the default ``on_response`` callback for ask_user_text.

    Single field: returns ``"User responded: <value>"`` with the value
    trimmed. Multi-field: returns ``"User responded: {json}"`` with
    keys in the field-definition order. Empty / no recognised data:
    ``"User did not respond."``.
    """
    def _cb(data: dict) -> str:
        if not isinstance(data, dict) or not data:
            return "User did not respond."
        if len(field_keys) == 1:
            value = data.get(field_keys[0], "")
            text = str(value).strip() if value is not None else ""
            if not text:
                return "User did not respond."
            return f"User responded: {text}"
        ordered = {k: str(data.get(k, "")) for k in field_keys}
        if not any(v.strip() for v in ordered.values()):
            return "User did not respond."
        return "User responded: " + json.dumps(ordered, ensure_ascii=False)

    return _cb


async def tool_ask_user_text(ctx, prompt: str, fields: list | None = None,
                             submit_label: str = "Submit") -> ToolResult:
    """Pause the turn and ask the user for free-form text input."""
    log.info(f"[tool:ask_user_text] prompt={prompt!r} "
             f"fields={len(fields) if fields else 0}")
    if not prompt or not prompt.strip():
        return ToolResult(
            text="[error: ask_user_text requires a non-empty prompt]")
    if not fields:
        normalized: list[dict] = [{"key": "value", "label": prompt.strip()}]
    else:
        normalized_or_none = _normalize_text_input_fields(fields)
        if normalized_or_none is None or not normalized_or_none:
            return ToolResult(
                text="[error: ask_user_text fields must each be a non-empty "
                     "string or a {key, label, ...} dict with unique keys]")
        normalized = normalized_or_none
    widget_data: dict = {"prompt": prompt, "fields": normalized}
    if submit_label and submit_label != "Submit":
        widget_data["submit_label"] = submit_label
    widget = WidgetRequest(
        widget_type="text_input",
        data=widget_data,
        on_response=_default_text_input_callback(
            [f["key"] for f in normalized]),
    )
    short = (f"ask: {len(normalized)} field"
             + ("s" if len(normalized) != 1 else ""))
    return ToolResult(
        text=f"[awaiting user response: {prompt}]",
        display_short_text=short,
        widget=widget,
        end_turn=True,
    )


def tool_context_stats(ctx) -> str | ToolResult:
    """Report token budget statistics for the current conversation."""
    log.info("[tool:context_stats]")

    messages = ctx.messages or []
    config = ctx.config

    # System prompt
    system_msg = next((m for m in messages if m.get("role") == "system"), None)
    system_chars = len(system_msg.get("content", "")) if system_msg else 0
    system_tokens = estimate_tokens(system_msg.get("content", "") if system_msg else "")

    # Tool definitions
    from . import TOOL_DEFINITIONS
    extra_tool_defs = ctx.tools.extra_definitions
    from ..mcp_client import get_registry
    mcp_registry = get_registry()
    mcp_tool_defs = mcp_registry.get_tool_definitions() if mcp_registry else []
    all_tool_defs = TOOL_DEFINITIONS + extra_tool_defs + mcp_tool_defs
    tools_json = json.dumps(all_tool_defs)
    tools_tokens = estimate_tokens(tools_json)

    # Messages by role
    role_counts = {}
    role_chars = {}
    for msg in messages:
        role = msg.get("role", "unknown")
        role_counts[role] = role_counts.get(role, 0) + 1
        content = msg.get("content") or ""
        role_chars[role] = role_chars.get(role, 0) + len(content)
        # Include tool call JSON in assistant message size
        if msg.get("tool_calls"):
            role_chars[role] += len(json.dumps(msg["tool_calls"]))

    # History (everything except system prompt)
    history_chars = sum(role_chars.get(r, 0) for r in role_chars if r != "system")
    history_tokens = estimate_tokens("x" * history_chars)

    # Totals
    total_estimated = system_tokens + tools_tokens + history_tokens
    prompt_tokens_actual = ctx.tokens.total_prompt
    completion_tokens_actual = ctx.tokens.total_completion
    compaction_max = config.compaction.max_tokens

    # Archive size
    from ..archive import archive_path
    conv_id = (ctx.conv_id or "unknown")
    archive_file = archive_path(config, conv_id)
    archive_size = archive_file.stat().st_size if archive_file.exists() else 0

    lines = [
        "## Context Stats\n",
        f"**Compaction budget:** {compaction_max:,} tokens",
        f"**Last prompt tokens (actual):** {prompt_tokens_actual:,}",
        f"**Total completion tokens:** {completion_tokens_actual:,}",
        "",
        "### Estimated breakdown (approx)",
        "| Component | Chars | ~Tokens | % of budget |",
        "|-----------|-------|---------|-------------|",
        f"| System prompt | {system_chars:,} | ~{system_tokens:,} | {system_tokens*100//compaction_max if compaction_max else 0}% |",
        f"| Tool definitions ({len(all_tool_defs)}) | {len(tools_json):,} | ~{tools_tokens:,} | {tools_tokens*100//compaction_max if compaction_max else 0}% |",
        f"| Conversation history | {history_chars:,} | ~{history_tokens:,} | {history_tokens*100//compaction_max if compaction_max else 0}% |",
        f"| **Total estimated** | | **~{total_estimated:,}** | **{total_estimated*100//compaction_max if compaction_max else 0}%** |",
        "",
        "### Messages by role",
    ]

    for role in ["system", "user", "assistant", "tool"]:
        count = role_counts.get(role, 0)
        chars = role_chars.get(role, 0)
        if count:
            lines.append(f"- **{role}**: {count} message(s), {chars:,} chars")

    lines.append("\n### Archive")
    lines.append(f"- **Conversation ID:** {conv_id}")
    lines.append(f"- **Archive file size:** {archive_size:,} bytes")

    # Activated skills
    activated = ctx.skills.activated
    if activated:
        lines.append(f"\n### Active skills: {', '.join(activated)}")

    return "\n".join(lines)


CORE_TOOLS = {
    "web_fetch": tool_web_fetch,
    "debug_context": tool_debug_context,
    "context_stats": tool_context_stats,
    "current_time": tool_current_time,
    "wait": tool_wait,
    "ask_user_multiple_choice": tool_ask_user_multiple_choice,
    "ask_user_text": tool_ask_user_text,
}

CORE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "web_fetch",
            "description": "Fetch raw HTML from a URL via HTTP GET. Use this when you need the original markup or headers. If the tabstack skill is activated, prefer tabstack_extract_markdown for clean readable content.",
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
        "priority": "low",
        "function": {
            "name": "debug_context",
            "description": "Dump the current conversation context for debugging. Writes full context as JSON to workspace/debug_context.json and a summary to workspace/debug_context_summary.txt. Returns a brief summary in the response. Use when asked to inspect or describe your context.",
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
            "name": "context_stats",
            "description": "Show token budget statistics for the current conversation. Reports estimated breakdown of system prompt, tool definitions, and history versus the compaction budget. Use when asked about context size, token usage, or why the agent might be forgetting things.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "current_time",
            "description": "Get the current date and time. Use this instead of shell commands like 'date'.",
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
            "name": "wait",
            "description": (
                "Sleep for the specified number of seconds before returning. "
                "Use this when waiting for a background process to complete "
                "instead of polling shell_background_status in a tight loop. "
                "Call wait FIRST, then check status. Maximum 300 seconds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "Number of seconds to wait (1-300, default 30)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "ask_user_multiple_choice",
            "description": (
                "Pause the turn and ask the user to pick from a fixed list "
                "of options. Use ONLY when the right answer is genuinely "
                "ambiguous from context and you cannot make a reasonable "
                "choice on your own. Prefer to act on your best judgment; "
                "calling this tool is costly — it interrupts the user's "
                "flow. Reserve for decisions the user would want to weigh "
                "in on (e.g., \"which of these three files should I edit?\", "
                "\"publish or save as draft?\"). "
                "Only works in the web UI; Mattermost / terminal render "
                "the prompt as text and the turn ends without the choice."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Question presented to the user. Should be concise and answerable by picking one of the options.",
                    },
                    "options": {
                        "type": "array",
                        "description": (
                            "Options to choose from. Each entry is either a "
                            "bare string (used as both value and label) or a "
                            "{value, label, description?} object."
                        ),
                        "items": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "value": {"type": "string"},
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["value", "label"],
                                },
                            ],
                        },
                    },
                    "allow_multiple": {
                        "type": "boolean",
                        "description": "If true, the user can select multiple options (checkboxes); otherwise a single choice (radios). Default false.",
                    },
                },
                "required": ["prompt", "options"],
            },
        },
    },
    {
        "type": "function",
        "priority": "low",
        "function": {
            "name": "ask_user_text",
            "description": (
                "Pause the turn and ask the user a free-form text question — "
                "a single-line answer, a multiline blob, or a small "
                "multi-field form. Use this when the answer is open-ended "
                "(a name, a URL, a paragraph). For picking from a fixed "
                "list of options use ask_user_multiple_choice instead. "
                "Use ONLY when the right answer is genuinely ambiguous "
                "from context and you cannot make a reasonable choice on "
                "your own. Calling this tool is costly — it interrupts "
                "the user's flow. "
                "Only works in the web UI; Mattermost / terminal render "
                "the prompt as text and the turn ends without a response."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Question presented to the user.",
                    },
                    "fields": {
                        "type": "array",
                        "description": (
                            "Optional. Each field is either a bare string "
                            "(used as both key and title-cased label) or a "
                            "{key, label, placeholder?, default?, "
                            "multiline?, required?, max_length?} dict. "
                            "Keys must be unique. Omit for a single-field "
                            "text question keyed 'value'."
                        ),
                        "items": {
                            "anyOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "key": {"type": "string"},
                                        "label": {"type": "string"},
                                        "placeholder": {"type": "string"},
                                        "default": {"type": "string"},
                                        "multiline": {"type": "boolean"},
                                        "required": {"type": "boolean"},
                                        "max_length": {"type": "integer"},
                                    },
                                    "required": ["key", "label"],
                                },
                            ],
                        },
                    },
                    "submit_label": {
                        "type": "string",
                        "description": "Optional submit button label (default 'Submit').",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
]
