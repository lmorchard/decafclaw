from types import SimpleNamespace

import pytest

import decafclaw.workflow.workflows  # noqa: F401 — registers interview
from decafclaw.workflow.engine import run_workflow
from decafclaw.workflow.journal import Journal, fingerprint
from decafclaw.workflow.registry import get_workflow


def _ctx(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id="convI")


@pytest.mark.asyncio
async def test_interview_suspends_for_topic_first(tmp_path):
    spec = get_workflow("interview")
    assert spec is not None
    outcome = await run_workflow(_ctx(tmp_path), spec.fn,
                                 Journal(workflow_name="interview"))
    assert outcome.status == "suspended"
    assert "about" in outcome.suspend.prompt.lower()


@pytest.mark.asyncio
async def test_interview_replays_to_artifact(tmp_path):
    """Seed a full journal (topic + one Q/A + done + synth) → pure replay,
    no LLM, reaches the artifact."""
    spec = get_workflow("interview")
    j = Journal(workflow_name="interview")

    def fp_user(prompt):
        return fingerprint("user_input", {"prompt": prompt, "choices": None})

    def fp_llm(prompt, schema, system):
        return fingerprint("llm_call",
                           {"prompt": prompt, "schema": schema, "system": system})

    from decafclaw.workflow.workflows.interview import (
        _ARTIFACT_SCHEMA,
        _DECISION_SCHEMA,
        _SYS_ASK,
        _SYS_SYNTH,
        _ask_prompt,
        _synth_prompt,
    )
    j.append((0,), "user_input", fp_user("What should this interview be about?"),
             "tide pools")
    q1_prompt = _ask_prompt("tide pools", [])
    j.append((1,), "llm_call", fp_llm(q1_prompt, _DECISION_SCHEMA, _SYS_ASK),
             {"done": False, "question": "What draws you to them?"})
    j.append((2,), "user_input", fp_user("What draws you to them?"), "the creatures")
    q2_prompt = _ask_prompt(
        "tide pools", [{"q": "What draws you to them?", "a": "the creatures"}])
    j.append((3,), "llm_call", fp_llm(q2_prompt, _DECISION_SCHEMA, _SYS_ASK),
             {"done": True, "question": ""})
    synth_prompt = _synth_prompt(
        "tide pools", [{"q": "What draws you to them?", "a": "the creatures"}])
    j.append((4,), "llm_call", fp_llm(synth_prompt, _ARTIFACT_SCHEMA, _SYS_SYNTH),
             {"title": "Tide Pools", "body": "..."})

    outcome = await run_workflow(_ctx(tmp_path), spec.fn, j)
    assert outcome.status == "done"
    assert outcome.result["title"] == "Tide Pools"
