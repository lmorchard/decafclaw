"""Unit-level tests for the research_brief workflow definition.

These tests load the actual workflow.yaml from the bundled skill directory
and verify it parses without errors, all step IDs resolve, and the
critique cycle's back-edge to outline is correctly represented.

No LLM calls are made; this validates the workflow graph only.
"""

from pathlib import Path

import pytest

from decafclaw.skills.research_brief.tools import count_draft_words
from decafclaw.workflow.jinja_env import render_template
from decafclaw.workflow.loader import load_workflow
from decafclaw.workflow.types import StepKind, WorkflowDef  # noqa: F401

RESEARCH_BRIEF_DIR = (
    Path(__file__).parent.parent.parent
    / "src" / "decafclaw" / "skills" / "research_brief"
)


@pytest.fixture
def research_brief_wf() -> WorkflowDef:
    return load_workflow(RESEARCH_BRIEF_DIR)


def test_workflow_loads_without_error(research_brief_wf):
    """Workflow parses cleanly from disk."""
    assert research_brief_wf is not None
    assert research_brief_wf.name == "research_brief"


def test_initial_step_is_gather(research_brief_wf):
    assert research_brief_wf.initial_step == "gather"


def test_all_expected_steps_present(research_brief_wf):
    step_ids = set(research_brief_wf.steps_by_id)
    expected = {"gather", "read_sources", "outline", "draft", "word_count",
                "shorten", "critique", "publish"}
    assert expected == step_ids


def test_step_kinds(research_brief_wf):
    by_id = research_brief_wf.steps_by_id
    assert by_id["gather"].kind == StepKind.SUBAGENT
    assert by_id["read_sources"].kind == StepKind.TOOL_CALL
    assert by_id["outline"].kind == StepKind.LLM_CALL
    assert by_id["draft"].kind == StepKind.LLM_CALL
    assert by_id["word_count"].kind == StepKind.PYTHON
    assert by_id["shorten"].kind == StepKind.LLM_CALL
    assert by_id["critique"].kind == StepKind.ROUTE
    assert by_id["publish"].kind == StepKind.TOOL_CALL


def test_word_count_fn_configured(research_brief_wf):
    step = research_brief_wf.steps_by_id["word_count"]
    assert step.config["fn"] == "count_draft_words"


def test_word_count_conditional_edges(research_brief_wf):
    """word_count: count > 800 → shorten; default → critique."""
    step = research_brief_wf.steps_by_id["word_count"]
    assert len(step.next_edges) == 2
    cond_edge = step.next_edges[0]
    assert cond_edge.to == "shorten"
    assert "800" in cond_edge.if_expr
    default_edge = step.next_edges[1]
    assert default_edge.to == "critique"
    assert default_edge.if_expr == ""


def test_critique_choices(research_brief_wf):
    """critique route step has approve/revise/abort choices."""
    step = research_brief_wf.steps_by_id["critique"]
    choice_ids = {c.id for c in step.choices}
    assert choice_ids == {"approve", "revise", "abort"}


def test_critique_revise_back_edge_to_outline(research_brief_wf):
    """The revise choice points back to outline (back-edge / cycle)."""
    step = research_brief_wf.steps_by_id["critique"]
    revise = next(c for c in step.choices if c.id == "revise")
    assert revise.to == "outline"


def test_critique_approve_goes_to_publish(research_brief_wf):
    step = research_brief_wf.steps_by_id["critique"]
    approve = next(c for c in step.choices if c.id == "approve")
    assert approve.to == "publish"


def test_critique_abort_is_terminal(research_brief_wf):
    """abort choice has empty to='' → terminal."""
    step = research_brief_wf.steps_by_id["critique"]
    abort = next(c for c in step.choices if c.id == "abort")
    assert abort.to == ""


def test_publish_is_terminal(research_brief_wf):
    """publish step has no next_edges → terminal."""
    step = research_brief_wf.steps_by_id["publish"]
    assert step.next_edges == ()


def test_publish_uses_workflow_artifact_write(research_brief_wf):
    step = research_brief_wf.steps_by_id["publish"]
    assert step.config["tool"] == "workflow_artifact_write"


def test_all_to_refs_resolve(research_brief_wf):
    """Validate that all edge and choice targets reference valid step ids or ''.

    The loader does this at load time, so this is effectively a re-check
    confirming the fixture loaded cleanly (no ValueError was raised).
    """
    step_ids = set(research_brief_wf.steps_by_id)
    for step in research_brief_wf.steps:
        for edge in step.next_edges:
            assert edge.to == "" or edge.to in step_ids, (
                f"Step {step.id!r}: edge to={edge.to!r} not in {step_ids}"
            )
        for choice in step.choices:
            assert choice.to == "" or choice.to in step_ids, (
                f"Step {step.id!r}: choice {choice.id!r} to={choice.to!r} not in {step_ids}"
            )


# --- B1: publish template tests ---

def _get_publish_content_template(research_brief_wf) -> str:
    """Extract the content arg template from the publish step."""
    step = research_brief_wf.steps_by_id["publish"]
    return step.config["args"]["content"]


def test_publish_template_prefers_shorten_when_present(research_brief_wf):
    """When state.shorten exists, the publish content must use shorten.body."""
    template = _get_publish_content_template(research_brief_wf)
    state = {
        "outline": {"title": "Test Topic", "bullets": ["point one"]},
        "draft": {"body": "long draft body"},
        "shorten": {"body": "short body"},
    }
    rendered = render_template(template, state)
    assert "short body" in rendered
    assert "long draft body" not in rendered


def test_publish_template_uses_draft_when_no_shorten(research_brief_wf):
    """When state.shorten is absent, publish content must use draft.body."""
    template = _get_publish_content_template(research_brief_wf)
    state = {
        "outline": {"title": "Test Topic", "bullets": ["point one"]},
        "draft": {"body": "long draft body"},
    }
    rendered = render_template(template, state)
    assert "long draft body" in rendered


# --- B2: count_draft_words tests ---

def test_count_draft_words_uses_draft_body():
    """count_draft_words counts words in state.draft.body."""
    state = {"draft": {"body": "one two three four five"}}
    result = count_draft_words(state)
    assert result == {"count": 5}


def test_count_draft_words_ignores_state_shorten():
    """Even when state.shorten exists (prior cycle), count_draft_words counts state.draft.body."""
    state = {
        "draft": {"body": "new draft with six words here"},
        "shorten": {"body": "old short body"},
    }
    result = count_draft_words(state)
    # Should count the draft (6 words), not the shorten body (3 words)
    assert result == {"count": 6}


def test_count_draft_words_empty_draft():
    """Empty draft body returns count 0."""
    state = {"draft": {"body": ""}}
    result = count_draft_words(state)
    assert result == {"count": 0}


def test_count_draft_words_missing_draft():
    """Missing draft key returns count 0."""
    result = count_draft_words({})
    assert result == {"count": 0}
