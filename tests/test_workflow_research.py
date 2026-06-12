"""Tests for the /research hero workflow (Phase 7).

The orchestrator exercises all four new primitives end-to-end:
  * wf.user_input (twice — topic, scope)
  * wf.llm_call    (the plan stage)
  * wf.parallel    (fan-out search queries via wf.tool_call inside thunks)
  * wf.pipeline    (per-result extract → summarize via sub.llm_call)
  * wf.subagent    (final synthesis)

These tests mock the engine boundaries (LLM caller, tool execution,
delegate.run_child_turn) so we can walk the orchestrator deterministically
without touching real model/search providers. Phase 8's live smoke covers
the real wiring.
"""
from unittest.mock import AsyncMock, patch

import pytest

import decafclaw.workflow.workflows  # noqa: F401 — registers research
from decafclaw.media import ToolResult
from decafclaw.workflow.engine import run_workflow
from decafclaw.workflow.journal import Journal, fingerprint
from decafclaw.workflow.registry import get_workflow

# --- Helpers ---------------------------------------------------------------

def _fp_user(prompt):
    return fingerprint("user_input", {"prompt": prompt, "choices": None})


def _fp_llm(prompt, schema, system):
    return fingerprint(
        "llm_call", {"prompt": prompt, "schema": schema, "system": system})


def _fp_tool(name, args):
    return fingerprint("tool_call", {"name": name, "args": args})


def _fp_parallel(count):
    return fingerprint("parallel", {"count": count})


def _fp_pipeline(items, stage_count):
    return fingerprint(
        "pipeline", {"items": items, "stage_count": stage_count})


# --- Tests -----------------------------------------------------------------

def test_research_registers_as_workflow():
    """The @workflow('research') decorator side-effects the registry on
    import; this is the contract the slash-command dispatcher depends on."""
    spec = get_workflow("research")
    assert spec is not None
    assert spec.name == "research"


@pytest.mark.asyncio
async def test_research_orchestrator_walks_to_completion(ctx):
    """Walk the whole orchestrator with mocked primitives. Verify it
    invokes each primitive in the right order, threads results through,
    and returns the synthesized report dict."""
    from decafclaw.workflow.workflows.research import (
        _PLAN_SCHEMA,
        _REPORT_SCHEMA,
        _SEARCH_TOOL,
        _SUMMARY_SCHEMA,
        _SYS_PLAN,
        _SYS_SUMMARIZE,
    )

    spec = get_workflow("research")
    assert spec is not None

    # Seed the journal with the two user_input answers so the orchestrator
    # walks past the suspend points. Everything downstream runs "live"
    # against mocked primitives.
    j = Journal(workflow_name="research")
    j.append((0,), "user_input",
             _fp_user("What topic should I research?"),
             "tide pool ecology")
    j.append((1,), "user_input",
             _fp_user("Any specific angle, audience, or constraint? "
                      "(Press enter for none.)"),
             "for a general audience")

    # Mock the LLM caller (plan stage + per-source summarize stages).
    plan_result = {"queries": ["q1: anemones", "q2: hermit crabs",
                                "q3: ochre stars"]}
    summarize_returns = [
        {"title": "Anemones", "key_points": ["sting", "symbiosis"]},
        {"title": "Hermit crabs", "key_points": ["shells", "moult"]},
        {"title": "Ochre stars", "key_points": ["keystone", "wasting"]},
    ]

    llm_calls: list[dict] = []
    summarize_idx = {"n": 0}

    async def fake_llm(ctx, **kw):
        llm_calls.append(kw)
        if kw["system"] == _SYS_PLAN:
            return plan_result
        if kw["system"] == _SYS_SUMMARIZE:
            r = summarize_returns[summarize_idx["n"]]
            summarize_idx["n"] += 1
            return r
        raise AssertionError(f"unexpected llm_call: system={kw['system']!r}")

    # Mock the tool dispatcher. Each call returns a fake search result.
    tool_calls: list[tuple[str, dict]] = []

    async def fake_tool(ctx, name, args):
        tool_calls.append((name, args))
        return ToolResult(text=f"Results for {args.get('query', '?')}",
                          data=None)

    # Mock the subagent (delegate.run_child_turn).
    final_report = {"title": "Tide pools",
                    "body": "# Tide pools\n\nSynthesized."}

    # ctx needs a workspace_path the journal can persist to.
    # The research orchestrator gates tool_call by ctx.tools.allowed; allow
    # the chosen search tool through.
    ctx.tools.allowed = {_SEARCH_TOOL}

    with patch("decafclaw.workflow.handle.execute_tool", new=fake_tool), \
         patch(
             "decafclaw.tools.delegate.run_child_turn",
             new_callable=AsyncMock,
             return_value=("ignored text", final_report),
         ) as mock_child:
        outcome = await run_workflow(
            ctx, spec.fn, j, llm_caller=fake_llm)

    # The orchestrator returns the structured report (subagent with schema
    # returns the dict, not the text).
    assert outcome.status == "done", f"got {outcome.status}: {outcome.error}"
    assert outcome.result == final_report

    # Primitives invoked in expected order:
    #   1 plan llm_call + 3 summarize llm_calls = 4 total.
    assert len(llm_calls) == 4
    assert llm_calls[0]["schema"] == _PLAN_SCHEMA
    assert llm_calls[0]["system"] == _SYS_PLAN
    for i in range(1, 4):
        assert llm_calls[i]["schema"] == _SUMMARY_SCHEMA
        assert llm_calls[i]["system"] == _SYS_SUMMARIZE

    # One tool_call per query.
    assert len(tool_calls) == 3
    for (name, args), q in zip(tool_calls, plan_result["queries"]):
        assert name == _SEARCH_TOOL
        assert args.get("query") == q

    # Subagent called once with the report schema.
    mock_child.assert_called_once()
    _args, kwargs = mock_child.call_args
    assert kwargs["return_schema"] == _REPORT_SCHEMA


@pytest.mark.asyncio
async def test_research_fails_fast_when_search_tool_returns_all_errors(ctx):
    """When every search-tool call returns an `[error: ...]`-shaped result
    (e.g., skill-bundled tool unavailable in workflow context — smoke
    Finding 1), the orchestrator must raise BEFORE the pipeline wastes
    tokens summarizing error text and the subagent runs on garbage."""
    from decafclaw.workflow.workflows.research import _SEARCH_TOOL

    spec = get_workflow("research")
    assert spec is not None

    j = Journal(workflow_name="research")
    j.append((0,), "user_input",
             _fp_user("What topic should I research?"), "topic")
    j.append((1,), "user_input",
             _fp_user("Any specific angle, audience, or constraint? "
                      "(Press enter for none.)"), "scope")

    plan_result = {"queries": ["q1", "q2"]}

    async def fake_llm(ctx, **kw):
        if kw["schema"]["properties"].get("queries"):
            return plan_result
        # Summarize must NOT run.
        raise AssertionError("summarize llm_call must not run after fail-fast")

    async def error_tool(ctx, name, args):
        return ToolResult(
            text=f"[error: unknown tool {name!r}]", data=None)

    ctx.tools.allowed = {_SEARCH_TOOL}

    with patch("decafclaw.workflow.handle.execute_tool", new=error_tool), \
         patch(
             "decafclaw.tools.delegate.run_child_turn",
             new_callable=AsyncMock,
         ) as mock_child:
        outcome = await run_workflow(ctx, spec.fn, j, llm_caller=fake_llm)

    assert outcome.status == "error"
    assert "tool likely unavailable" in outcome.error.lower() or \
           "all" in outcome.error.lower()
    mock_child.assert_not_called()


@pytest.mark.asyncio
async def test_research_orchestrator_resumes_from_journal(ctx):
    """Pre-populate the journal through the pipeline stage. The subagent
    call is the first live boundary. user_input / llm_call / tool_call /
    parallel / pipeline should all replay from the journal — if any of
    them runs live, the sabotage mocks blow up."""
    from decafclaw.workflow.workflows.research import (
        _PLAN_SCHEMA,
        _REPORT_SCHEMA,
        _SEARCH_TOOL,
        _SUMMARY_SCHEMA,
        _SYS_PLAN,
        _SYS_SUMMARIZE,
        _research_plan_prompt,
        _summarize_prompt,
        _synth_prompt,
    )

    ctx.tools.allowed = {_SEARCH_TOOL}

    topic = "kelp forests"
    scope = "for a marine biology newsletter"
    queries = ["q1: bull kelp", "q2: sea urchins"]
    search_text_template = "Results for {q}"
    summaries = [
        {"title": "Bull kelp", "key_points": ["fast growth", "habitat"]},
        {"title": "Sea urchins", "key_points": ["barrens", "grazing"]},
    ]
    report = {"title": "Kelp forests", "body": "# Kelp..."}

    j = Journal(workflow_name="research")
    # (0,) user_input — topic
    j.append((0,), "user_input",
             _fp_user("What topic should I research?"), topic)
    # (1,) user_input — scope
    j.append((1,), "user_input",
             _fp_user("Any specific angle, audience, or constraint? "
                      "(Press enter for none.)"), scope)
    # (2,) llm_call — plan
    plan_prompt = _research_plan_prompt(topic, scope)
    j.append((2,), "llm_call",
             _fp_llm(plan_prompt, _PLAN_SCHEMA, _SYS_PLAN),
             {"queries": queries})

    # (3,) parallel — search fan-out. Per-thunk child entries:
    #   (3, i, 0) tool_call — search for queries[i]
    search_results = []
    for i, q in enumerate(queries):
        text = search_text_template.format(q=q)
        result_dict = {"text": text, "data": None}
        j.append((3, i, 0), "tool_call",
                 _fp_tool(_SEARCH_TOOL, {"query": q}), result_dict)
        search_results.append(result_dict)
    j.append((3,), "parallel", _fp_parallel(len(queries)), search_results)

    # (4,) pipeline — per-result extract → summarize. Per-item child entries:
    #   stage 1 (extract) has no journaled side-effect (it's a pure dict ->
    #   str), so only the summarize llm_call is journaled at (4, i, 0).
    for i, q in enumerate(queries):
        extracted = search_text_template.format(q=q)
        prompt = _summarize_prompt(extracted)
        j.append((4, i, 0), "llm_call",
                 _fp_llm(prompt, _SUMMARY_SCHEMA, _SYS_SUMMARIZE),
                 summaries[i])
    j.append((4,), "pipeline",
             _fp_pipeline(search_results, 2), summaries)

    # (5,) subagent — the first live boundary. NOT pre-populated.

    # Sabotage mocks: user_input never gets called (raises automatically
    # via WorkflowSuspended if the cache misses), llm_call / tool_call
    # would blow up if invoked live.
    async def boom_llm(ctx, **kw):
        raise AssertionError(
            f"llm_call MUST NOT run during journal-driven replay: {kw}")

    async def boom_tool(ctx, name, args):
        raise AssertionError(
            f"tool_call MUST NOT run during journal-driven replay: "
            f"{name} {args}")

    # The subagent IS live: that's what we're verifying gets reached.
    with patch("decafclaw.workflow.handle.execute_tool", new=boom_tool), \
         patch(
             "decafclaw.tools.delegate.run_child_turn",
             new_callable=AsyncMock,
             return_value=("ignored", report),
         ) as mock_child:
        spec = get_workflow("research")
        assert spec is not None
        outcome = await run_workflow(
            ctx, spec.fn, j, llm_caller=boom_llm)

    assert outcome.status == "done", f"got {outcome.status}: {outcome.error}"
    assert outcome.result == report

    # Subagent saw the assembled prompt with topic + scope + rendered
    # summaries.
    mock_child.assert_called_once()
    _args, kwargs = mock_child.call_args
    expected_prompt = _synth_prompt(topic, scope, summaries)
    assert kwargs["task"] == expected_prompt
    assert kwargs["return_schema"] == _REPORT_SCHEMA


@pytest.mark.asyncio
async def test_research_first_suspend_is_topic_question(ctx):
    """A fresh journal suspends immediately on the topic user_input — the
    same shape as /interview's first-suspend smoke. Confirms the
    orchestrator is reachable through run_workflow and the registry."""
    spec = get_workflow("research")
    assert spec is not None

    outcome = await run_workflow(
        ctx, spec.fn, Journal(workflow_name="research"))
    assert outcome.status == "suspended"
    assert outcome.suspend is not None
    assert "topic" in outcome.suspend.prompt.lower()
