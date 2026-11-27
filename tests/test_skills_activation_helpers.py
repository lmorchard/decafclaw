"""Tests for the always-loaded and workflow skill activation helpers (split from #1419)."""

import pytest

from decafclaw.skills import SkillInfo, activate_always_loaded, activate_skills_for_workflow
from decafclaw.tools.skill_tools import SkillActivationResult
from decafclaw.workflow.errors import WorkflowSkillActivationFailed
from tests.skill_helpers import _make_skill_info

# -- activate_always_loaded helper tests --


@pytest.mark.asyncio
async def test_activate_always_loaded_runs_each_skill_once(ctx, tmp_path, monkeypatch):
    """Each always-loaded trusted-tier skill is activated exactly once."""
    skill_a = _make_skill_info(tmp_path, name="alpha", trust_tier="bundled")
    skill_a.always_loaded = True
    skill_b = _make_skill_info(tmp_path, name="beta", trust_tier="bundled")
    skill_b.always_loaded = True
    ctx.config.discovered_skills = [skill_a, skill_b]

    calls: list[str] = []

    async def fake_activate(ctx_arg, info):
        calls.append(info.name)
        ctx_arg.skills.activated[info.name] = ""

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    await activate_always_loaded(ctx)

    assert calls == ["alpha", "beta"]
    assert set(ctx.skills.activated.keys()) == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_activate_always_loaded_skips_workspace_tier(
    ctx,
    tmp_path,
    monkeypatch,
):
    """Workspace-tier skills are skipped even if `always_loaded` is True."""
    bundled = _make_skill_info(tmp_path, name="bundled-skill", trust_tier="bundled")
    bundled.always_loaded = True
    workspace = _make_skill_info(
        tmp_path,
        name="workspace-skill",
        trust_tier="workspace",
    )
    workspace.always_loaded = True  # defense-in-depth: should still be skipped
    ctx.config.discovered_skills = [bundled, workspace]

    calls: list[str] = []

    async def fake_activate(ctx_arg, info):
        calls.append(info.name)
        ctx_arg.skills.activated[info.name] = ""

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    await activate_always_loaded(ctx)

    assert calls == ["bundled-skill"]
    assert "workspace-skill" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_always_loaded_skips_already_activated(
    ctx,
    tmp_path,
    monkeypatch,
):
    """Already-activated skills are not re-activated (idempotent)."""
    skill = _make_skill_info(tmp_path, name="already-active", trust_tier="bundled")
    skill.always_loaded = True
    ctx.config.discovered_skills = [skill]
    ctx.skills.activated = {"already-active"}

    calls: list[str] = []

    async def fake_activate(ctx_arg, info):
        calls.append(info.name)

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    await activate_always_loaded(ctx)

    assert calls == []


@pytest.mark.asyncio
async def test_activate_always_loaded_fails_soft(
    ctx,
    tmp_path,
    monkeypatch,
    caplog,
):
    """A failed activation logs but does not block subsequent skills."""
    skill_a = _make_skill_info(tmp_path, name="boomer", trust_tier="bundled")
    skill_a.always_loaded = True
    skill_b = _make_skill_info(tmp_path, name="survivor", trust_tier="bundled")
    skill_b.always_loaded = True
    ctx.config.discovered_skills = [skill_a, skill_b]

    async def fake_activate(ctx_arg, info):
        if info.name == "boomer":
            raise RuntimeError("boom")
        ctx_arg.skills.activated[info.name] = ""

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    with caplog.at_level("ERROR", logger="decafclaw.skills"):
        await activate_always_loaded(ctx)

    assert "survivor" in ctx.skills.activated
    assert "boomer" not in ctx.skills.activated
    error_records = [r for r in caplog.records if r.levelname == "ERROR" and "boomer" in r.getMessage()]
    assert len(error_records) == 1


# -- activate_skills_for_workflow helper tests --


@pytest.mark.asyncio
async def test_activate_skills_for_workflow_succeeds(ctx, tmp_path, monkeypatch):
    """Each requested skill is activated exactly once."""
    skill_a = _make_skill_info(tmp_path, name="alpha", trust_tier="bundled")
    skill_b = _make_skill_info(tmp_path, name="beta", trust_tier="bundled")
    ctx.config.discovered_skills = [skill_a, skill_b]

    calls: list[SkillInfo] = []

    async def fake_activate(ctx_arg, info):
        calls.append(info)
        ctx_arg.skills.activated[info.name] = ""

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    await activate_skills_for_workflow(ctx, ["alpha", "beta"])

    assert set(ctx.skills.activated.keys()) == {"alpha", "beta"}
    assert len(calls) == 2
    assert calls[0] is skill_a
    assert calls[1] is skill_b


@pytest.mark.asyncio
async def test_activate_skills_for_workflow_unknown_skill_raises(
    ctx,
    tmp_path,
    monkeypatch,
):
    """Unknown skill name raises WorkflowSkillActivationFailed."""
    ctx.config.discovered_skills = []

    async def fake_activate(ctx_arg, info):
        raise AssertionError("should not be called")

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    with pytest.raises(WorkflowSkillActivationFailed) as exc_info:
        await activate_skills_for_workflow(ctx, ["bogus-skill"])

    assert "bogus-skill" in str(exc_info.value)
    assert ctx.skills.activated == {}


@pytest.mark.asyncio
async def test_activate_skills_for_workflow_tool_load_failure_raises(
    ctx,
    tmp_path,
    monkeypatch,
):
    """When activate_skill_internal returns a SkillActivationResult instead of
    raising (its tool-load failure path), the helper must still raise
    WorkflowSkillActivationFailed. Without this guard, the workflow would
    proceed with the required skill silently missing."""
    skill = _make_skill_info(
        tmp_path,
        name="broken-tools-skill",
        trust_tier="bundled",
    )
    ctx.config.discovered_skills = [skill]

    async def fake_activate(ctx_arg, info_arg):
        # Mirror the tool-load failure path — returns a SkillActivationResult
        # and does NOT add to ctx.skills.activated.
        return SkillActivationResult(text="[error: failed to load skill 'broken-tools-skill': ImportError(...)]")

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    with pytest.raises(WorkflowSkillActivationFailed) as exc_info:
        await activate_skills_for_workflow(ctx, ["broken-tools-skill"])

    assert "broken-tools-skill" in str(exc_info.value)
    # The failure result's error text should be surfaced in the message
    # for debugging.
    assert "failed to load" in str(exc_info.value)
    # Skill was NOT activated.
    assert "broken-tools-skill" not in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_skills_for_workflow_init_failure_raises(
    ctx,
    tmp_path,
    monkeypatch,
):
    """A skill whose init() raises surfaces WorkflowSkillActivationFailed."""
    skill = _make_skill_info(tmp_path, name="exploder", trust_tier="bundled")
    ctx.config.discovered_skills = [skill]

    original = RuntimeError("boom")

    async def fake_activate(ctx_arg, info):
        raise original

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    with pytest.raises(WorkflowSkillActivationFailed) as exc_info:
        await activate_skills_for_workflow(ctx, ["exploder"])

    msg = str(exc_info.value)
    assert "exploder" in msg
    assert "boom" in msg
    assert exc_info.value.__cause__ is original


@pytest.mark.asyncio
async def test_activate_skills_for_workflow_idempotent(
    ctx,
    tmp_path,
    monkeypatch,
):
    """Already-activated skills are skipped — the activator is not invoked."""
    skill = _make_skill_info(tmp_path, name="already-active", trust_tier="bundled")
    ctx.config.discovered_skills = [skill]
    ctx.skills.activated = {"already-active"}

    async def sabotage(ctx_arg, info):
        raise AssertionError("should not be called")

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        sabotage,
    )

    await activate_skills_for_workflow(ctx, ["already-active"])

    assert ctx.skills.activated == {"already-active"}


@pytest.mark.asyncio
async def test_activate_skills_for_workflow_permits_workspace_tier(
    ctx,
    tmp_path,
    monkeypatch,
):
    """Workspace-tier skills ARE permitted when the workflow opts in by name."""
    workspace = _make_skill_info(
        tmp_path,
        name="workspace-skill",
        trust_tier="workspace",
    )
    ctx.config.discovered_skills = [workspace]

    calls: list[str] = []

    async def fake_activate(ctx_arg, info):
        calls.append(info.name)
        ctx_arg.skills.activated[info.name] = ""

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal",
        fake_activate,
    )

    await activate_skills_for_workflow(ctx, ["workspace-skill"])

    assert calls == ["workspace-skill"]
    assert "workspace-skill" in ctx.skills.activated
