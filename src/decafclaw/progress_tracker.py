"""Shared builder for the ``progress_tracker`` widget payload.

The ``progress_tracker`` widget renders a quiet multi-step status list with the
shape ``{title, summary, steps: [{label, status, note?}]}`` (see
``web/static/widgets/progress_tracker/widget.json``). That shape was previously
hand-assembled in a dozen places — the background job parsers, the project
skill, and the checklist tool. This module is the single place that builds it so
every producer emits an identical payload (#1376).

The builders return plain dicts (not dataclasses) because the sticky slot
serializes widget data with ``json.dumps``; a step's optional ``note`` is
omitted when empty so the JSON stays clean.
"""


def progress_step(label: str, status: str, note: str | None = None) -> dict:
    """Build one ``progress_tracker`` step.

    Shape matches the widget's data_schema: required ``label`` and ``status``,
    plus an optional ``note`` (omitted when falsy).
    """
    step: dict[str, str] = {"label": label, "status": status}
    if note:
        step["note"] = note
    return step


def progress_payload(title: str, summary: str, steps: list[dict]) -> dict:
    """Assemble a ``progress_tracker`` widget payload.

    Returns ``{"title": ..., "summary": ..., "steps": [...]}`` — the exact
    shape the widget's data_schema expects. Both ``title`` and ``summary`` are
    always present (pass "" to render an empty field).
    """
    return {"title": title, "summary": summary, "steps": steps}
