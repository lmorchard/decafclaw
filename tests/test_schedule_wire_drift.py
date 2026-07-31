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
# user-editable content) plus the parser's read-only diagnostic. All
# four are read-only for structural reasons, not by policy choice, so
# there is no positive patchability counter-test for this set:
# `unknown_keys` in particular can never be observed to "take" through
# `write_overlay`, because `serialize_to_markdown` never emits it —
# any patch is silently discarded by the reparse on the next
# `discover_schedules` regardless of what write_overlay does with it
# in memory. That non-persistence guarantee is covered directly by
# `test_unknown_keys_are_not_written_back` in test_schedules.py.
NOT_PATCHABLE = {"name", "source", "path", "unknown_keys"}

# Wire keys `_schedule_to_dict` emits that have no backing ScheduleTask
# field: computed/derived data (overlay status, raw frontmatter text,
# file mtime, next/last run timestamps). Kept explicit so
# test_wire_renames_are_injective can check rename targets against the
# *whole* emitted keyspace, not just other field names — a rename
# pointing at one of these passes a field-only injectivity check (no
# other field maps there either) while still masking the renamed field
# behind an already-populated key.
NON_FIELD_WIRE_KEYS = {
    "has_overlay", "frontmatter_raw", "modified", "next_run_iso", "last_run_iso",
}

# Sample values by annotated type. A field with a new type raises
# KeyError here, which is the point: it forces a decision instead of
# silently skipping the field.
#
# Keying on `f.type` works only because `schedules.py` has no
# `from __future__ import annotations`; with it, `f.type` would be the
# *string* `"bool"` rather than the type object and every lookup here
# would KeyError. That failure is loud and immediate, which is the
# behaviour we want — but the coupling is worth knowing about before
# adding the future import.
#
# `bool` is absent on purpose: see `_sample_for` below.
SAMPLES = {
    str: "sample-value",
    list[str]: ["sample-value"],
}


def _sample_for(spec):
    """A patch value that a no-op write_overlay cannot satisfy by luck.

    For a bool the value space has two elements, so a fixed sample is
    only discriminating when it differs from the field's default: patch
    a `False`-defaulting field to `False` and the assertion passes
    whether or not write_overlay ever wired the field up. Derive the
    sample from the field's own default instead, so it is always the
    value the field does *not* already hold.
    """
    if spec.name in SAMPLE_OVERRIDES:
        return SAMPLE_OVERRIDES[spec.name]
    if spec.type is bool:
        return not spec.default
    return SAMPLES[spec.type]

# Values that must satisfy a validator rather than merely round-trip.
SAMPLE_OVERRIDES = {"schedule": "*/5 * * * *"}

# One distinct value per patchable field, used by
# test_wire_values_match_task_fields below. Distinct *per field* (not
# just non-default) so a wire value that got misrouted onto the wrong
# key — not merely dropped — still fails the comparison.
#
# `enabled` is deliberately excluded: it's the only bool field, and a
# bool's value space is two elements, so any single seeded value can
# be matched by a hardcoded constant purely by chance (a constant
# "False" wire value passes as easily as the real field). No single
# seed closes that hole — see test_wire_value_for_bool_field_round_trips
# below, which probes both True and False instead.
DISTINCT_VALUES = {
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
    """A rename target must be unique — across *all* wire keys, not
    just other field names — or a masking rename can silence the
    presence check while making the field invisible.

    Concretely: delete a field's wire line, then "fix" the resulting
    failure by adding ``WIRE_RENAMES["that_field"] = "some_other_key"``
    that already exists in the payload — the presence check in
    ``test_every_dataclass_field_reaches_the_wire`` would go green
    while the field is still absent from the UI. The existing key can
    belong to another field (checked below) or to a computed,
    non-field wire key like ``has_overlay`` (checked against
    NON_FIELD_WIRE_KEYS, since those don't show up in ``mapped`` at
    all and a field-only injectivity check can't see them).
    """
    mapped = [WIRE_RENAMES.get(f.name, f.name) for f in fields(ScheduleTask)]
    assert len(set(mapped)) == len(mapped), (
        f"two or more fields map to the same wire key: {mapped}"
    )

    masked = set(WIRE_RENAMES.values()) & NON_FIELD_WIRE_KEYS
    assert not masked, (
        f"WIRE_RENAMES targets a computed wire key with no backing field "
        f"({masked}) — this masks the renamed field behind an "
        f"already-populated key just as surely as a field-to-field collision."
    )


def test_non_field_wire_keys_is_exactly_the_computed_remainder(config):
    """The one hand-maintained list left in the anti-enumeration guard.

    ``NON_FIELD_WIRE_KEYS`` is consumed by the masking check above, so a
    stale copy silently narrows that check: add a computed key to
    ``_schedule_to_dict`` without updating the set and a WIRE_RENAMES
    entry pointing at the new key sails through, reopening the exact
    hole the masking check closed. Derive the set from the payload and
    pin it, so a new computed key fails here until the author looks at
    it.
    """
    _seed(config, name="nonfield-probe")
    task = {t.name: t for t in discover_schedules(config)}["nonfield-probe"]
    payload = _schedule_to_dict(config, task)

    computed = set(payload) - {
        WIRE_RENAMES.get(f.name, f.name) for f in fields(ScheduleTask)
    }
    assert NON_FIELD_WIRE_KEYS == computed, (
        f"NON_FIELD_WIRE_KEYS is stale. _schedule_to_dict emits computed keys "
        f"{sorted(computed)}; the set lists {sorted(NON_FIELD_WIRE_KEYS)}. "
        f"Update it — the masking check in test_wire_renames_are_injective "
        f"can only see the keys named here."
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
    patchable = [
        f.name for f in fields(ScheduleTask)
        if f.name not in NOT_PATCHABLE and f.name != "enabled"
    ]
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
        if f.name == "enabled":
            continue  # two-valued; see test_wire_value_for_bool_field_round_trips
        assert payload[f.name] == getattr(task, f.name), (
            f"wire value for {f.name!r} does not match the parsed task "
            f"— check for a hardcoded stub or a misrouted field."
        )


def test_wire_value_for_bool_field_round_trips(config):
    """`enabled` needs both of its two possible values probed.

    A single seeded value can't distinguish a hardcoded constant from
    the real field for a two-valued type: hardcoding
    ``"enabled": False`` (or ``True``) in ``_schedule_to_dict`` would
    satisfy a comparison against a single seeded value purely by
    chance. Two fixtures with opposite values close that hole — no
    constant can satisfy both.
    """
    _seed(config, name="bool-probe-true")
    _seed(config, name="bool-probe-false")
    write_overlay(config, "bool-probe-false", {"enabled": False})

    tasks = {t.name: t for t in discover_schedules(config)}
    for name, expected in [("bool-probe-true", True), ("bool-probe-false", False)]:
        task = tasks[name]
        assert task.enabled is expected  # sanity: the overlay took
        payload = _schedule_to_dict(config, task)
        assert payload["enabled"] == expected, (
            f"wire value for 'enabled' does not match the parsed task "
            f"for {name!r} (expected {expected!r})"
        )


@pytest.mark.parametrize(
    "field_name",
    [f.name for f in fields(ScheduleTask) if f.name not in NOT_PATCHABLE],
)
def test_every_editable_field_is_patchable(config, field_name):
    """write_overlay must accept a patch for each editable field."""
    _seed(config)
    spec = {f.name: f for f in fields(ScheduleTask)}[field_name]
    sample = _sample_for(spec)

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
