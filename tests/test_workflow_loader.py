"""Tests for the workflow loader and validation gates."""

from pathlib import Path

import pytest

from decafclaw.workflow.loader import (
    LoaderError,
    load_workflow,
)
from decafclaw.workflow.types import PhaseKind


def _write_workflow(root: Path, files: dict[str, str]) -> Path:
    skill_dir = root / "skill"
    skill_dir.mkdir()
    phases_dir = skill_dir / "phases"
    phases_dir.mkdir()
    for relpath, content in files.items():
        target = skill_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return skill_dir


_SKILL_MD = """---
name: demo
description: test workflow
kind: workflow
user-invocable: true
workflow:
  initial-phase: gather
---
body
"""

_GATHER = """---
kind: subagent
tools: [vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
---
gather prompt
"""

_DRAFT = """---
kind: inline
tools: [vault_write]
next-phases:
  - id: review
    when: ready
---
draft prompt
"""

_REVIEW = """---
kind: inline
tools: [vault_read]
next-phases:
  - id: publish
    when: approved
    gate:
      type: review
      message: "Approve?"
      approve-label: "Yes"
      deny-label: "No"
      on-deny: draft
---
review prompt
"""

_PUBLISH = """---
kind: inline
tools: [vault_write]
---
publish prompt
"""


def test_load_happy_path(tmp_path):
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    wf = load_workflow(d)
    assert wf.name == "demo"
    assert wf.initial_phase == "gather"
    assert set(wf.phases) == {"gather", "draft", "review", "publish"}
    assert wf.phases["gather"].kind == PhaseKind.SUBAGENT
    assert wf.phases["gather"].outputs == ("sources.md",)
    assert wf.phases["draft"].next_phases[0].id == "review"
    assert wf.phases["draft"].next_phases[0].when == "ready"
    review_edge = wf.phases["review"].next_phases[0]
    assert review_edge.gate is not None
    assert review_edge.gate.on_deny == "draft"
    assert wf.phases["publish"].is_terminal


def test_load_fails_when_initial_phase_missing(tmp_path):
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD.replace("initial-phase: gather",
                                       "initial-phase: nope"),
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="initial-phase"):
        load_workflow(d)


def test_load_fails_when_edge_target_undefined(tmp_path):
    bad_draft = _DRAFT.replace("- id: review", "- id: ghost")
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": bad_draft,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="ghost"):
        load_workflow(d)


def test_load_fails_when_multi_edge_missing_when(tmp_path):
    bad_draft = """---
kind: inline
tools: [vault_write]
next-phases:
  - id: review
  - id: publish
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": bad_draft,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="when"):
        load_workflow(d)


def test_load_fails_when_subagent_has_multiple_edges(tmp_path):
    bad_gather = """---
kind: subagent
tools: [vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
    when: usually
  - id: review
    when: short-circuit
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="subagent"):
        load_workflow(d)


def test_load_fails_when_subagent_has_gated_edge(tmp_path):
    bad_gather = """---
kind: subagent
tools: [vault_read]
outputs: [sources.md]
next-phases:
  - id: draft
    gate:
      type: review
      message: "Approve?"
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="gate"):
        load_workflow(d)


def test_load_fails_when_subagent_missing_outputs(tmp_path):
    bad_gather = """---
kind: subagent
tools: [vault_read]
next-phases:
  - id: draft
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="outputs"):
        load_workflow(d)


def test_load_fails_when_gate_on_deny_undefined(tmp_path):
    bad_review = _REVIEW.replace("on-deny: draft", "on-deny: ghost")
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": bad_review,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="on-deny.*ghost"):
        load_workflow(d)


def test_load_fails_when_no_phases_directory(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD)
    with pytest.raises(LoaderError, match="phases"):
        load_workflow(skill_dir)


def test_load_fails_when_phase_id_has_uppercase(tmp_path):
    """Phase ids must match [a-z][a-z0-9_-]*. Uppercase rejected."""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD.replace(
            "initial-phase: gather", "initial-phase: Gather"),
        "phases/Gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match=r"\[a-z\]"):
        load_workflow(d)


def test_load_fails_when_outputs_contains_null(tmp_path):
    """outputs: must be a list of non-empty strings — null rejected."""
    bad_gather = """---
kind: subagent
tools: [vault_read]
outputs:
  - null
  - sources.md
next-phases:
  - id: draft
---
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": bad_gather,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="outputs"):
        load_workflow(d)


def test_load_fails_when_gate_type_unsupported(tmp_path):
    """Only gate type 'review' is supported in v1."""
    bad_review = _REVIEW.replace("type: review", "type: input")
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": _GATHER,
        "phases/draft.md": _DRAFT,
        "phases/review.md": bad_review,
        "phases/publish.md": _PUBLISH,
    })
    with pytest.raises(LoaderError, match="gate type"):
        load_workflow(d)


def test_load_subagent_skill_escape_hatch(tmp_path):
    """A subagent phase with subagent-skill: doesn't need outputs:
    because the referenced skill owns its own output contract."""
    gather_with_skill = """---
kind: subagent
subagent-skill: my-worker
next-phases:
  - id: draft
---
unused body
"""
    d = _write_workflow(tmp_path, {
        "SKILL.md": _SKILL_MD,
        "phases/gather.md": gather_with_skill,
        "phases/draft.md": _DRAFT,
        "phases/review.md": _REVIEW,
        "phases/publish.md": _PUBLISH,
    })
    wf = load_workflow(d)
    assert wf.phases["gather"].subagent_skill == "my-worker"
    assert wf.phases["gather"].outputs == ()
