"""Tests for skill permissions, activation, and invocation gates (split from #1419)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from decafclaw.context import TaskMode
from decafclaw.context_composer import render_activated_skills
from decafclaw.media import ToolResult
from decafclaw.schedules import _render_required_skill_bodies
from decafclaw.skills import (
    SkillInfo,
    discover_skills,
    find_command,
    find_skill,
    index_skills,
    parse_skill_md,
    substitute_skill_placeholders,
)
from decafclaw.tools.skill_tools import (
    _compute_skill_hash,
    _load_permissions,
    _save_permission,
    activate_skill_internal,
    tool_activate_skill,
)
from tests.skill_helpers import _make_skill_info, _text, _write_skill, _write_ws_skill

# -- permissions tests --


def test_load_permissions_missing_file(config):
    """Returns empty dict when permissions file doesn't exist."""
    perms = _load_permissions(config)
    assert perms == {}


def test_save_and_load_permission(config):
    """Saves a permission and loads it back."""
    _save_permission(config, "tabstack", {"status": "always", "hash": ""})
    perms = _load_permissions(config)
    assert perms["tabstack"] == {"status": "always", "hash": ""}


def test_save_permission_preserves_existing(config):
    """Saving a new permission preserves existing ones."""
    _save_permission(config, "alpha", {"status": "always", "hash": ""})
    _save_permission(config, "beta", {"status": "always", "hash": ""})
    perms = _load_permissions(config)
    assert perms["alpha"] == {"status": "always", "hash": ""}
    assert perms["beta"] == {"status": "always", "hash": ""}


# -- activation tests --


@pytest.mark.asyncio
async def test_activate_unknown_skill(ctx):
    """Activating an unknown skill returns an error."""
    ctx.config.discovered_skills = []
    result = await tool_activate_skill(ctx, name="nonexistent")
    assert "[error:" in _text(result)
    assert "not found" in _text(result)


@pytest.mark.asyncio
async def test_activate_already_active(ctx, tmp_path):
    """Re-activating returns 'already active'."""
    skill = _make_skill_info(tmp_path)
    ctx.config.discovered_skills = [skill]
    ctx.skills.activated = {skill.name: _compute_skill_hash(skill)}
    _save_permission(ctx.config, skill.name, {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name=skill.name)
    assert "already active" in _text(result)


@pytest.mark.asyncio
async def test_activate_with_always_permission(ctx, tmp_path):
    """Skill with 'always' permission activates without confirmation."""
    skill = _make_skill_info(tmp_path)
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, skill.name, {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name=skill.name)
    assert f"Activated skill '{skill.name}'." in _text(result)
    assert skill.name in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_with_auto_approve_skips_confirmation(ctx, tmp_path):
    """Skill with auto_approve=True activates without a confirmation prompt."""
    skill = _make_skill_info(tmp_path)
    skill.auto_approve = True
    ctx.config.discovered_skills = [skill]

    # If confirmation were requested, this would hang (no handler wired
    # on this test ctx). Successful activation proves it was skipped.
    result = await tool_activate_skill(ctx, name=skill.name)
    assert f"Activated skill '{skill.name}'." in _text(result)
    assert skill.name in ctx.skills.activated


@pytest.mark.asyncio
async def test_auto_approve_blocked_by_explicit_deny(ctx, tmp_path):
    """User's explicit 'deny' in permissions overrides auto-approve."""
    skill = _make_skill_info(tmp_path)
    skill.auto_approve = True
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, skill.name, "deny")

    result = await tool_activate_skill(ctx, name=skill.name)
    assert "[error:" in _text(result)
    assert "denied" in _text(result)
    assert skill.name not in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_trusted_admin_tier_bypasses_confirmation(ctx, tmp_path):
    """Admin-tier skill activates without confirmation (trust by placement)."""
    skill = _make_skill_info(tmp_path, trust_tier="admin")
    ctx.config.discovered_skills = [skill]
    # No permission record, no auto_approve flag — only the trust tier
    # should allow activation to proceed.
    result = await tool_activate_skill(ctx, name=skill.name)
    assert f"Activated skill '{skill.name}'." in _text(result)
    assert skill.name in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_trusted_extra_tier_bypasses_confirmation(ctx, tmp_path):
    """Extra-path-tier skill activates without confirmation."""
    skill = _make_skill_info(tmp_path, trust_tier="extra")
    ctx.config.discovered_skills = [skill]
    result = await tool_activate_skill(ctx, name=skill.name)
    assert f"Activated skill '{skill.name}'." in _text(result)
    assert skill.name in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_trusted_tier_no_permission_write(ctx, tmp_path):
    """Trusted-tier activation does NOT write to skill_permissions.json —
    placement is implicit trust, no need to record it."""
    skill = _make_skill_info(tmp_path, trust_tier="extra")
    ctx.config.discovered_skills = [skill]
    await tool_activate_skill(ctx, name=skill.name)
    perms = _load_permissions(ctx.config)
    assert skill.name not in perms


@pytest.mark.asyncio
async def test_deny_still_wins_over_trust_tier(ctx, tmp_path):
    """A user's explicit 'deny' record overrides the trust-tier bypass."""
    skill = _make_skill_info(tmp_path, trust_tier="extra")
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, skill.name, "deny")
    result = await tool_activate_skill(ctx, name=skill.name)
    assert "[error:" in _text(result)
    assert "denied" in _text(result)
    assert skill.name not in ctx.skills.activated


@pytest.mark.asyncio
async def test_workspace_skill_still_requires_confirmation(
    ctx,
    tmp_path,
    monkeypatch,
):
    """Workspace-tier skill activation routes through the confirmation flow
    even when no permission record exists. (Test mocks the confirmation
    handler to deny, verifying activation does NOT proceed.)"""
    skill = _make_skill_info(tmp_path, trust_tier="workspace")
    ctx.config.discovered_skills = [skill]

    async def fake_request(_ctx, _name):
        return (False, False)  # denied, not always

    monkeypatch.setattr(
        "decafclaw.tools.skill_tools._request_skill_confirmation",
        fake_request,
    )
    result = await tool_activate_skill(ctx, name=skill.name)
    assert "denied" in _text(result)
    assert skill.name not in ctx.skills.activated


@pytest.mark.asyncio
async def test_activate_substitutes_skill_dir_in_body(ctx, tmp_path):
    """The composer's activated-skills block substitutes `$SKILL_DIR` with the
    skill's actual location.

    Without this, a skill body containing `$SKILL_DIR/fetch.sh` is
    shown verbatim to the LLM, which then has to guess where the
    skill lives. That guess fails for skills loaded via
    `extra_skill_paths` (e.g. shared optional skills in `contrib/`)
    because their location isn't conventional. The substitution routes
    through the shared `substitute_skill_placeholders` helper, matching the
    command and schedule paths.
    """
    skill = _make_skill_info(
        tmp_path,
        name="dir-test",
        body="Run: $SKILL_DIR/fetch.sh",
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, skill.name, {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name=skill.name)
    assert f"Activated skill '{skill.name}'." in _text(result)

    rendered = render_activated_skills(ctx)
    assert rendered is not None
    block = rendered[0]["content"]
    assert "$SKILL_DIR" not in block
    # The substitution uses .resolve() to match the command/schedule paths
    # so the body always carries an absolute path regardless of data_home.
    assert f"Run: {skill.location.resolve()}/fetch.sh" in block


# -- substitute_skill_placeholders ---------------------------------------------


class TestSubstituteSkillPlaceholders:
    """The single placeholder-expansion helper shared by every body renderer."""

    def test_arguments_placeholder(self):
        result = substitute_skill_placeholders("Do the thing with $ARGUMENTS", arguments="foo bar")
        assert result == "Do the thing with foo bar"

    def test_positional_args(self):
        result = substitute_skill_placeholders("Move $0 to $1", arguments="today tomorrow")
        assert result == "Move today to tomorrow"

    def test_mixed(self):
        result = substitute_skill_placeholders("City: $0\nFull: $ARGUMENTS", arguments="Portland OR")
        assert result == "City: Portland\nFull: Portland OR"

    def test_no_placeholder_appends(self):
        result = substitute_skill_placeholders("Do the thing.", arguments="extra context")
        assert "ARGUMENTS: extra context" in result

    def test_no_arguments_replaces_with_empty(self):
        # $ARGUMENTS is replaced even when the argument string is empty.
        result = substitute_skill_placeholders("Do the thing. $ARGUMENTS", arguments="")
        assert result == "Do the thing. "

    def test_out_of_range_positional_left_literal(self):
        result = substitute_skill_placeholders("Use $0 and $5", arguments="only-one")
        assert "only-one" in result
        assert "$5" in result  # unreplaced

    def test_skill_dir(self):
        result = substitute_skill_placeholders("Run $SKILL_DIR/fetch.sh", skill_dir="/skills/x")
        assert result == "Run /skills/x/fetch.sh"

    def test_skill_dir_unset_leaves_literal(self):
        # An empty skill_dir leaves $SKILL_DIR untouched.
        result = substitute_skill_placeholders("Run $SKILL_DIR/fetch.sh")
        assert result == "Run $SKILL_DIR/fetch.sh"


@pytest.mark.asyncio
async def test_all_three_entry_points_expand_identically(ctx, config, tmp_path):
    """The command path, the composer's activated-skills block, and the
    schedule's required-skill renderer must expand
    `$SKILL_DIR`/`$ARGUMENTS`/positional placeholders identically — they all
    route through `substitute_skill_placeholders`.

    Regression guard: before centralization, activation and the schedule
    renderer only substituted `$SKILL_DIR` (leaving `$ARGUMENTS` literal)
    while the command path substituted it, so the same body expanded to
    different text depending on how it was reached.
    """
    skill_dir = tmp_path / "demo"
    skill_dir.mkdir()
    dir_str = str(skill_dir.resolve())
    body = "Run $SKILL_DIR/fetch.sh with $ARGUMENTS and $0"

    # The helper is the single source of truth.
    expected = substitute_skill_placeholders(body, arguments="", skill_dir=dir_str)

    # Entry point 1: the helper itself (the command path calls this).
    assert substitute_skill_placeholders(body, arguments="", skill_dir=dir_str) == expected

    # Entry point 2: the composer's activated-skills block.
    skill = SkillInfo(name="demo", description="Demo", location=skill_dir, body=body, trust_tier="bundled")
    ctx.config.discovered_skills = [skill]
    activated = await activate_skill_internal(ctx, skill)
    assert isinstance(activated, str)
    rendered_block = render_activated_skills(ctx)
    assert rendered_block is not None
    block = rendered_block[0]["content"]
    inner = block.split('<skill name="demo">\n', 1)[1].rsplit("\n</skill>", 1)[0]
    assert inner == expected

    # Entry point 3: the schedule's required-skill renderer.
    config.discovered_skills = [skill]
    rendered = _render_required_skill_bodies(config, ["demo"])
    assert '<skill name="demo">' in rendered
    inner = rendered.split('<skill name="demo">\n', 1)[1].rsplit("\n</skill>", 1)[0]
    assert inner == expected


@pytest.mark.asyncio
async def test_discover_bundled_tabstack(config, monkeypatch):
    """Bundled tabstack skill is discovered when TABSTACK_API_KEY is set."""
    monkeypatch.setenv("TABSTACK_API_KEY", "test-key")
    skills = discover_skills(config)
    assert any(s.name == "tabstack" for s in skills)


@pytest.mark.asyncio
async def test_discover_bundled_tabstack_skipped_without_key(config, monkeypatch):
    """Bundled tabstack skill is NOT discovered when TABSTACK_API_KEY is missing."""
    monkeypatch.delenv("TABSTACK_API_KEY", raising=False)
    skills = discover_skills(config)
    assert not any(s.name == "tabstack" for s in skills)


@pytest.mark.asyncio
async def test_activate_native_skill(ctx, tmp_path):
    """Native skill activation imports tools.py and registers tools on ctx."""
    skill_dir = tmp_path / "native-skill"
    skill_dir.mkdir(parents=True)
    # Create a tools.py with a simple tool
    (skill_dir / "tools.py").write_text(
        "TOOLS = {'native_test': lambda ctx: 'hello'}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'native_test'}}]\n"
        "_init_called = False\n"
        "def init(config):\n"
        "    global _init_called\n"
        "    _init_called = True\n"
    )
    skill = SkillInfo(
        name="native-skill",
        description="Native test.",
        location=skill_dir,
        body="Native instructions.",
        has_native_tools=True,
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "native-skill", {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name="native-skill")
    assert "Activated skill 'native-skill'." in _text(result)
    assert "Native instructions." not in _text(result)
    assert "native_test" in _text(result)
    assert "native_test" in ctx.tools.extra
    assert len(ctx.tools.extra_definitions) == 1


@pytest.mark.asyncio
async def test_activate_shadowed_skill_with_inherited_tools(ctx, tmp_path):
    """Activating a skill that inherited tools loads tools from tools_location."""
    base_dir = tmp_path / "base-skill"
    base_dir.mkdir(parents=True)
    (base_dir / "tools.py").write_text(
        "TOOLS = {'inherited_test': lambda ctx: 'from_base'}\n"
        "TOOL_DEFINITIONS = [{'type': 'function', 'function': {'name': 'inherited_test'}}]\n"
    )
    override_dir = tmp_path / "override-skill"
    override_dir.mkdir(parents=True)

    skill = SkillInfo(
        name="inherited-skill",
        description="Override test.",
        location=override_dir,
        body="Overridden instructions.",
        has_native_tools=True,
        tools_location=base_dir,
    )
    ctx.config.discovered_skills = [skill]
    _save_permission(ctx.config, "inherited-skill", {"status": "always", "hash": _compute_skill_hash(skill)})

    result = await tool_activate_skill(ctx, name="inherited-skill")
    assert "Activated skill 'inherited-skill'." in _text(result)
    assert "Overridden instructions." not in _text(result)
    assert "inherited_test" in _text(result)
    assert "inherited_test" in ctx.tools.extra
    assert len(ctx.tools.extra_definitions) == 1


@pytest.mark.asyncio
async def test_activate_skill_rediscovers_when_missing_from_catalog(ctx):
    """activate_skill and refresh_skills run concurrently under
    asyncio.gather, so activate can read config.discovered_skills before
    refresh replaces it. On a miss, re-discover before reporting not-found
    rather than emitting a nondeterministic contradiction."""
    from decafclaw.tools.skill_tools import _save_permission, tool_activate_skill

    _write_ws_skill(ctx, "latecomer", "name: latecomer\ndescription: New skill.", body="Latecomer body.")
    # Pre-approve: this test is about the catalog miss, not the workspace-tier
    # confirmation gate that would otherwise block activation.
    _save_permission(ctx.config, "latecomer", {"status": "always", "hash": ""})
    # Simulate the stale snapshot: the skill exists on disk but the catalog
    # this call sees predates it.
    ctx.config.discovered_skills = []

    result = await tool_activate_skill(ctx, name="latecomer")
    assert "not found" not in _text(result)
    assert "latecomer" in ctx.skills.activated
    # The catalog is repaired too, so restore_skills finds it on later turns.
    assert "latecomer" in {s.name for s in ctx.config.discovered_skills}


@pytest.mark.asyncio
async def test_activate_skill_still_reports_genuinely_missing_skill(ctx):
    from decafclaw.tools.skill_tools import tool_activate_skill

    ctx.config.discovered_skills = []
    result = await tool_activate_skill(ctx, name="no-such-skill-anywhere")
    assert "not found" in _text(result)


@pytest.mark.asyncio
async def test_activation_warns_when_skill_tool_shadows_core_tool(ctx):
    """Shadowing a core tool name is legal — execute_tool checks
    ctx.tools.extra before the global registry, so the skill's version
    genuinely wins — but nothing used to say a word about it. The agent that
    hit #684 wrote a `debug_context` tool with no idea one already existed.

    Inform rather than reject: the override is supported, the silence isn't.
    """
    from decafclaw.tools import TOOL_DEFINITIONS
    from decafclaw.tools.skill_tools import _save_permission, tool_activate_skill

    assert "debug_context" in {td["function"]["name"] for td in TOOL_DEFINITIONS}

    _write_ws_skill(
        ctx,
        "shadower",
        "name: shadower\ndescription: Shadows a core tool.",
        tools_py=(
            "def debug_context(ctx):\n    return 'mine'\n\n"
            "TOOLS = {'debug_context': debug_context}\n"
            "TOOL_DEFINITIONS = [{'type': 'function', "
            "'function': {'name': 'debug_context'}}]\n"
        ),
    )
    ctx.config.discovered_skills = discover_skills(ctx.config)
    _save_permission(
        ctx.config,
        "shadower",
        {
            "status": "always",
            "hash": _compute_skill_hash(find_skill(index_skills(ctx.config.discovered_skills), "shadower")),
        },
    )

    result = _text(await tool_activate_skill(ctx, name="shadower"))

    assert "shadower" in ctx.skills.activated, "shadowing must not block activation"
    assert "debug_context" in result
    assert "shadow" in result.lower()


@pytest.mark.asyncio
async def test_shadow_warnings_are_ordered_deterministically(ctx):
    """Shadow notes go into the activation result the LLM reads, so their
    order must not depend on set iteration — string hashing is randomized
    per process, so an unsorted set would emit these in a different order
    between runs."""
    from decafclaw.tools.skill_tools import _save_permission, tool_activate_skill

    _write_ws_skill(
        ctx,
        "multishadow",
        "name: multishadow\ndescription: Shadows two core tools.",
        tools_py=(
            "def debug_context(ctx):\n    return 'mine'\n\n"
            "def shell(ctx):\n    return 'mine'\n\n"
            "TOOLS = {'debug_context': debug_context, 'shell': shell}\n"
            "TOOL_DEFINITIONS = [\n"
            "    {'type': 'function', 'function': {'name': 'debug_context'}},\n"
            "    {'type': 'function', 'function': {'name': 'shell'}},\n"
            "]\n"
        ),
    )
    ctx.config.discovered_skills = discover_skills(ctx.config)
    _save_permission(
        ctx.config,
        "multishadow",
        {
            "status": "always",
            "hash": _compute_skill_hash(find_skill(index_skills(ctx.config.discovered_skills), "multishadow")),
        },
    )

    result = _text(await tool_activate_skill(ctx, name="multishadow"))

    # Both reported, in sorted order.
    assert result.index("'debug_context' shadows") < result.index("'shell' shadows")


@pytest.mark.asyncio
async def test_skill_invocation_flags_enforced(ctx, tmp_path):
    # 1. user_invocable=False -> find_command should return None
    skill_dir1 = tmp_path / "hidden_from_user"
    _write_skill(skill_dir1, "name: hidden_from_user\ndescription: desc\nuser-invocable: false")

    # 2. disable_model_invocation=True -> tool_activate_skill should reject
    skill_dir2 = tmp_path / "hidden_from_model"
    _write_skill(skill_dir2, "name: hidden_from_model\ndescription: desc\ndisable-model-invocation: true")

    info1 = parse_skill_md(skill_dir1 / "SKILL.md")
    info2 = parse_skill_md(skill_dir2 / "SKILL.md")

    ctx.config.discovered_skills = [info1, info2]

    # User invocation enforcement
    assert find_command("hidden_from_user", ctx.config.discovered_skills) is None

    # Model invocation enforcement
    from decafclaw.tools.skill_tools import tool_activate_skill

    result = await tool_activate_skill(ctx, "hidden_from_model")
    assert isinstance(result, ToolResult)
    assert (
        "not allowed" in result.text.lower()
        or "disabled" in result.text.lower()
        or "cannot be activated" in result.text.lower()
    )


@pytest.mark.asyncio
async def test_skill_allowed_tools_enforced(ctx, tmp_path):
    skill_dir = tmp_path / "restricted_skill"
    _write_skill(skill_dir, "name: restricted_skill\ndescription: desc\nallowed-tools: [my_tool1]", tools_py=True)
    (skill_dir / "tools.py").write_text("""
async def my_tool1(ctx): return 1
async def my_tool2(ctx): return 2
TOOLS = {"my_tool1": my_tool1, "my_tool2": my_tool2}
TOOL_DEFINITIONS = [
    {"function": {"name": "my_tool1", "description": "1"}},
    {"function": {"name": "my_tool2", "description": "2"}}
]
""")
    from decafclaw.skills import parse_skill_md

    info = parse_skill_md(skill_dir / "SKILL.md")
    ctx.config.discovered_skills = [info]

    from decafclaw.tool_definitions import build_tool_list
    from decafclaw.tools.skill_tools import activate_skill_internal

    await activate_skill_internal(ctx, info)
    tools, text, _ = build_tool_list(ctx)
    tool_names = [t["function"]["name"] for t in tools]
    assert "my_tool1" in tool_names
    assert "my_tool2" not in tool_names


@pytest.mark.asyncio
async def test_skill_tool_conflict_resolution(ctx, tmp_path):
    skill1_dir = tmp_path / "skill1"
    _write_skill(skill1_dir, "name: skill1\ndescription: 1", tools_py=True)
    (skill1_dir / "tools.py").write_text("""
async def my_tool(ctx): return "skill1"
TOOLS = {"my_tool": my_tool}
TOOL_DEFINITIONS = [{"function": {"name": "my_tool", "description": "1"}}]
""")

    skill2_dir = tmp_path / "skill2"
    _write_skill(skill2_dir, "name: skill2\ndescription: 2", tools_py=True)
    (skill2_dir / "tools.py").write_text("""
async def my_tool(ctx): return "skill2"
TOOLS = {"my_tool": my_tool}
TOOL_DEFINITIONS = [{"function": {"name": "my_tool", "description": "2"}}]
""")

    from decafclaw.skills import parse_skill_md

    info1 = parse_skill_md(skill1_dir / "SKILL.md")
    info2 = parse_skill_md(skill2_dir / "SKILL.md")

    ctx.config.discovered_skills = [info1, info2]

    from decafclaw.tools.skill_tools import activate_skill_internal

    await activate_skill_internal(ctx, info1)
    await activate_skill_internal(ctx, info2)

    from decafclaw.tool_definitions import build_tool_list

    tools, text, _ = build_tool_list(ctx)

    my_tools = [t for t in tools if "my_tool" in t["function"]["name"]]
    assert len(my_tools) == 2
    tool_names = {t["function"]["name"] for t in my_tools}
    assert tool_names == {"my_tool", "skill2__my_tool"}

    from decafclaw.tool_execution import execute_single_tool

    # We must mock execute_single_tool's use of asyncio.Semaphore
    # But wait, we can just call execute_tool directly.
    from decafclaw.tools import execute_tool

    res1 = await execute_tool(ctx, "my_tool", {})
    assert res1.text == "skill1"

    res2 = await execute_tool(ctx, "skill2__my_tool", {})
    assert res2.text == "skill2"


@pytest.mark.asyncio
async def test_unattended_turn_records_activation_denial(ctx, tmp_path):
    """When a skill activation is denied on an unattended turn, activation_denials is recorded."""
    skill = _make_skill_info(tmp_path, trust_tier="workspace", name="ws-denied-skill")
    ctx.config.discovered_skills = [skill]
    ctx.task_mode = TaskMode.SCHEDULED

    result = await tool_activate_skill(ctx, name="ws-denied-skill")
    assert "denied by user" in (result.text if hasattr(result, "text") else str(result))
    assert "ws-denied-skill" in ctx.skills.activation_denials


@pytest.mark.asyncio
async def test_scheduled_task_fails_when_required_skill_denied(config, tmp_path):
    """Scheduled task fails loudly with is_ok=False when required skill activation fails or is skipped."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus
    from decafclaw.schedules import ScheduleTask, run_schedule_task

    ws_skill_dir = config.workspace_path / "skills" / "ws-required"
    _write_skill(ws_skill_dir, "name: ws-required\ndescription: Workspace required.", tools_py=True)
    config.discovered_skills = discover_skills(config)

    task = ScheduleTask(
        name="test-newsletter",
        schedule="* * * * *",
        body="HEARTBEAT_OK — all quiet.",
        source="admin",
        path=Path("/fake/SCHEDULE.md"),
        required_skills=["ws-required"],
    )

    manager = ConversationManager(config, EventBus())

    # Stub run_agent_turn to return HEARTBEAT_OK text
    async def fake_run(turn_ctx, user_message, history, **kwargs):
        from decafclaw.media import ToolResult

        # Turn completes with HEARTBEAT_OK
        return ToolResult(
            text="HEARTBEAT_OK — nothing to report.",
            termination_reason="skill_activation_failed" if turn_ctx.skills.activation_denials else None,
        )

    with (
        patch("decafclaw.agent.run_agent_turn", side_effect=fake_run),
        patch("decafclaw.notifications.notify") as mock_notify,
    ):
        result = await run_schedule_task(config, EventBus(), manager, task)

    assert result["is_ok"] is False
    assert mock_notify.called
    kwargs = mock_notify.call_args.kwargs
    assert kwargs["priority"] == "high"
    assert "alert" in kwargs["title"].lower()
