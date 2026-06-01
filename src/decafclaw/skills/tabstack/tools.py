"""Tabstack skill tools — web browsing, extraction, research, and automation.

Targets tabstack SDK >= 2.6.1, where `/automate` and `/research` stream a typed
discriminated union of events (`event` discriminator + typed `.data` payload), and
`extract.json` / `generate.json` return plain dicts.
"""

import json
import logging
from dataclasses import dataclass, field

from tabstack import APIStatusError, AsyncTabstack

from decafclaw.media import ToolResult
from decafclaw.tools.confirmation import request_confirmation

log = logging.getLogger(__name__)


@dataclass
class SkillConfig:
    api_key: str = field(
        default="", metadata={"secret": True, "env_alias": "TABSTACK_API_KEY"})
    api_url: str = field(
        default="", metadata={"env_alias": "TABSTACK_API_URL"})


# Initialized once via init(config, skill_config) on skill activation
_client: AsyncTabstack | None = None


def init(config, skill_config: SkillConfig):
    """Initialize the Tabstack client. Called by the skill loader on activation."""
    global _client
    api_url = skill_config.api_url or None
    if api_url:
        _client = AsyncTabstack(api_key=skill_config.api_key, base_url=api_url)
    else:
        _client = AsyncTabstack(api_key=skill_config.api_key)
    log.info(f"Tabstack client initialized (url={api_url or 'default'})")


def _get_client() -> AsyncTabstack:
    if _client is None:
        raise RuntimeError("Tabstack not initialized — skill not activated?")
    return _client


# -- Read / extract tools ---------------------------------------------------

async def tool_tabstack_extract_markdown(ctx, url: str) -> ToolResult:
    """Extract clean Markdown from a web page or PDF."""
    log.info(f"[tool:tabstack_extract_markdown] {url}")
    try:
        result = await _get_client().extract.markdown(url=url)
        return ToolResult(
            text=result.content,
            data={"url": url, "size": len(result.content.encode("utf-8"))},
        )
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


async def tool_tabstack_extract_json(ctx, url: str, json_schema: dict) -> ToolResult:
    """Extract structured JSON data from a web page or PDF."""
    log.info(f"[tool:tabstack_extract_json] {url}")
    try:
        # SDK >= 2.6: extract.json returns the parsed object directly (a dict).
        result = await _get_client().extract.json(url=url, json_schema=json_schema)
        return ToolResult(
            text=json.dumps(result, indent=2),
            data={"url": url, "result": result},
        )
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


async def tool_tabstack_generate(ctx, url: str, json_schema: dict, instructions: str) -> str:
    """Transform web/PDF content into structured JSON using LLM instructions."""
    log.info(f"[tool:tabstack_generate] {url}")
    try:
        # SDK >= 2.6: generate.json returns the parsed object directly (a dict).
        result = await _get_client().generate.json(
            url=url, json_schema=json_schema, instructions=instructions
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"[error: {e}]"


# -- Automate (with optional interactive form-fill) -------------------------

# Events whose payload carries a `final_answer` (alias finalAnswer).
_AUTOMATE_FINAL_EVENTS = frozenset(
    {"complete", "task:completed", "task:validated", "task:aborted"}
)
_AUTOMATE_FORM_EVENTS = frozenset(
    {"interactive:form_data:request", "interactive:form_data:error"}
)


async def tool_tabstack_automate(
    ctx,
    task: str,
    url: str | None = None,
    data: dict | None = None,
    interactive: bool = False,
) -> str:
    """Run a multi-step browser automation task.

    When ``interactive`` is true, the browser agent may pause on a form and request
    field values. Those requests are answered from the supplied ``data`` dict — gated
    by a user confirmation before any personal data is submitted. Required fields not
    found in ``data`` cause the request to be cancelled and reported back so the caller
    can gather them and retry.
    """
    log.info(
        "[tool:tabstack_automate] task=%s url=%s interactive=%s data_keys=%s",
        task, url, interactive, sorted((data or {}).keys()),
    )
    try:
        client = _get_client()
        kwargs: dict = {"task": task}
        if url:
            kwargs["url"] = url
        if interactive:
            kwargs["interactive"] = True
            # `data` is only meaningful for interactive form-fill; never forward it
            # otherwise, so a non-interactive call can't leak personal data.
            if data:
                kwargs["data"] = data
        stream = await client.agent.automate(**kwargs)

        final_answer: str | None = None
        error_message: str | None = None
        form_notes: list[str] = []
        async for event in stream:
            kind = getattr(event, "event", None)

            msg = _automate_progress(event)
            if msg:
                log.info("[tool:tabstack_automate] %s", msg)
                await ctx.publish("tool_status", tool="tabstack_automate", message=msg)

            if kind in _AUTOMATE_FINAL_EVENTS:
                # final_answer is consistently aliased across these payload types;
                # getattr keeps us off per-variant isinstance branches for the union.
                answer = getattr(event.data, "final_answer", None)
                if answer:
                    final_answer = answer
                error_message = _automate_error_text(event) or error_message
            elif kind == "error":
                # Top-level uncaught error escaping the task runner.
                error_message = _automate_error_text(event) or error_message
            elif kind in _AUTOMATE_FORM_EVENTS:
                note = await _handle_form_request(ctx, client, event, data or {})
                if note:
                    form_notes.append(note)

        return _compose_automate_result(final_answer, error_message, form_notes)
    except Exception as e:
        return f"[error: {e}]"


# Progress lines are trimmed to keep the live narration tidy.
_PROGRESS_MAX = 200


def _truncate(text: str, limit: int = _PROGRESS_MAX) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _automate_progress(event) -> str | None:
    """Extract a human-readable progress line from an automate stream event.

    Surfaces the high-signal events as a running narration. Deliberately omits
    the typed-in ``value`` on action events — it can contain the personal data we
    just confirmed — and skips the noisy/huge events (screenshots, raw LLM
    generations, metrics, debug). Unmapped events return None and are dropped.
    """
    kind = getattr(event, "event", None)
    data = getattr(event, "data", None)
    if data is None:
        return None
    if kind == "agent:step":
        n = getattr(data, "current_iteration", None)
        return f"step {int(n)}" if n is not None else None
    if kind == "agent:status":
        return getattr(data, "message", None)
    if kind == "agent:reasoned":
        reasoning = getattr(data, "reasoning", None)
        return _truncate(reasoning) if reasoning else None
    if kind == "agent:action":
        # Note: data.value (text typed into a field) is intentionally NOT shown.
        action = getattr(data, "action", None)
        ref = getattr(data, "ref", None)
        if not action:
            return None
        return f"→ {action} ({ref})" if ref else f"→ {action}"
    if kind == "browser:action_completed":
        # Success is implied by forward progress; only surface failures.
        if getattr(data, "success", True):
            return None
        err = getattr(data, "error", None)
        return f"action failed: {_truncate(err)}" if err else "action failed"
    if kind == "browser:navigated":
        title = getattr(data, "title", None)
        nav_url = getattr(data, "url", None)
        if title and nav_url:
            return f"opened {title} — {nav_url}"
        return f"opened {nav_url}" if nav_url else None
    if kind == "agent:extracted":
        extracted = getattr(data, "extracted_data", None)
        return f"extracted: {_truncate(extracted, 120)}" if extracted else None
    if kind == "agent:waiting":
        secs = getattr(data, "seconds", None)
        return f"waiting {int(secs)}s" if secs is not None else None
    if kind == "browser:reconnected":
        return "browser reconnected"
    if kind == "task:validated":
        quality = getattr(data, "completion_quality", None)
        return f"validated ({quality})" if quality else "validated"
    if kind == "task:validation_error":
        return "validation retry"
    if kind == "error":
        return _automate_error_text(event)
    return None


def _automate_error_text(event) -> str | None:
    """Pull a human-readable error message out of an error-bearing event.

    Handles task:aborted (``reason``), the top-level ``error`` event and an
    unsuccessful ``complete`` (both nest the message under ``data.error.message``).
    """
    kind = getattr(event, "event", None)
    data = getattr(event, "data", None)
    if data is None:
        return None
    if kind == "task:aborted":
        reason = getattr(data, "reason", None)
        return f"task aborted: {_truncate(reason)}" if reason else "task aborted"
    err = getattr(data, "error", None)
    if err is not None:
        message = getattr(err, "message", None)
        if message:
            return _truncate(message)
    return None


def _compose_automate_result(
    final_answer: str | None, error_message: str | None, form_notes: list[str],
) -> str:
    parts: list[str] = []
    if final_answer:
        parts.append(final_answer)
    elif error_message:
        parts.append(f"[error: {error_message}]")
    if form_notes:
        parts.append("\n".join(form_notes))
    if not parts:
        return "[error: automate stream ended without a final answer]"
    return "\n\n".join(parts)


async def _handle_form_request(ctx, client, event, data: dict) -> str | None:
    """Answer (or cancel) an interactive form-data request.

    Returns a human-readable note to fold into the tool result, or None.
    """
    fd = event.data
    request_id = getattr(fd, "request_id", None)
    page_url = getattr(fd, "page_url", "") or ""
    form_description = getattr(fd, "form_description", "") or ""
    fields = list(getattr(fd, "fields", []) or [])

    if not request_id:
        return None

    # Narrate the form, never the values being entered.
    label_list = ", ".join(getattr(f, "label", "?") for f in fields) or "(no fields)"
    await ctx.publish(
        "tool_status", tool="tabstack_automate",
        message=_truncate(f"form on {page_url}: {label_list}"),
    )

    matched, missing = _match_fields(fields, data)

    if missing:
        await _safe_input(client, request_id, cancelled=True)
        labels = ", ".join(missing)
        return (
            f"[interactive: could not fill required field(s) on {page_url}: {labels}. "
            f"Re-run tabstack_automate with these values in `data` to continue.]"
        )

    approval = await request_confirmation(
        ctx,
        tool_name="tabstack_automate",
        command=f"submit form on {page_url}" if page_url else "submit web form",
        message=_format_form_confirmation(form_description, page_url, fields, matched),
    )
    if not approval.get("approved"):
        await _safe_input(client, request_id, cancelled=True)
        return f"[interactive: form submission on {page_url} was declined by the user.]"

    await _safe_input(client, request_id, fields=matched)
    return None


def _match_fields(fields, data: dict) -> tuple[list[dict], list[str]]:
    """Match requested form fields against the supplied data dict.

    Matching is case-insensitive on the field label (with an exact-key fallback).
    Returns ``(matched, missing)`` where ``matched`` is a list of ``{ref, value}``
    pairs and ``missing`` lists the labels of unmatched *required* fields.
    """
    lookup = {str(k).strip().lower(): v for k, v in (data or {}).items()}
    matched: list[dict] = []
    missing: list[str] = []
    for f in fields:
        label = getattr(f, "label", "") or ""
        ref = getattr(f, "ref", None)
        required = bool(getattr(f, "required", False))
        value = lookup.get(label.strip().lower())
        if value is None or ref is None:
            if required:
                missing.append(label or (ref or "?"))
            continue
        matched.append({"ref": ref, "value": str(value)})
    return matched, missing


def _format_form_confirmation(
    form_description: str, page_url: str, fields, matched: list[dict],
) -> str:
    """Render the confirmation body shown before submitting form data."""
    # Map ref -> field for label/type lookup when rendering submitted values.
    by_ref = {getattr(f, "ref", None): f for f in fields}
    lines = []
    if form_description:
        lines.append(form_description)
    if page_url:
        lines.append(f"Page: {page_url}")
    lines.append("--- submitting ---")
    for pair in matched:
        f = by_ref.get(pair["ref"])
        label = getattr(f, "label", pair["ref"]) if f else pair["ref"]
        field_type = getattr(f, "field_type", None) if f else None
        shown = "••••" if field_type == "password" else pair["value"]
        lines.append(f"{label}: {shown}")
    return "\n".join(lines)


async def _safe_input(client, request_id, *, fields=None, cancelled=False) -> None:
    """POST a form-input response.

    Swallows only an expired/consumed input window (HTTP 410 Gone) — the one error
    that's expected when the user is slow to confirm. Every other failure
    (network, auth, 5xx) propagates so it surfaces as a tool error instead of
    leaving the run in an undefined state.
    """
    try:
        if cancelled:
            await client.agent.automate_input(request_id, cancelled=True)
        else:
            await client.agent.automate_input(request_id, fields=fields or [])
    except APIStatusError as e:
        if e.status_code == 410:
            log.info("automate_input window expired (request_id=%s)", request_id)
            return
        raise


# -- Research ---------------------------------------------------------------

async def tool_tabstack_research(ctx, query: str, mode: str = "balanced") -> ToolResult:
    """Search the web, analyze multiple sources, and synthesize an answer."""
    log.info(f"[tool:tabstack_research] query={query} mode={mode}")
    try:
        stream = await _get_client().agent.research(query=query, mode=mode)  # type: ignore[arg-type]

        final_answer: str | None = None
        error_message: str | None = None
        async for event in stream:
            kind = getattr(event, "event", None)
            data = getattr(event, "data", None)

            msg = getattr(data, "message", None) if data is not None else None
            if msg:
                log.info("[tool:tabstack_research] %s", msg)
                await ctx.publish("tool_status", tool="tabstack_research", message=msg)

            if kind == "complete" and data is not None:
                report = getattr(data, "report", None)
                if report:
                    final_answer = report
            elif kind == "error" and data is not None:
                # Research-failed event carries a human-readable message plus a
                # nested data.error.message; prefer the top-level one.
                err = getattr(data, "error", None)
                error_message = (
                    msg or (getattr(err, "message", None) if err else None) or error_message
                )

        if not final_answer:
            if error_message:
                return ToolResult(text=f"[error: {_truncate(error_message)}]")
            return ToolResult(text="[error: research stream ended without a final answer]")
        return ToolResult(
            text=final_answer,
            data={"query": query, "mode": mode, "size": len(final_answer.encode("utf-8"))},
        )
    except Exception as e:
        return ToolResult(text=f"[error: {e}]")


# -- Registry ---------------------------------------------------------------

TOOLS = {
    "tabstack_extract_markdown": tool_tabstack_extract_markdown,
    "tabstack_extract_json": tool_tabstack_extract_json,
    "tabstack_generate": tool_tabstack_generate,
    "tabstack_automate": tool_tabstack_automate,
    "tabstack_research": tool_tabstack_research,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "tabstack_extract_markdown",
            "description": "Read a web page or PDF and return its content as clean, readable Markdown. Best for articles, documentation, and PDFs. Prefer this over web_fetch for readable content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the web page or PDF to read",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tabstack_extract_json",
            "description": "Extract structured data from a web page or PDF using a JSON schema. Best for pulling specific fields like prices, product details, tables, or lists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the page or PDF to extract from",
                    },
                    "json_schema": {
                        "type": "object",
                        "description": "JSON Schema defining the structure of data to extract",
                    },
                },
                "required": ["url", "json_schema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tabstack_generate",
            "description": "Transform web page or PDF content into structured JSON using natural language instructions. Use for summaries, categorization, sentiment analysis, or reformatting content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the page or PDF to transform",
                    },
                    "json_schema": {
                        "type": "object",
                        "description": "JSON Schema defining the output structure",
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Natural language instructions for how to transform the content",
                    },
                },
                "required": ["url", "json_schema", "instructions"],
            },
        },
    },
    {
        "type": "function",
        # Interactive runs add a user-confirmation round-trip plus tabstack's form-input
        # window (up to ~2 min), which can exceed the default 180s tool timeout.
        "timeout": 300,
        "function": {
            "name": "tabstack_automate",
            "description": "Automate web tasks using natural language. Has its own built-in web search — great for quick lookups like addresses, hours, prices, or simple facts. Also handles browser interactions: clicking, navigating, filling forms. Prefer this over tabstack_research for simple questions that just need a quick search. Takes 30-120 seconds.\n\nINTERACTIVE FORM-FILL: To let the browser agent fill a form with personal data, set interactive=true and pass the values in `data` (keys should match the form field labels, e.g. {\"Email\": \"a@b.com\", \"Full name\": \"Ada\"}). Each submission is shown to the user for confirmation before personal data is sent. If a required field is missing from `data`, the request is cancelled and the missing field names are reported back — gather them and re-run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Natural language description of the browser task to perform",
                    },
                    "url": {
                        "type": "string",
                        "description": "Starting URL (optional — omit to let the browser search)",
                    },
                    "data": {
                        "type": "object",
                        "description": "Personal/contextual values for form filling, keyed by field label (e.g. {\"Email\": \"a@b.com\"}). Only used when interactive=true.",
                    },
                    "interactive": {
                        "type": "boolean",
                        "description": "Enable interactive form-fill: answer the agent's form-data requests from `data`, gated by user confirmation. Default false.",
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tabstack_research",
            "description": "Deep multi-source web research with synthesis and citations. Use ONLY for complex questions that need analysis of multiple sources: comparisons, fact-checking across sources, topic deep-dives. For simple lookups (addresses, hours, single facts), use tabstack_automate instead — it's faster and cheaper. Takes 60-120 seconds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The research question or topic",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["fast", "balanced"],
                        "description": "fast for quick answers, balanced (default) for deeper multi-source research",
                    },
                },
                "required": ["query"],
            },
        },
    },
]
