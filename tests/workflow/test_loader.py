"""Tests for workflow loader — parse workflow.yaml + validate refs."""

from pathlib import Path

import pytest
import yaml

from decafclaw.workflow.loader import load_workflow
from decafclaw.workflow.types import StepKind


@pytest.fixture
def tmp_skill_dir(tmp_path):
    """Create a minimal skill directory with SKILL.md and workflow.yaml."""
    return tmp_path


def write_skill(skill_dir: Path, frontmatter: dict, workflow: dict) -> None:
    """Write SKILL.md and workflow.yaml to the skill dir."""
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(frontmatter.get("description", "A workflow skill."))
    (skill_dir / "SKILL.md").write_text("\n".join(fm_lines))
    (skill_dir / "workflow.yaml").write_text(yaml.dump(workflow))


def test_load_minimal_llm_call(tmp_skill_dir):
    write_skill(
        tmp_skill_dir,
        {"name": "hello", "description": "Hello workflow", "kind": "workflow"},
        {
            "initial-step": "greet",
            "steps": [
                {
                    "id": "greet",
                    "kind": "llm_call",
                    "prompt": "Say hello",
                    "schema": {"type": "object", "properties": {"greeting": {"type": "string"}}},
                }
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    assert wf.name == "hello"
    assert wf.initial_step == "greet"
    assert len(wf.steps) == 1
    step = wf.steps[0]
    assert step.id == "greet"
    assert step.kind == StepKind.LLM_CALL
    assert step.next_edges == ()


def test_load_with_next_step(tmp_skill_dir):
    write_skill(
        tmp_skill_dir,
        {"name": "two_step", "description": "Two steps", "kind": "workflow"},
        {
            "initial-step": "first",
            "steps": [
                {
                    "id": "first",
                    "kind": "llm_call",
                    "prompt": "Step one",
                    "schema": {"type": "object"},
                    "next": "second",
                },
                {
                    "id": "second",
                    "kind": "llm_call",
                    "prompt": "Step two",
                    "schema": {"type": "object"},
                },
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    first = wf.steps_by_id["first"]
    assert len(first.next_edges) == 1
    assert first.next_edges[0].to == "second"
    assert first.next_edges[0].if_expr == ""


def test_load_conditional_next(tmp_skill_dir):
    write_skill(
        tmp_skill_dir,
        {"name": "cond_wf", "description": "Conditional", "kind": "workflow"},
        {
            "initial-step": "decide",
            "steps": [
                {
                    "id": "decide",
                    "kind": "llm_call",
                    "prompt": "Decide",
                    "schema": {"type": "object"},
                    "next": [
                        {"if": "state.x > 0", "to": "positive"},
                        {"to": "negative"},
                    ],
                },
                {"id": "positive", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}},
                {"id": "negative", "kind": "llm_call", "prompt": "n", "schema": {"type": "object"}},
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    decide = wf.steps_by_id["decide"]
    assert len(decide.next_edges) == 2
    assert decide.next_edges[0].if_expr == "state.x > 0"
    assert decide.next_edges[0].to == "positive"
    assert decide.next_edges[1].if_expr == ""
    assert decide.next_edges[1].to == "negative"


def test_load_raises_on_invalid_to_ref(tmp_skill_dir):
    write_skill(
        tmp_skill_dir,
        {"name": "bad_wf", "description": "Bad ref", "kind": "workflow"},
        {
            "initial-step": "step1",
            "steps": [
                {
                    "id": "step1",
                    "kind": "llm_call",
                    "prompt": "p",
                    "schema": {"type": "object"},
                    "next": "nonexistent",
                },
            ],
        },
    )
    with pytest.raises(ValueError, match="nonexistent"):
        load_workflow(tmp_skill_dir)


def test_load_raises_on_invalid_initial_step(tmp_skill_dir):
    write_skill(
        tmp_skill_dir,
        {"name": "bad_wf2", "description": "Bad initial", "kind": "workflow"},
        {
            "initial-step": "missing",
            "steps": [
                {"id": "step1", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}},
            ],
        },
    )
    with pytest.raises(ValueError, match="initial-step"):
        load_workflow(tmp_skill_dir)


def test_load_warns_on_unreachable_step(tmp_skill_dir, caplog):
    write_skill(
        tmp_skill_dir,
        {"name": "unreachable_wf", "description": "Unreachable step", "kind": "workflow"},
        {
            "initial-step": "step1",
            "steps": [
                {"id": "step1", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}},
                {"id": "orphan", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}},
            ],
        },
    )
    import logging
    with caplog.at_level(logging.WARNING, logger="decafclaw.workflow.loader"):
        wf = load_workflow(tmp_skill_dir)
    unreachable_msgs = [r.message for r in caplog.records if "unreachable" in r.message.lower() or "orphan" in r.message.lower()]
    assert unreachable_msgs, f"Expected unreachable warning, got: {[r.message for r in caplog.records]}"
    assert wf is not None


def test_load_rejects_unknown_kind(tmp_skill_dir):
    """An unknown step kind raises ValueError at load time."""
    write_skill(
        tmp_skill_dir,
        {"name": "future_wf", "description": "Future kind", "kind": "workflow"},
        {
            "initial-step": "step1",
            "steps": [
                {"id": "step1", "kind": "future_kind"},
            ],
        },
    )
    with pytest.raises(ValueError, match="Unknown step kind"):
        load_workflow(tmp_skill_dir)


def test_load_user_input_missing_config_raises(tmp_skill_dir):
    """user_input step with no input: and no choices: raises ValueError."""
    write_skill(
        tmp_skill_dir,
        {"name": "ui_wf", "description": "user_input workflow", "kind": "workflow"},
        {
            "initial-step": "step1",
            "steps": [
                {"id": "step1", "kind": "user_input"},
            ],
        },
    )
    # user_input without input:text or choices: is invalid config
    with pytest.raises(ValueError, match="must have"):
        load_workflow(tmp_skill_dir)


def test_load_tool_call_step(tmp_skill_dir):
    """tool_call steps load and parse correctly (Phase 2)."""
    write_skill(
        tmp_skill_dir,
        {"name": "tool_wf", "description": "Tool call workflow", "kind": "workflow"},
        {
            "initial-step": "list_ws",
            "steps": [
                {
                    "id": "list_ws",
                    "kind": "tool_call",
                    "tool": "workspace_list",
                    "args": {"path": ""},
                },
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    assert len(wf.steps) == 1
    step = wf.steps[0]
    assert step.id == "list_ws"
    from decafclaw.workflow.types import StepKind
    assert step.kind == StepKind.TOOL_CALL
    assert step.config["tool"] == "workspace_list"
    assert step.config["args"] == {"path": ""}


def test_load_tool_call_missing_tool_raises(tmp_skill_dir):
    """tool_call step without 'tool' field raises ValueError."""
    write_skill(
        tmp_skill_dir,
        {"name": "bad_wf", "description": "Missing tool", "kind": "workflow"},
        {
            "initial-step": "step1",
            "steps": [
                {"id": "step1", "kind": "tool_call"},
            ],
        },
    )
    with pytest.raises(ValueError, match="missing required 'tool'"):
        load_workflow(tmp_skill_dir)


def test_load_subagent_step(tmp_skill_dir):
    """subagent steps load and parse correctly (Phase 3)."""
    write_skill(
        tmp_skill_dir,
        {"name": "subagent_wf", "description": "Subagent workflow", "kind": "workflow"},
        {
            "initial-step": "gather",
            "steps": [
                {
                    "id": "gather",
                    "kind": "subagent",
                    "prompt": "Gather sources on {{ state.topic | default('AI') }}",
                    "skill": "tabstack",
                    "tools": ["tabstack_research", "vault_write"],
                    "outputs": ["sources.md"],
                    "context-profile": {"memory-retrieval": "off"},
                },
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    assert len(wf.steps) == 1
    step = wf.steps[0]
    assert step.id == "gather"
    from decafclaw.workflow.types import StepKind
    assert step.kind == StepKind.SUBAGENT
    assert step.config["prompt"] == "Gather sources on {{ state.topic | default('AI') }}"
    assert step.config["skill"] == "tabstack"
    assert step.config["tools"] == ["tabstack_research", "vault_write"]
    assert step.config["outputs"] == ["sources.md"]
    assert step.config["context-profile"] == {"memory-retrieval": "off"}


def test_load_subagent_step_prompt_from(tmp_skill_dir):
    """subagent step with prompt-from reads the file content."""
    prompts_dir = tmp_skill_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "gather.md").write_text("Gather sources on the topic.")

    write_skill(
        tmp_skill_dir,
        {"name": "subagent_wf2", "description": "Subagent pf", "kind": "workflow"},
        {
            "initial-step": "gather",
            "steps": [
                {
                    "id": "gather",
                    "kind": "subagent",
                    "prompt-from": "gather.md",
                    "tools": [],
                    "outputs": [],
                },
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    step = wf.steps[0]
    assert step.config["prompt"] == "Gather sources on the topic."


def test_load_workflow_description_preserved_with_horizontal_rule(tmp_skill_dir):
    """Workflow description is correct even when SKILL.md body contains '---' (horizontal rule).

    Regression for the double-parse bug: previously loader.py split SKILL.md text on
    '---' which misparsed when the body contained a markdown horizontal rule.
    """
    # Write SKILL.md manually to include a horizontal rule in the body section
    skill_md = tmp_skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: hr_wf\n"
        "description: My HR workflow\n"
        "kind: workflow\n"
        "---\n"
        "\n"
        "This is the body.\n"
        "\n"
        "---\n"
        "\n"
        "A section after a horizontal rule.\n"
    )
    (tmp_skill_dir / "workflow.yaml").write_text(yaml.dump({
        "initial-step": "step1",
        "steps": [{"id": "step1", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}}],
    }))
    wf = load_workflow(tmp_skill_dir)
    assert wf.description == "My HR workflow"
    assert wf.name == "hr_wf"


def test_load_prompt_from(tmp_skill_dir):
    """Test that prompt-from: loads content from prompts/<name>.md."""
    prompts_dir = tmp_skill_dir / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "greet.md").write_text("This is the greeting prompt.")
    write_skill(
        tmp_skill_dir,
        {"name": "prompt_from_wf", "description": "Prompt from", "kind": "workflow"},
        {
            "initial-step": "greet",
            "steps": [
                {
                    "id": "greet",
                    "kind": "llm_call",
                    "prompt-from": "greet.md",
                    "schema": {"type": "object"},
                },
            ],
        },
    )
    wf = load_workflow(tmp_skill_dir)
    step = wf.steps_by_id["greet"]
    assert step.config["prompt"] == "This is the greeting prompt."


def test_load_rejects_route_with_no_choices(tmp_skill_dir):
    """route step with empty choices raises ValueError at load time."""
    write_skill(
        tmp_skill_dir,
        {"name": "no_choices_wf", "description": "Empty choices", "kind": "workflow"},
        {
            "initial-step": "decide",
            "steps": [
                {
                    "id": "decide",
                    "kind": "route",
                    "prompt": "Choose a direction",
                    "choices": [],
                },
            ],
        },
    )
    with pytest.raises(ValueError, match="at least one choice"):
        load_workflow(tmp_skill_dir)


def test_load_rejects_route_with_missing_choices(tmp_skill_dir):
    """route step with no choices key raises ValueError at load time."""
    write_skill(
        tmp_skill_dir,
        {"name": "missing_choices_wf", "description": "Missing choices", "kind": "workflow"},
        {
            "initial-step": "decide",
            "steps": [
                {
                    "id": "decide",
                    "kind": "route",
                    "prompt": "Choose a direction",
                    # no choices key
                },
            ],
        },
    )
    with pytest.raises(ValueError, match="at least one choice"):
        load_workflow(tmp_skill_dir)


def test_load_rejects_route_with_duplicate_choice_ids(tmp_skill_dir):
    """route step with two choices sharing the same id raises ValueError at load time."""
    write_skill(
        tmp_skill_dir,
        {"name": "dup_choices_wf", "description": "Duplicate choice ids", "kind": "workflow"},
        {
            "initial-step": "decide",
            "steps": [
                {
                    "id": "decide",
                    "kind": "route",
                    "prompt": "Choose a direction",
                    "choices": [
                        {"id": "approve", "to": "publish", "when": "draft is good"},
                        {"id": "approve", "to": "outline", "when": "draft needs rework"},
                    ],
                },
                {"id": "publish", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}},
                {"id": "outline", "kind": "llm_call", "prompt": "p", "schema": {"type": "object"}},
            ],
        },
    )
    with pytest.raises(ValueError, match="duplicate choice ids"):
        load_workflow(tmp_skill_dir)
