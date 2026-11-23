"""Tests for the shared ``progress_tracker`` payload builder (#1376).

The builder is the single place that assembles the widget's
``{title, summary, steps: [{label, status, note?}]}`` shape. These cases pin
that contract so a producer can't silently drift from the widget's data_schema.
"""

import json
from pathlib import Path

import jsonschema

from decafclaw.progress_tracker import progress_payload, progress_step


def test_progress_step_omits_empty_note():
    step = progress_step("Build", "in_progress")
    assert step == {"label": "Build", "status": "in_progress"}
    assert "note" not in step


def test_progress_step_includes_note_when_present():
    step = progress_step("Build", "done", note="ok")
    assert step == {"label": "Build", "status": "done", "note": "ok"}


def test_progress_step_omits_blank_note():
    step = progress_step("Build", "pending", note="")
    assert "note" not in step


def test_progress_payload_shape():
    payload = progress_payload("Background job", "1/3 · Build", [progress_step("Build", "in_progress")])
    assert payload == {
        "title": "Background job",
        "summary": "1/3 · Build",
        "steps": [{"label": "Build", "status": "in_progress"}],
    }


def test_progress_payload_always_includes_title_and_summary():
    # Empty strings are still emitted as keys, matching the prior hand-built payloads.
    payload = progress_payload("", "", [])
    assert set(payload) == {"title", "summary", "steps"}
    assert payload["title"] == ""
    assert payload["summary"] == ""
    assert payload["steps"] == []


def test_progress_payload_is_json_serializable():
    # The sticky slot serializes widget data with json.dumps; the builder must
    # produce plain, JSON-encodable values.
    payload = progress_payload("Job", "done", [progress_step("Step 1", "done", note="ok")])
    round_tripped = json.loads(json.dumps(payload))
    assert round_tripped == payload


def test_progress_payload_matches_widget_schema():
    """Builder output must satisfy the progress_tracker widget's data_schema."""
    schema_path = (
        Path(__file__).parent.parent
        / "src"
        / "decafclaw"
        / "web"
        / "static"
        / "widgets"
        / "progress_tracker"
        / "widget.json"
    )
    schema = json.loads(schema_path.read_text())["data_schema"]
    payload = progress_payload(
        "Background job",
        "[2/5] Building",
        [progress_step("make build", "in_progress", note="[2/5] Building")],
    )
    jsonschema.validate(payload, schema)  # raises on a shape mismatch
