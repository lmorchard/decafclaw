"""Code-driven workflow engine spike — `research_brief` as imperative orchestrator.

The point of this spike: prove the Sophie pattern walks gather → draft → review →
publish end-to-end against `vertex-gemini-flash`, the model that has stalled
every prior PR #557 iteration.

The orchestrator is plain async code. Each phase is a function that calls the
LLM with a *forced* single-tool schema (the only thing the model can do is emit
the call) and returns the parsed args. State is threaded forward as return
values. The one routing decision (after `draft`: review vs. back-to-gather) is
also a structured call, not a hoped-for tool emission.

Throwaway. If this works, the pattern gets folded into the existing engine in a
follow-up — see docs/dev-sessions/2026-05-31-1223-code-driven-engine-spike/.
"""

import asyncio
import json
import logging
import re
from typing import Any

from decafclaw.llm import call_llm
from decafclaw.media import ToolResult

log = logging.getLogger(__name__)

MODEL = "vertex-gemini-flash"
MAX_GATHER_REVISITS = 1


# --- Structured-output helper -----------------------------------------------

async def _call_structured(
    ctx,
    *,
    system: str,
    user_msg: str,
    schema: dict,
    tool_name: str,
    description: str = "",
    retries: int = 1,
) -> dict:
    """Force a structured response by exposing a single tool the model must call.

    Returns the parsed tool arguments. Retries once with a stricter nudge if the
    model narrates instead of calling. After retries are exhausted, raises.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description or (
                "Submit the structured result for this step. "
                "You MUST call this — do not respond with prose."
            ),
            "parameters": schema,
        },
    }]
    last_error: str | None = None
    for attempt in range(retries + 1):
        result = await call_llm(
            ctx.config, messages, tools=tools, model_name=MODEL,
        )
        tool_calls = result.get("tool_calls") or []
        if tool_calls:
            args_raw = tool_calls[0].get("function", {}).get("arguments") or "{}"
            try:
                return json.loads(args_raw)
            except json.JSONDecodeError as e:
                last_error = f"invalid JSON in tool args: {e}; raw={args_raw[:200]!r}"
        else:
            last_error = (
                f"model emitted text instead of calling {tool_name!r}: "
                f"{(result.get('content') or '')[:200]!r}"
            )
        # Stricter retry — last attempt gets a nudge appended to the user msg.
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                user_msg + f"\n\nIMPORTANT: You MUST call the tool `{tool_name}` "
                f"now. Do not narrate. Emit only the function call."
            )},
        ]
    raise RuntimeError(
        f"structured call to {tool_name} failed after {retries + 1} attempts: "
        f"{last_error}"
    )


# --- Phase functions --------------------------------------------------------

async def _gather(
    ctx, topic: str, prior_sources: list | None = None, gap_reason: str = "",
) -> dict:
    system = (
        "You are the source-gathering phase of a multi-phase research-brief "
        "workflow. Identify 4-6 high-signal sources for the topic plus 3-5 "
        "key themes that emerge across them. Use sources you would plausibly "
        "expect to exist; name publishers (NYT, NIH, Wikipedia, etc.) rather "
        "than inventing URLs you can't verify."
    )
    user_msg = f"Topic: {topic}\n\nIdentify 4-6 sources and 3-5 themes."
    if prior_sources:
        prior = "\n".join(f"- {s.get('title', '?')}" for s in prior_sources)
        user_msg += (
            f"\n\nYou previously found these sources:\n{prior}\n\n"
            f"The drafting phase reported a gap: {gap_reason}\n\n"
            f"Return ADDITIONAL sources addressing the gap, combined with the "
            f"existing set."
        )
    schema = {
        "type": "object",
        "properties": {
            "sources": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "publisher": {"type": "string"},
                        "summary": {"type": "string"},
                    },
                    "required": ["title", "publisher", "summary"],
                },
            },
            "themes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["sources", "themes"],
    }
    return await _call_structured(
        ctx, system=system, user_msg=user_msg, schema=schema,
        tool_name="submit_gather",
    )


async def _draft(ctx, topic: str, gather: dict) -> dict:
    sources_text = "\n".join(
        f"- {s['title']} ({s['publisher']}): {s['summary']}"
        for s in gather["sources"]
    )
    themes_text = "\n".join(f"- {t}" for t in gather["themes"])
    system = (
        "You are the drafting phase of a multi-phase research-brief workflow. "
        "Compose a 250-400-word brief from the sources and themes provided. "
        "Structure: one-paragraph framing, 2-3 themed body sections, a short "
        "open-questions list."
    )
    user_msg = (
        f"Topic: {topic}\n\nSources:\n{sources_text}\n\n"
        f"Key themes:\n{themes_text}\n\nWrite the brief now."
    )
    schema = {
        "type": "object",
        "properties": {
            "framing": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["heading", "body"],
                },
            },
            "open_questions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["framing", "sections", "open_questions"],
    }
    result = await _call_structured(
        ctx, system=system, user_msg=user_msg, schema=schema,
        tool_name="submit_draft",
    )
    # Render a flat `body` string for downstream phases / display.
    lines = [result["framing"], ""]
    for sec in result["sections"]:
        lines.append(f"## {sec['heading']}\n\n{sec['body']}\n")
    lines.append("## Open questions\n")
    for q in result["open_questions"]:
        lines.append(f"- {q}")
    result["body"] = "\n".join(lines).strip()
    return result


async def _draft_route(ctx, topic: str, draft: dict, gather: dict) -> dict:
    system = (
        "You are the routing-decision step after the drafting phase. Decide "
        "whether the draft is ready for review or whether the sources were "
        "too thin and we should gather more first. Default to 'review' unless "
        "there is a concrete, named gap that additional sources would close."
    )
    user_msg = (
        f"Topic: {topic}\n\nDraft framing: {draft['framing'][:400]}\n\n"
        f"Source count: {len(gather['sources'])}\n\n"
        f"Decide the next step: 'review' (default) or 'gather' (only if the "
        f"draft revealed a concrete missing-source gap)."
    )
    schema = {
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["review", "gather"]},
            "reason": {"type": "string"},
        },
        "required": ["target", "reason"],
    }
    return await _call_structured(
        ctx, system=system, user_msg=user_msg, schema=schema,
        tool_name="submit_route_decision",
    )


async def _review(ctx, topic: str, draft: dict) -> dict:
    system = (
        "You are the review phase. Critique the draft briefly: name one "
        "strength, one weakness, give a 1-sentence summary, and decide whether "
        "it is ready to publish."
    )
    user_msg = f"Topic: {topic}\n\nDraft:\n{draft['body']}\n\nReview the draft."
    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "strength": {"type": "string"},
            "weakness": {"type": "string"},
            "ready_to_publish": {"type": "boolean"},
        },
        "required": ["summary", "strength", "weakness", "ready_to_publish"],
    }
    return await _call_structured(
        ctx, system=system, user_msg=user_msg, schema=schema,
        tool_name="submit_review",
    )


def _publish(ctx, topic: str, draft: dict, review: dict) -> dict:
    """Pure-Python publish — write a file to the workspace. No LLM call."""
    slug = re.sub(r"[^a-z0-9-]+", "-", topic.lower()).strip("-")[:60] or "untitled"
    path = ctx.config.workspace_path / "spike_briefs" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"---\n"
        f"topic: {topic}\n"
        f"source: spike_research_brief\n"
        f"ready_to_publish: {review['ready_to_publish']}\n"
        f"---\n\n"
        f"# {topic}\n\n"
        f"{draft['body']}\n\n"
        f"---\n\n## Review\n\n"
        f"**Summary**: {review['summary']}\n\n"
        f"**Strength**: {review['strength']}\n\n"
        f"**Weakness**: {review['weakness']}\n"
    )
    path.write_text(content)
    return {"path": str(path.relative_to(ctx.config.workspace_path))}


# --- Orchestrator tool ------------------------------------------------------

def _check_cancel(ctx) -> None:
    if ctx.cancelled is not None and ctx.cancelled.is_set():
        raise asyncio.CancelledError("user interrupted")


async def _status(ctx, msg: str) -> None:
    log.info("[spike_brief] %s", msg)
    await ctx.publish("tool_status", tool="spike_brief_run", message=msg)


async def tool_spike_brief_run(ctx, topic: str) -> ToolResult:
    """Run the code-driven research_brief orchestrator end-to-end."""
    topic = (topic or "").strip()
    if not topic:
        return ToolResult(text="[error: spike_brief_run requires a non-empty `topic`]")

    transcript: list[dict[str, Any]] = []
    await _status(ctx, f"starting orchestrator (topic={topic!r}, model={MODEL})")

    try:
        # Phase 1: gather
        await _status(ctx, "[phase: gather] researching sources...")
        gather = await _gather(ctx, topic)
        transcript.append({
            "phase": "gather",
            "detail": f"{len(gather['sources'])} sources, {len(gather['themes'])} themes",
        })
        _check_cancel(ctx)

        # Phase 2: draft (with bounded back-to-gather loop)
        draft: dict | None = None
        for revisit in range(MAX_GATHER_REVISITS + 1):
            await _status(ctx, f"[phase: draft] writing brief (attempt {revisit + 1})...")
            draft = await _draft(ctx, topic, gather)
            _check_cancel(ctx)

            await _status(ctx, "[phase: draft → route] choosing next step...")
            route = await _draft_route(ctx, topic, draft, gather)
            transcript.append({
                "phase": "draft",
                "detail": (
                    f"attempt {revisit + 1}; route → {route['target']} "
                    f"(reason: {route['reason'][:80]})"
                ),
            })
            _check_cancel(ctx)

            if route["target"] == "review":
                break
            if revisit >= MAX_GATHER_REVISITS:
                transcript.append({
                    "phase": "draft",
                    "detail": "hit gather-revisit cap; proceeding to review",
                })
                break
            await _status(ctx, "[phase: gather (revisit)] fetching more sources...")
            gather = await _gather(
                ctx, topic,
                prior_sources=gather["sources"],
                gap_reason=route["reason"],
            )
            transcript.append({
                "phase": "gather-revisit",
                "detail": f"{len(gather['sources'])} sources after gap-driven revisit",
            })
            _check_cancel(ctx)

        assert draft is not None  # the loop runs at least once

        # Phase 3: review
        await _status(ctx, "[phase: review] critiquing draft...")
        review = await _review(ctx, topic, draft)
        transcript.append({"phase": "review", "detail": review["summary"][:120]})
        _check_cancel(ctx)

        # Phase 4: publish
        await _status(ctx, "[phase: publish] writing to workspace...")
        pub = _publish(ctx, topic, draft, review)
        transcript.append({"phase": "publish", "detail": f"wrote {pub['path']}"})

    except asyncio.CancelledError:
        await _status(ctx, "orchestrator cancelled by user")
        lines = ["# Spike: research_brief — **CANCELLED**", "", "Transcript so far:"]
        for t in transcript:
            lines.append(f"- **{t['phase']}**: {t['detail']}")
        return ToolResult(text="\n".join(lines), end_turn=True)

    await _status(ctx, f"complete — published to {pub['path']}")
    lines = ["# Spike: research_brief — **walked end-to-end**", "", "**Transcript:**"]
    for t in transcript:
        lines.append(f"- **{t['phase']}**: {t['detail']}")
    lines.extend([
        "",
        "---",
        "",
        "**Final brief:**",
        "",
        draft["body"],
        "",
        f"_Written to_ `{pub['path']}`",
    ])
    return ToolResult(text="\n".join(lines), end_turn=True)


# --- Skill registration -----------------------------------------------------

TOOLS = {
    "spike_brief_run": tool_spike_brief_run,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "spike_brief_run",
            "description": (
                "Run the code-driven research_brief workflow orchestrator end-to-end "
                "on the given topic. The tool runs all four phases (gather, draft, "
                "review, publish) internally — do not call any other tool between "
                "phases. Returns the phase transcript plus the final brief."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The research topic for the brief.",
                    },
                },
                "required": ["topic"],
            },
        },
    },
]
