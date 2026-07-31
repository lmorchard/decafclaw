"""Guards keeping the schedule tier trust classification honest.

The gate in `run_schedule_task` asks whether `task.source` is in
`_PREAPPROVAL_TIERS`. That is only safe if every tier a schedule can
actually be discovered with has had its trust decided deliberately.

Both tests below are needed. The first alone would be an enumeration
guarding itself — the mistake #732's drift guard was hardened against.
"""

from pathlib import Path

from decafclaw.schedules import (
    _PREAPPROVAL_TIERS,
    _UNTRUSTED_TIERS,
    SCHEDULE_TIERS,
    discover_schedules,
)

MINIMAL = '---\nschedule: "0 3 * * *"\n---\n\nBody.\n'


def test_every_declared_tier_is_classified():
    """Adding a tier without deciding its trust fails here."""
    classified = _PREAPPROVAL_TIERS | _UNTRUSTED_TIERS
    assert set(SCHEDULE_TIERS) == classified, (
        f"unclassified: {set(SCHEDULE_TIERS) - classified}; "
        f"stale: {classified - set(SCHEDULE_TIERS)}"
    )


def test_trust_sets_are_disjoint():
    assert not (_PREAPPROVAL_TIERS & _UNTRUSTED_TIERS)


def test_discovery_only_produces_declared_tiers(config, tmp_path, monkeypatch):
    """Using a new tier literal at a discovery site fails here.

    Exercises all four discovery paths so no tier can appear at runtime
    that `SCHEDULE_TIERS` does not know about.
    """
    # admin standalone
    admin = config.agent_path / "schedules" / "admin-standalone.md"
    admin.parent.mkdir(parents=True, exist_ok=True)
    admin.write_text(MINIMAL)

    # workspace standalone
    ws = config.workspace_path / "schedules" / "ws-standalone.md"
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_text(MINIMAL)

    # admin-level skill SCHEDULE.md
    admin_skill = config.agent_path / "skills" / "adminskill"
    admin_skill.mkdir(parents=True, exist_ok=True)
    (admin_skill / "SCHEDULE.md").write_text(MINIMAL)

    # extra-path (contrib) skill SCHEDULE.md
    extra_root = tmp_path / "extra-skills"
    extra_skill = extra_root / "contribskill"
    extra_skill.mkdir(parents=True, exist_ok=True)
    (extra_skill / "SCHEDULE.md").write_text(MINIMAL)
    monkeypatch.setattr(
        "decafclaw.schedules._resolve_extra_skill_paths",
        lambda _config: [Path(extra_root)],
    )

    tasks = discover_schedules(config)
    found = {t.source for t in tasks}

    # Bundled skills (dream, garden, newsletter) ship SCHEDULE.md, so the
    # bundled tier is exercised without any fixture setup.
    assert {"admin", "workspace", "extra", "bundled"} <= found, (
        f"fixture did not exercise every discovery path; got {found}"
    )
    assert found <= set(SCHEDULE_TIERS), (
        f"discovery produced undeclared tier(s): {found - set(SCHEDULE_TIERS)}"
    )
