"""Tabstack tools — web browsing, extraction, research, and automation."""

import json
import logging
from tabstack import Tabstack

log = logging.getLogger(__name__)

# Initialized once from config via init_tabstack()
_client: Tabstack | None = None


def init_tabstack(api_key: str, api_url: str | None = None):
    """Initialize the Tabstack client. Call once at startup."""
    global _client
    kwargs = {"api_key": api_key}
    if api_url:
        kwargs["base_url"] = api_url
    _client = Tabstack(**kwargs)
    log.info(f"Tabstack client initialized (url={api_url or 'default'})")


def _get_client() -> Tabstack:
    if _client is None:
        raise RuntimeError("Tabstack not initialized — is TABSTACK_API_KEY set?")
    return _client


# -- Tool implementations ---------------------------------------------------

def tool_tabstack_extract_markdown(url: str) -> str:
    """Extract clean Markdown from a web page or PDF."""
    log.info(f"[tool:tabstack_extract_markdown] {url}")
    try:
        result = _get_client().extract.markdown(url=url)
        return result.content
    except Exception as e:
        return f"[error: {e}]"


def tool_tabstack_extract_json(url: str, json_schema: dict) -> str:
    """Extract structured JSON data from a web page or PDF."""
    log.info(f"[tool:tabstack_extract_json] {url}")
    try:
        result = _get_client().extract.json(url=url, json_schema=json_schema)
        return json.dumps(result.data, indent=2)
    except Exception as e:
        return f"[error: {e}]"


def tool_tabstack_generate(url: str, json_schema: dict, instructions: str) -> str:
    """Transform web/PDF content into structured JSON using LLM instructions."""
    log.info(f"[tool:tabstack_generate] {url}")
    try:
        result = _get_client().generate.json(
            url=url, json_schema=json_schema, instructions=instructions
        )
        return json.dumps(result.data, indent=2)
    except Exception as e:
        return f"[error: {e}]"


def tool_tabstack_automate(task: str, url: str | None = None) -> str:
    """Run a multi-step browser automation task."""
    log.info(f"[tool:tabstack_automate] task={task} url={url}")
    try:
        kwargs = {"task": task}
        if url:
            kwargs["url"] = url
        stream = _get_client().agent.automate(**kwargs)

        final_answer = None
        for event in stream:
            _log_stream_event("automate", event)

            # SDK v2: fields are directly on the event object
            answer = _get_field(event, "finalAnswer") or _get_report(event)
            if answer:
                final_answer = answer

        return final_answer or "[error: automate stream ended without a final answer]"
    except Exception as e:
        return f"[error: {e}]"


def tool_tabstack_research(query: str, mode: str = "balanced") -> str:
    """Search the web, analyze multiple sources, and synthesize an answer."""
    log.info(f"[tool:tabstack_research] query={query} mode={mode}")
    try:
        stream = _get_client().agent.research(query=query, mode=mode)

        final_answer = None
        for event in stream:
            _log_stream_event("research", event)

            # SDK v2: the report is in metadata.report on the final event
            report = _get_report(event)
            if report:
                final_answer = report

        return final_answer or "[error: research stream ended without a final answer]"
    except Exception as e:
        return f"[error: {e}]"


# -- Helpers ----------------------------------------------------------------

def _get_field(obj, key):
    """Get a field from an object, handling both dict and attribute access."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _get_report(event):
    """Extract the report/answer from a stream event.

    SDK v2 puts the report in metadata.report on the final event.
    """
    # Direct report field
    report = _get_field(event, "report")
    if report:
        return report

    # Nested in metadata
    metadata = _get_field(event, "metadata")
    if metadata:
        report = _get_field(metadata, "report")
        if report:
            return report

    # finalAnswer (automate)
    return _get_field(event, "finalAnswer")


def _log_stream_event(prefix, event):
    """Log a streaming event for debugging."""
    msg = _get_field(event, "message")
    if msg:
        log.info(f"[tool:{prefix}] {msg}")


# -- Registry ---------------------------------------------------------------

TABSTACK_TOOLS = {
    "tabstack_extract_markdown": tool_tabstack_extract_markdown,
    "tabstack_extract_json": tool_tabstack_extract_json,
    "tabstack_generate": tool_tabstack_generate,
    "tabstack_automate": tool_tabstack_automate,
    "tabstack_research": tool_tabstack_research,
}

TABSTACK_TOOL_DEFINITIONS = [
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
        "function": {
            "name": "tabstack_automate",
            "description": "Automate multi-step browser interactions using natural language. Can click, navigate, fill forms, and extract data across pages. Use when you need real browser interaction, not just page reading. Takes 30-120 seconds.",
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
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tabstack_research",
            "description": "Search the web, analyze multiple sources, and synthesize a comprehensive answer with citations. Best for complex questions, fact-checking, comparisons, and topic deep-dives. Takes 60-120 seconds.",
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
