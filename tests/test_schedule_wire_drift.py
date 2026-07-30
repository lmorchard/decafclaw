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

# Fields `_schedule_to_dict` renames on the wire. Renaming is fine only
# when the target key is unique to that field — see
# test_wire_renames_are_injective below. Do NOT add an entry here to
# silence a failing presence check by mapping a new field onto an
# existing wire key; that makes the field invisible instead of wiring
# it through (the exact failure mode this file exists to catch).
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

# One distinct value per patchable field, used by
# test_wire_values_match_task_fields below. Distinct *per field* (not
# just non-default) so a wire value that got misrouted onto the wrong
# key — not merely dropped — still fails the comparison.
DISTINCT_VALUES = {
    "enabled": False,
    "schedule": "*/5 * * * *",
    "body": "Distinct body.",
    "channel": "distinct-channel",
    "model": "distinct-model",
    "allowed_tools": ["distinct-tool"],
    "shell_patterns": ["distinct-pattern"],
    "required_skills": ["distinct-skill"],
    "email_recipients": ["distinct@example.com"],
    "pre_script": "distinct-pre-script.py",
}


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


def test_wire_renames_are_injective(config):
    """A rename target must be unique, or a masking rename can silence
    the presence check while making the field invisible.

    Concretely: delete a field's wire line, then "fix" the resulting
    failure by adding ``WIRE_RENAMES["that_field"] = "some_other_key"``
    that already exists in the payload — the presence check in
    ``test_every_dataclass_field_reaches_the_wire`` would go green
    while the field is still absent from the UI.
    """
    mapped = [WIRE_RENAMES.get(f.name, f.name) for f in fields(ScheduleTask)]
    assert len(set(mapped)) == len(mapped), (
        f"two or more fields map to the same wire key: {mapped}"
    )


def test_wire_values_match_task_fields(config):
    """A hardcoded stub (e.g. ``"pre_script": ""``) must fail, not just
    a missing key.

    Every editable field gets its own distinct value (not merely a
    non-default one) so a *misrouted* wire value — field A's value
    written under field B's key — is caught too, not only a dropped or
    stubbed one.
    """
    path = _seed(config, name="value-probe")
    patchable = [f.name for f in fields(ScheduleTask) if f.name not in NOT_PATCHABLE]
    write_overlay(config, "value-probe", {name: DISTINCT_VALUES[name] for name in patchable})

    # unknown_keys is read-only: write_overlay's serialize_to_markdown
    # only emits recognized keys, so it can't be set through the
    # overlay round-trip above. Inject an unrecognized key directly
    # into the file instead.
    lines = path.read_text().splitlines(keepends=True)
    lines.insert(1, "typo-key: sample-value\n")
    path.write_text("".join(lines))

    task = {t.name: t for t in discover_schedules(config)}["value-probe"]
    payload = _schedule_to_dict(config, task)

    for f in fields(ScheduleTask):
        if f.name in WIRE_RENAMES:
            continue  # source/path hold derived values; presence-only
            # (already asserted by test_every_dataclass_field_reaches_the_wire)
            # is correct for those.
        assert payload[f.name] == getattr(task, f.name), (
            f"wire value for {f.name!r} does not match the parsed task "
            f"— check for a hardcoded stub or a misrouted field."
        )


def test_unknown_keys_is_not_patchable(config):
    """unknown_keys is a read-only parser diagnostic. A patch for it
    must not take — this is the positive half of NOT_PATCHABLE; without
    it, a patchability regression could be silenced by adding the field
    to NOT_PATCHABLE (as the failure message for
    test_every_editable_field_is_patchable explicitly suggests) even if
    it should have been wired through instead.
    """
    _seed(config, name="not-patchable-probe")
    write_overlay(config, "not-patchable-probe", {"unknown_keys": ["should-not-take"]})
    task = {t.name: t for t in discover_schedules(config)}["not-patchable-probe"]
    assert task.unknown_keys == []


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
