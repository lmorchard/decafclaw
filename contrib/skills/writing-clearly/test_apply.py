"""Tests for the deterministic plan-apply step in the writing-clearly skill."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_THIS_DIR = Path(__file__).parent
_tools_spec = importlib.util.spec_from_file_location(
    "decafclaw_contrib_writing_clearly_tools", _THIS_DIR / "tools.py"
)
assert _tools_spec is not None and _tools_spec.loader is not None
writing_clearly_tools = importlib.util.module_from_spec(_tools_spec)
sys.modules["decafclaw_contrib_writing_clearly_tools"] = writing_clearly_tools
_tools_spec.loader.exec_module(writing_clearly_tools)

_apply_plan = writing_clearly_tools._apply_plan


def _edit(before, after, kind="substitution", rule="Rule 13"):
    return {"kind": kind, "rule": rule, "before": before, "after": after, "note": ""}


@pytest.mark.asyncio
async def test_apply_single_substitution():
    draft = "Things got done fast."
    edits = [_edit("Things got done fast", "Work got done fast")]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == "Work got done fast."
    assert len(applied) == 1
    assert skipped == []


@pytest.mark.asyncio
async def test_apply_ordered_cascade():
    """A later entry can target text produced by an earlier entry."""
    draft = "It is very important that we move quickly."
    edits = [
        _edit("very important", "essential"),
        _edit("essential that we move quickly", "essential we move quickly"),
    ]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == "It is essential we move quickly."
    assert len(applied) == 2
    assert skipped == []


@pytest.mark.asyncio
async def test_apply_before_not_found():
    draft = "The quick brown fox."
    edits = [_edit("slow green turtle", "fast red bird")]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == draft
    assert applied == []
    assert len(skipped) == 1
    assert skipped[0]["_skip_reason"] == "before_not_found"


@pytest.mark.asyncio
async def test_apply_noop():
    draft = "Already clean."
    edits = [_edit("Already clean", "Already clean")]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == draft
    assert applied == []
    assert skipped[0]["_skip_reason"] == "noop"


@pytest.mark.asyncio
async def test_apply_empty_before():
    draft = "Some text."
    edits = [_edit("", "replacement")]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == draft
    assert applied == []
    assert skipped[0]["_skip_reason"] == "before_empty"


@pytest.mark.asyncio
async def test_apply_empty_plan():
    draft = "Untouched."
    revised, applied, skipped = await _apply_plan(draft, [])
    assert revised == draft
    assert applied == []
    assert skipped == []


@pytest.mark.asyncio
async def test_apply_first_occurrence_only():
    """A single entry replaces only the first occurrence; a second entry
    can target the second occurrence."""
    draft = "really really tired"
    edits = [_edit("really", "very"), _edit("really", "deeply")]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == "very deeply tired"
    assert len(applied) == 2
    assert skipped == []


@pytest.mark.asyncio
async def test_apply_rewrite_kind():
    """`rewrite` entries are applied identically to substitutions —
    just longer before/after strings."""
    draft = "This is a really important point that you should remember."
    edits = [
        _edit(
            "This is a really important point that you should remember.",
            "Remember this point.",
            kind="rewrite",
            rule="Rule 18",
        ),
    ]
    revised, applied, skipped = await _apply_plan(draft, edits)
    assert revised == "Remember this point."
    assert applied[0]["kind"] == "rewrite"


@pytest.mark.asyncio
async def test_apply_progress_events_emitted():
    """When publish is provided, one event fires per applied edit and one per skip."""
    events = []

    async def fake_publish(*args, **kwargs):
        events.append((args, kwargs))

    draft = "really fast and really slow"
    edits = [
        _edit("really fast", "quickly"),
        _edit("missing", "nope"),
        _edit("really slow", "slowly"),
    ]
    revised, applied, skipped = await _apply_plan(draft, edits, publish=fake_publish)
    assert revised == "quickly and slowly"
    assert len(applied) == 2
    assert len(skipped) == 1
    # One event per entry: 2 applied + 1 skipped = 3 events.
    assert len(events) == 3
    messages = [e[1]["message"] for e in events]
    assert any("Applied" in m for m in messages)
    assert any("Skipped" in m for m in messages)
