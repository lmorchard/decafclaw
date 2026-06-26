from types import SimpleNamespace

import pytest

from decafclaw.confirmations import (
    ConfirmationAction,
    ConfirmationRequest,
    ConfirmationResponse,
)
from decafclaw.workflow.journal import (
    Journal,
    fingerprint,
    load_journal,
    save_journal,
)


def test_workflow_user_input_action_exists():
    assert ConfirmationAction.WORKFLOW_USER_INPUT.value == "workflow_user_input"


async def _noop(*args, **kwargs):
    pass


def _ctx(tmp_path, conv_id="convR"):
    # run_workflow_turn now activates skills (Phase 4 of #580); the helper
    # iterates ctx.config.discovered_skills, so provide an empty list plus
    # the ctx.skills / ctx.tools fields the activation helpers touch.
    return SimpleNamespace(
        config=SimpleNamespace(
            workspace_path=tmp_path,
            discovered_skills=[],
        ),
        conv_id=conv_id,
        publish=_noop,
        skills=SimpleNamespace(activated=set()),
        tools=SimpleNamespace(extra={}, extra_definitions=[]),
    )


@pytest.mark.asyncio
async def test_handler_journals_answer_and_enqueues_resume(tmp_path):
    from decafclaw.workflow.resume import WorkflowUserInputHandler

    cfg = SimpleNamespace(workspace_path=tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    save_journal(cfg, "convR", j)

    fp = fingerprint("user_input",
                     {"prompt": "What should this interview be about?",
                      "choices": None})
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="What should this interview be about?",
        action_data={"workflow_name": "interview", "seq": "0",
                     "args_fingerprint": fp, "prompt": "...", "choices": None},
        timeout=None,
    )
    response = ConfirmationResponse(confirmation_id="x", approved=True,
                                    data={"value": "tide pools"})

    enqueued = []

    class FakeManager:
        config = cfg
        async def enqueue_turn(self, conv_id, **kw):
            enqueued.append((conv_id, kw))

    handler = WorkflowUserInputHandler(FakeManager())
    out = await handler.on_approve(_ctx(tmp_path), request, response)

    reloaded = load_journal(cfg, "convR")
    assert reloaded.get((0,)).kind == "user_input"
    assert reloaded.get((0,)).result == "tide pools"
    assert reloaded.status == "running"
    assert enqueued and enqueued[0][1]["metadata"]["resume"] is True
    assert enqueued[0][1]["metadata"]["workflow_name"] == "interview"
    assert out == {"continue_loop": False}


@pytest.mark.asyncio
async def test_run_workflow_turn_fresh_start_suspends_and_posts(tmp_path):
    import decafclaw.workflow.workflows  # noqa: F401 — register interview
    from decafclaw.workflow.resume import run_workflow_turn

    posted = []

    class FakeManager:
        config = SimpleNamespace(workspace_path=tmp_path)
        async def post_confirmation(self, conv_id, request):
            posted.append(request)

    ctx = _ctx(tmp_path, "convT")
    result = await run_workflow_turn(
        ctx, FakeManager(),
        workflow_name="interview", resume=False)
    assert posted, "a confirmation should be posted on suspend"
    assert posted[0].action_type == ConfirmationAction.WORKFLOW_USER_INPUT
    # action_data carries the tuple-path seq as a dotted string for JSON safety.
    assert posted[0].action_data["seq"] == "0"
    assert posted[0].action_data["workflow_name"] == "interview"
    from decafclaw.media import ToolResult
    assert isinstance(result, ToolResult)
    assert result.text.startswith("_")  # italic-wrapped prompt


@pytest.mark.asyncio
async def test_run_workflow_turn_done_archives_artifact(config):
    """On status=done, run_workflow_turn must persist the rendered artifact
    as a role=assistant archive row, so a conversation reload still has the
    final output visible (verification finding #2 for PR #573)."""
    from decafclaw.archive import read_archive
    from decafclaw.workflow.journal import Journal, save_journal
    from decafclaw.workflow.registry import workflow as register_workflow
    from decafclaw.workflow.resume import run_workflow_turn

    conv_id = "convDone"

    @register_workflow("artifact_done_test")
    async def _wf(wf):
        return {"title": "Tide Pools", "body": "Brief notes."}

    # Pre-seed an empty journal so run_workflow_turn treats this as a resume
    # path without needing user_input — the workflow returns immediately.
    save_journal(config, conv_id, Journal(workflow_name="artifact_done_test"))

    from types import SimpleNamespace

    async def _noop(*args, **kwargs):
        pass

    class FakeManager:
        async def post_confirmation(self, conv_id, request):
            raise AssertionError("done path should not post a confirmation")

    ctx = SimpleNamespace(config=config, conv_id=conv_id, publish=_noop)
    result = await run_workflow_turn(
        ctx, FakeManager(),
        workflow_name="artifact_done_test", resume=True)
    assert "Tide Pools" in result.text

    msgs = read_archive(config, conv_id)
    assistant_msgs = [m for m in msgs if m.get("role") == "assistant"]
    assert assistant_msgs, (
        "expected a role=assistant archive row for the workflow artifact"
    )
    assert "Tide Pools" in (assistant_msgs[-1].get("content") or "")


def _make_skill_info(tmp_path, name, *, trust_tier="bundled", always_loaded=False):
    """Lightweight SkillInfo factory mirroring tests/test_skills.py's helper."""
    from decafclaw.skills import SkillInfo
    location = tmp_path / name
    location.mkdir(parents=True, exist_ok=True)
    info = SkillInfo(
        name=name, description=f"{name} description",
        location=location, body="Instructions here.",
        has_native_tools=False,
        trust_tier=trust_tier,
    )
    info.always_loaded = always_loaded
    return info


@pytest.mark.asyncio
async def test_run_workflow_turn_activates_always_loaded_before_orchestrator(
    config, ctx, tmp_path, monkeypatch,
):
    """Always-loaded skills are activated and their tools are visible in
    ctx.tools.extra by the time run_workflow is invoked."""
    from decafclaw.workflow.registry import REGISTRY
    from decafclaw.workflow.registry import workflow as register_workflow
    from decafclaw.workflow.resume import run_workflow_turn

    skill = _make_skill_info(tmp_path, "alpha", always_loaded=True)
    ctx.config.discovered_skills = [skill]
    ctx.conv_id = "convAlwaysLoaded"

    async def fake_activate(ctx_arg, info):
        ctx_arg.tools.extra["fake_tool"] = lambda *_: None
        ctx_arg.skills.activated.add(info.name)

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal", fake_activate,
    )

    captured = {}

    async def fake_run_workflow(ctx_arg, fn, journal, *, model):
        # Snapshot at the moment run_workflow is called.
        captured["tools_extra"] = dict(ctx_arg.tools.extra)
        from decafclaw.workflow.engine import WorkflowOutcome
        return WorkflowOutcome(status="done", result="ok")

    monkeypatch.setattr(
        "decafclaw.workflow.resume.run_workflow", fake_run_workflow,
    )

    @register_workflow("wf_always_loaded_test")
    async def _wf(wf):
        return "ok"

    try:
        class FakeManager:
            async def post_confirmation(self, conv_id, request):
                raise AssertionError("done path should not post a confirmation")

        await run_workflow_turn(
            ctx, FakeManager(),
            workflow_name="wf_always_loaded_test", resume=False)

        assert "fake_tool" in captured["tools_extra"], (
            "always-loaded skill's tool must be in ctx.tools.extra "
            "before run_workflow is called"
        )
    finally:
        REGISTRY.pop("wf_always_loaded_test", None)


@pytest.mark.asyncio
async def test_run_workflow_turn_activates_requires_skills(
    config, ctx, tmp_path, monkeypatch,
):
    """A workflow's declared requires_skills are activated and their tools
    land in ctx.tools.extra before the orchestrator runs."""
    from decafclaw.workflow.registry import REGISTRY
    from decafclaw.workflow.registry import workflow as register_workflow
    from decafclaw.workflow.resume import run_workflow_turn

    skill = _make_skill_info(tmp_path, "tabstack-like")
    ctx.config.discovered_skills = [skill]
    ctx.conv_id = "convRequiresSkills"

    async def fake_activate(ctx_arg, info):
        ctx_arg.tools.extra["fake_tool"] = lambda *_: None
        ctx_arg.skills.activated.add(info.name)

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal", fake_activate,
    )

    captured = {}

    async def fake_run_workflow(ctx_arg, fn, journal, *, model):
        captured["tools_extra"] = dict(ctx_arg.tools.extra)
        from decafclaw.workflow.engine import WorkflowOutcome
        return WorkflowOutcome(status="done", result="ok")

    monkeypatch.setattr(
        "decafclaw.workflow.resume.run_workflow", fake_run_workflow,
    )

    @register_workflow(
        "wf_requires_skills_test", requires_skills=("tabstack-like",),
    )
    async def _wf(wf):
        return "ok"

    try:
        class FakeManager:
            async def post_confirmation(self, conv_id, request):
                raise AssertionError("done path should not post a confirmation")

        await run_workflow_turn(
            ctx, FakeManager(),
            workflow_name="wf_requires_skills_test", resume=False)

        assert "fake_tool" in captured["tools_extra"], (
            "requires_skills skill's tool must be in ctx.tools.extra "
            "before run_workflow is called"
        )
    finally:
        REGISTRY.pop("wf_requires_skills_test", None)


@pytest.mark.asyncio
async def test_run_workflow_turn_returns_error_on_activation_failure(
    config, ctx, tmp_path, monkeypatch,
):
    """When a requires_skills entry is unknown, activate_skills_for_workflow
    raises WorkflowSkillActivationFailed; the turn returns an error ToolResult,
    persists journal.status='error', and does NOT invoke run_workflow."""
    from decafclaw.media import ToolResult
    from decafclaw.workflow.journal import load_journal
    from decafclaw.workflow.registry import REGISTRY
    from decafclaw.workflow.registry import workflow as register_workflow
    from decafclaw.workflow.resume import run_workflow_turn

    ctx.config.discovered_skills = []  # no skills discovered → bogus name
    ctx.conv_id = "convActivationFail"

    sabotage_called = {"value": False}

    async def sabotage_run_workflow(*args, **kwargs):
        sabotage_called["value"] = True
        raise AssertionError("should not run")

    monkeypatch.setattr(
        "decafclaw.workflow.resume.run_workflow", sabotage_run_workflow,
    )

    @register_workflow(
        "wf_activation_fail_test", requires_skills=("missing-skill",),
    )
    async def _wf(wf):
        return "ok"

    try:
        class FakeManager:
            async def post_confirmation(self, conv_id, request):
                raise AssertionError("error path should not post a confirmation")

        result = await run_workflow_turn(
            ctx, FakeManager(),
            workflow_name="wf_activation_fail_test", resume=False)

        assert isinstance(result, ToolResult)
        assert result.text.startswith("[error: skill activation failed:")
        assert "missing-skill" in result.text
        assert sabotage_called["value"] is False

        # Journal status must be persisted as "error".
        journal = load_journal(ctx.config, ctx.conv_id)
        assert journal is not None
        assert journal.status == "error"
    finally:
        REGISTRY.pop("wf_activation_fail_test", None)


@pytest.mark.asyncio
async def test_run_workflow_turn_activation_idempotent_on_resume(
    config, ctx, tmp_path, monkeypatch,
):
    """If a requires_skills entry is already in ctx.skills.activated (e.g.
    after a resume), activate_skill_internal is NOT called again — the
    helper's idempotency guard short-circuits before the inner call."""
    from decafclaw.workflow.registry import REGISTRY
    from decafclaw.workflow.registry import workflow as register_workflow
    from decafclaw.workflow.resume import run_workflow_turn

    skill = _make_skill_info(tmp_path, "foo-skill")
    ctx.config.discovered_skills = [skill]
    ctx.skills.activated = {"foo-skill"}
    ctx.conv_id = "convIdempotent"

    async def sabotage_activate(ctx_arg, info):
        raise AssertionError("should not be called on resume")

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools.activate_skill_internal", sabotage_activate,
    )

    async def fake_run_workflow(ctx_arg, fn, journal, *, model):
        from decafclaw.workflow.engine import WorkflowOutcome
        return WorkflowOutcome(status="done", result="ok")

    monkeypatch.setattr(
        "decafclaw.workflow.resume.run_workflow", fake_run_workflow,
    )

    @register_workflow(
        "wf_idempotent_test", requires_skills=("foo-skill",),
    )
    async def _wf(wf):
        return "ok"

    try:
        class FakeManager:
            async def post_confirmation(self, conv_id, request):
                raise AssertionError("done path should not post a confirmation")

        result = await run_workflow_turn(
            ctx, FakeManager(),
            workflow_name="wf_idempotent_test", resume=False)

        from decafclaw.media import ToolResult
        assert isinstance(result, ToolResult)
        # Sabotage mock NOT called — assertion proven by the test not raising.
    finally:
        REGISTRY.pop("wf_idempotent_test", None)


@pytest.mark.asyncio
async def test_handler_deny_marks_error_and_archives(tmp_path):
    from decafclaw.workflow.resume import WorkflowUserInputHandler

    cfg = SimpleNamespace(workspace_path=tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    save_journal(cfg, "convD", j)
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="q?", action_data={"workflow_name": "interview", "seq": 0,
        "args_fingerprint": "fp", "prompt": "q?", "choices": None}, timeout=None)
    response = ConfirmationResponse(confirmation_id="x", approved=False)

    class FakeManager:
        config = cfg
        async def enqueue_turn(self, conv_id, **kw):
            raise AssertionError("deny must NOT enqueue a resume turn")

    handler = WorkflowUserInputHandler(FakeManager())
    out = await handler.on_deny(_ctx(tmp_path, "convD"), request, response)
    assert out == {"continue_loop": False}
    assert load_journal(cfg, "convD").status == "error"
