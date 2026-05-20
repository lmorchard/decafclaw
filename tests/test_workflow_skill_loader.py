"""Tests for the skill-loader branch that recognizes kind:workflow."""

from pathlib import Path

import pytest

from decafclaw.skills import parse_skill_md
from decafclaw.workflow import registry


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


_SKILL_WORKFLOW = """---
name: test-wf
description: A test workflow.
kind: workflow
user-invocable: true
workflow:
  initial-phase: a
---
body
"""

_PHASE_A = """---
kind: inline
tools: []
next-phases:
  - id: b
---
A prompt
"""

_PHASE_B = """---
kind: inline
tools: []
---
B prompt
"""


def _write(tmp_path: Path, files: dict[str, str]) -> Path:
    sk = tmp_path / "test-wf"
    sk.mkdir()
    (sk / "SKILL.md").write_text(files["SKILL.md"])
    phases = sk / "phases"
    phases.mkdir()
    for name, content in files.items():
        if name == "SKILL.md":
            continue
        (phases / name).write_text(content)
    return sk / "SKILL.md"


def test_parse_skill_md_workflow_registers_definition(tmp_path: Path):
    path = _write(tmp_path, {
        "SKILL.md": _SKILL_WORKFLOW,
        "a.md": _PHASE_A,
        "b.md": _PHASE_B,
    })
    info = parse_skill_md(path)
    assert info is not None
    assert info.name == "test-wf"
    assert registry.get("test-wf") is not None
    assert registry.get("test-wf").initial_phase == "a"


def test_parse_skill_md_workflow_invalid_skips_registration(
        tmp_path: Path, caplog):
    bad_phase_a = """---
kind: inline
next-phases:
  - id: ghost
---
"""
    path = _write(tmp_path, {
        "SKILL.md": _SKILL_WORKFLOW,
        "a.md": bad_phase_a,
        "b.md": _PHASE_B,
    })
    with caplog.at_level("WARNING"):
        parse_skill_md(path)
    # SkillInfo still returned (skill loader is lenient), but the
    # workflow registry is NOT populated for an invalid workflow
    assert registry.get("test-wf") is None
    assert any("workflow" in rec.message.lower()
               for rec in caplog.records)
