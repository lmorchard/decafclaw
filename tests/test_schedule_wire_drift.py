"""The guard that keeps four hand-maintained field lists honest.

`ScheduleTask`, `serialize_to_markdown`, `write_overlay`'s patch handling
and `_schedule_to_dict` are four separate enumerations of the same field
set. They had drifted apart at four different points before #729's
follow-on work. These tests derive their expectations from
`dataclasses.fields(ScheduleTask)`, so adding a field fails here until it
is either wired through or explicitly exempted.
"""

from dataclasses import fields
from pathlib import Path

import pytest

from decafclaw.http_server import _schedule_to_dict
from decafclaw.schedules import ScheduleTask, discover_schedules, write_overlay

# Fields `_schedule_to_dict` renames on the wire. Renaming is fine;
# dropping is not, so these are mapped rather than exempted.
WIRE_RENAMES = {"source": "source_tier", "path": "source_path"}

# Not patchable: identity and provenance (the file's location is not
# user-editable content) plus the parser's read-only diagnostic.
NOT_PATCHABLE = {"name", "source", "path", "unknown_keys"}

# Sample values by annotated type. A field with a new type raises
# KeyError here, which is the point: it forces a decision instead of
# silently skipping the field.
SAMPLES = {
    str: "sample-value",
    bool: False,
    list[str]: ["sample-value"],
}

# Values that must satisfy a validator rather than merely round-trip.
SAMPLE_OVERRIDES = {"schedule": "*/5 * * * *"}


def _seed(config, name="drift-probe"):
    path = config.workspace_path / "schedules" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        'schedule: "0 3 * * *"\n'
        "---\n\n"
        "Body.\n"
    )
    return path


def test_every_dataclass_field_reaches_the_wire(config):
    """No field may be invisible to the UI."""
    _seed(config)
    task = {t.name: t for t in discover_schedules(config)}["drift-probe"]
    payload = _schedule_to_dict(config, task)

    missing = [
        f.name for f in fields(ScheduleTask)
        if WIRE_RENAMES.get(f.name, f.name) not in payload
    ]
    assert missing == [], (
        f"fields absent from _schedule_to_dict: {missing}. Add them to the "
        f"wire payload, or add a rename to WIRE_RENAMES if the key differs."
    )


@pytest.mark.parametrize(
    "field_name",
    [f.name for f in fields(ScheduleTask) if f.name not in NOT_PATCHABLE],
)
def test_every_editable_field_is_patchable(config, field_name):
    """write_overlay must accept a patch for each editable field."""
    _seed(config)
    spec = {f.name: f for f in fields(ScheduleTask)}[field_name]
    sample = SAMPLE_OVERRIDES.get(field_name, SAMPLES[spec.type])

    write_overlay(config, "drift-probe", {field_name: sample})
    task = {t.name: t for t in discover_schedules(config)}["drift-probe"]

    assert getattr(task, field_name) == sample, (
        f"write_overlay ignored a patch for {field_name!r}. Add it to the "
        f"replace(...) call, or to NOT_PATCHABLE if it is read-only."
    )


def test_exemption_sets_name_only_real_fields(config):
    """A renamed or deleted field must not leave a stale exemption behind."""
    names = {f.name for f in fields(ScheduleTask)}
    assert NOT_PATCHABLE <= names, f"stale: {NOT_PATCHABLE - names}"
    assert set(WIRE_RENAMES) <= names, f"stale: {set(WIRE_RENAMES) - names}"


def test_frontmatter_raw_is_the_file_not_a_reserialization(config):
    """The raw view exists to show keys the parser drops."""
    path = config.workspace_path / "schedules" / "raw-probe.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        'schedule: "0 3 * * *"\n'
        "modle: vertex-gemini-pro\n"
        "---\n\n"
        "Body.\n"
    )
    task = {t.name: t for t in discover_schedules(config)}["raw-probe"]
    payload = _schedule_to_dict(config, task)

    assert "modle: vertex-gemini-pro" in payload["frontmatter_raw"]
    assert payload["unknown_keys"] == ["modle"]


def test_frontmatter_raw_survives_a_missing_file(config):
    """Bundled tasks whose path was removed must not 500 the list endpoint."""
    task = ScheduleTask(
        name="ghost", schedule="0 3 * * *", body="B",
        source="bundled", path=Path("/nonexistent/SCHEDULE.md"),
    )
    payload = _schedule_to_dict(config, task)
    assert payload["frontmatter_raw"] == ""
