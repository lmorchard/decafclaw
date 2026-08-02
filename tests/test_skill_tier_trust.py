"""The skill trust-tier partition, and the predicate every consumer must use.

Companion to `tests/test_schedule_tier_trust.py`, which pins the equivalent
partition for a *schedule's* `task.source`. These are deliberately two
different partitions over the same four tier names — see the paired comments
on `skills.SKILL_CAPABILITY_TIERS` and `schedules._PREAPPROVAL_TIERS`.

`SKILL_CAPABILITY_TIERS` is an allowlist on purpose. A denylist keyed off the
untrusted tiers would fail OPEN for any tier nobody thought to enumerate.
"""

from pathlib import Path

import pytest

from decafclaw.skills import (
    SKILL_CAPABILITY_TIERS,
    SKILL_TIERS,
    SkillInfo,
    grants_capability,
    skill_scan_entries,
)


def _info(tier: str) -> SkillInfo:
    return SkillInfo(
        name="probe",
        description="A skill used to exercise the tier predicate.",
        location=Path("/nonexistent/probe"),
        trust_tier=tier,
    )


def test_skill_tiers_covers_every_scan_entry_tier(config, tmp_path):
    """A new scan entry can't be added without declaring its tier.

    `skill_scan_entries` is the single source of truth for discovery roots,
    so any tier it can assign must appear in SKILL_TIERS — otherwise a skill
    could reach a consumer carrying a tier no partition classifies.
    """
    extra = tmp_path / "extra-skills"
    extra.mkdir()
    config.extra_skill_paths = [str(extra)]

    tiers = {tier for tier, _ in skill_scan_entries(config)}

    assert tiers == set(SKILL_TIERS)


def test_capability_tiers_partition_is_decided():
    """Pins which tiers are untrusted, so a NEW tier forces a decision.

    Adding a tier to SKILL_TIERS makes it implicitly untrusted, which is
    fail-closed and therefore safe — but silent. This assertion makes it
    loud: whoever adds the tier has to come here and say what it is.
    """
    assert SKILL_CAPABILITY_TIERS <= set(SKILL_TIERS)
    assert set(SKILL_TIERS) - SKILL_CAPABILITY_TIERS == {"workspace"}


def test_unassigned_trust_tier_is_untrusted(tmp_path):
    """A SkillInfo built off the discovery path must not arrive trusted.

    `discover_skills` always assigns the real tier from placement, so
    anything reaching a consumer without one has no placement to vouch for
    it. `parse_skill_md` is the one constructor that leaves the field at its
    default; it has no runtime callers today, which makes this latent rather
    than live — and exactly the kind of default that becomes live silently.
    """
    from decafclaw.skills import parse_skill_md

    skill_dir = tmp_path / "unplaced"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: unplaced\ndescription: Built without a placement.\n---\n\nBody.\n"
    )

    info = parse_skill_md(skill_dir / "SKILL.md")

    assert info is not None
    assert info.trust_tier == "workspace"
    assert grants_capability(info) is False


@pytest.mark.parametrize("tier,expected", [
    ("workspace", False),
    ("admin", True),
    ("bundled", True),
    ("extra", True),
    # Anything unrecognized must fail closed. "Workspace" (capitalized) and ""
    # are the shapes a typo or an unset field actually takes.
    ("", False),
    ("Workspace", False),
    ("plugin", False),
])
def test_grants_capability(tier, expected):
    assert grants_capability(_info(tier)) is expected
