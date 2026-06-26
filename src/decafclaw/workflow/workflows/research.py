"""Research → report: the multi-primitive hero workflow.

User-invocable as /research (web UI). The orchestrator drives a focused
research sweep end-to-end, exercising all four Phase 1-6 primitives:

  * wf.user_input  — collect topic + scope from the user
  * wf.llm_call    — plan a small set of search queries (structured output)
  * wf.parallel    — fan out search queries via wf.tool_call inside thunks
  * wf.pipeline    — per-source extract → summarize (structured output)
  * wf.subagent    — final synthesis as a child agent turn with a schema

The chosen search tool is `tabstack_research`: it accepts a single `query`
string and synthesizes a markdown report. The orchestrator's pipeline
extract stage simply pulls `text` out of the tool_call result dict, so
swapping in a different fetch/search tool only requires changing the
constant + the per-thunk `query=` keyword.
"""
from ..registry import workflow

_SEARCH_TOOL = "tabstack_research"

_SYS_PLAN = (
    "You plan focused research sweeps. Given a topic and any scope notes, "
    "generate 3-5 search queries that together cover the topic without "
    "overlap. Each query should be specific enough to return a useful "
    "single-page result."
)
_SYS_SUMMARIZE = (
    "You write tight summaries of source material. Extract the 3-5 most "
    "important specific points from the given content. Avoid generalities."
)
_SYS_SYNTH = (
    "You synthesize a set of source summaries into a coherent written "
    "report. Cite sources by their titles when relevant. Markdown body."
)

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
            "description": "Search queries covering the topic.",
        },
    },
    "required": ["queries"],
}

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["title", "key_points"],
}

_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string", "description": "Markdown body."},
    },
    "required": ["title", "body"],
}


def _research_plan_prompt(topic: str, scope: str) -> str:
    lines = [f"Topic: {topic}"]
    if scope:
        lines.append(f"Scope / angle: {scope}")
    lines.append("")
    lines.append(
        "Generate 3-5 search queries that together cover this topic.")
    return "\n".join(lines)


def _summarize_prompt(source_text: str) -> str:
    # Cap the source to keep the summarize prompt comfortably under the
    # model's input budget. Per-source cap is enough for a single-page
    # research report; if a search tool ever returns much more, this is the
    # knob to revisit.
    capped = source_text[:8000]
    return (
        "Source content:\n\n"
        f"{capped}\n\n"
        "Summarize the 3-5 most important specific points."
    )


def _render_summary(s: dict) -> str:
    """Plain helper — NOT journaled. Pure transformation over already-
    journaled summary dicts, safe to re-run on every replay."""
    if isinstance(s, dict) and "title" in s and "key_points" in s:
        bullets = "\n".join(f"- {p}" for p in s.get("key_points", []))
        return f"## {s['title']}\n{bullets}"
    return str(s)


def _is_error_result(r) -> bool:
    """`wf.tool_call` returns `{"text", "data"}`; failed tools surface as
    text starting with `[error:` (the decafclaw tool-failure convention)."""
    return (isinstance(r, dict)
            and r.get("data") is None
            and isinstance(r.get("text"), str)
            and r["text"].startswith("[error:"))


def _synth_prompt(topic: str, scope: str, summaries: list[dict]) -> str:
    lines = [f"Topic: {topic}"]
    if scope:
        lines.append(f"Scope / angle: {scope}")
    lines.append("")
    lines.append("Source summaries:")
    lines.append("")
    for s in summaries:
        lines.append(_render_summary(s))
        lines.append("")
    lines.append("Synthesize a titled markdown report.")
    return "\n".join(lines)


def _make_search_thunk(query: str):
    """Build a thunk for `wf.parallel`. Plain function returning an async
    function (NOT `async def` returning the inner) — `wf.parallel` expects
    `Callable[[sub], Awaitable[...]]`, not a coroutine."""
    async def _thunk(sub):
        return await sub.tool_call(_SEARCH_TOOL, query=query)
    return _thunk


@workflow("research", requires_skills=("tabstack",))
async def research(wf):
    topic = await wf.user_input("What topic should I research?")
    scope = await wf.user_input(
        "Any specific angle, audience, or constraint? "
        "(Press enter for none.)")

    plan = await wf.llm_call(
        prompt=_research_plan_prompt(topic, scope),
        schema=_PLAN_SCHEMA,
        system=_SYS_PLAN,
    )
    queries: list[str] = plan["queries"]

    # Fan out the searches. Each thunk gets its own sub-handle from
    # wf.parallel; the tool_call inside lands at (outer, idx, 0).
    search_results = await wf.parallel(
        [_make_search_thunk(q) for q in queries])

    # Fail fast if the search tool isn't actually available in this
    # workflow context (skill tools aren't reachable from workflow turns
    # in v1 — see #574 smoke Finding 1). Without this check the pipeline
    # would feed `[error: ...]` text into the summarizer, wasting tokens
    # and producing low-quality output.
    if all(_is_error_result(r) for r in search_results):
        sample = search_results[0].get("text", "") if search_results else ""
        raise RuntimeError(
            f"/research: all {len(queries)} searches via {_SEARCH_TOOL!r} "
            f"failed — tool likely unavailable in this workflow context. "
            f"First error: {sample[:200]}")

    # Per-result extract → summarize. Stage 1 is a pure dict→str
    # transform (NOT journaled; safe to re-run on replay). Stage 2
    # journals through sub.llm_call.
    async def _extract_stage(prev, item, idx, sub):
        return prev.get("text", "") if isinstance(prev, dict) else str(prev)

    async def _summarize_stage(prev, item, idx, sub):
        return await sub.llm_call(
            prompt=_summarize_prompt(prev),
            schema=_SUMMARY_SCHEMA,
            system=_SYS_SUMMARIZE,
        )

    summaries = await wf.pipeline(
        search_results, _extract_stage, _summarize_stage)

    # Final synthesis as a child agent turn with structured output.
    return await wf.subagent(
        prompt=_synth_prompt(topic, scope, summaries),
        schema=_REPORT_SCHEMA,
    )
