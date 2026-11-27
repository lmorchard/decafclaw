"""Tool policy and model pinning for calendar wake turns (#1971, #1972).

The tests that matter here are the trust ones. Policy *restriction* is a
convenience; policy *grant* is an authorization boundary, and the whole point
of ``policy_granted`` is that an agent-authored event must never be able to
preapprove its own shell commands (#731).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from decafclaw.calendar_events import CalendarEvent
from decafclaw.calendar_policy import (
    activate_required_skills,
    apply_event_policy,
    split_allowed_tools,
    validate_tool_names,
)


def _event(**overrides) -> CalendarEvent:
    base = dict(
        event_id="cal_test",
        conv_id="conv-a",
        target_conv_id="conv-a",
        session_mode="current",
        title="Weekly",
        reason="Do the thing",
        fire_at="2027-03-15T09:00:00+00:00",
        fire_at_ts=1_800_000_000.0,
        created_at="2027-03-01T00:00:00+00:00",
        updated_at="2027-03-01T00:00:00+00:00",
    )
    base.update(overrides)
    return CalendarEvent(**base)


def _ctx(**overrides) -> SimpleNamespace:
    """Minimal context surface that apply_event_policy touches."""
    tools = SimpleNamespace(
        allowed=None,
        disallowed=set(),
        preapproved=set(),
        preapproved_shell_patterns=[],
    )
    ctx = SimpleNamespace(tools=tools, active_model="", config=None)
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


# -- parsing ----------------------------------------------------------------


def test_shell_entries_split_out_of_allowed_tools():
    tools, patterns = split_allowed_tools(["shell(git *)", "vault_read", "  shell(python3 *)  ", "", "current_time"])
    assert tools == ["vault_read", "current_time"]
    assert patterns == ["git *", "python3 *"]


def test_shell_pattern_is_rejected_in_a_plain_tool_list():
    with pytest.raises(ValueError, match="shell"):
        validate_tool_names(["shell(git *)"], field_name="disallowed_tools")


def test_validate_tool_names_trims_and_drops_blanks():
    assert validate_tool_names([" vault_read ", "", "  "], field_name="allowed_tools") == ["vault_read"]


# -- restriction (works without a grant) ------------------------------------


def test_allowed_tools_restrict_the_visible_set():
    event = _event(allowed_tools=["vault_read", "vault_write"])
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert ctx.tools.allowed == {"vault_read", "vault_write", "tool_search", "activate_skill"}


def test_escape_hatch_tools_stay_reachable():
    """An under-specified event must still be able to recover via tool_search."""
    event = _event(allowed_tools=["vault_read"])
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert {"tool_search", "activate_skill"} <= ctx.tools.allowed


def test_disallowed_tools_apply_without_a_grant():
    event = _event(disallowed_tools=["shell", "workspace_write"])
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert ctx.tools.disallowed == {"shell", "workspace_write"}


def test_a_shell_pattern_keeps_the_shell_tool_visible():
    event = _event(allowed_tools=["shell(git *)"], policy_granted=True)
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert "shell" in ctx.tools.allowed


def test_an_event_with_no_policy_changes_nothing():
    event = _event()
    ctx = _ctx()
    ctx.tools.allowed = {"only_these"}
    ctx.tools.preapproved = {"only_these"}
    apply_event_policy(ctx, event)
    assert ctx.tools.allowed == {"only_these"}
    assert ctx.tools.preapproved == {"only_these"}


# -- the trust boundary -----------------------------------------------------


def test_restriction_without_a_grant_withholds_preapproval():
    """An agent-authored event restricts; it does not authorize."""
    event = _event(
        allowed_tools=["shell(git *)", "vault_read"],
        shell_patterns=["git *"],
        policy_granted=False,
    )
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert ctx.tools.allowed == {"vault_read", "shell", "tool_search", "activate_skill"}
    assert ctx.tools.preapproved == set()
    assert ctx.tools.preapproved_shell_patterns == []


def test_a_granted_event_preapproves():
    event = _event(
        allowed_tools=["vault_read"],
        shell_patterns=["git *", "python3 *"],
        policy_granted=True,
    )
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert ctx.tools.preapproved == {"vault_read"}
    assert ctx.tools.preapproved_shell_patterns == ["git *", "python3 *"]


def test_a_non_granted_event_clears_inherited_preapproval():
    """Restriction must not inherit a standing grant from a previous turn.

    ``compose_wake_context_setup`` replays the conversation's last context
    setup, so a prior turn's preapproval would otherwise leak into a wake that
    never earned it.
    """
    event = _event(allowed_tools=["vault_read"], policy_granted=False)
    ctx = _ctx()
    ctx.tools.preapproved = {"workspace_write", "shell"}
    ctx.tools.preapproved_shell_patterns = ["rm *"]
    apply_event_policy(ctx, event)
    assert ctx.tools.preapproved == set()
    assert ctx.tools.preapproved_shell_patterns == []


def test_escape_hatch_tools_are_never_preapproved():
    event = _event(allowed_tools=["tool_search", "activate_skill"], policy_granted=True)
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert ctx.tools.preapproved == set()


# -- model pin (#1972) ------------------------------------------------------


def test_model_pin_is_applied():
    event = _event(model="vertex-gemini-3.8-flash")
    ctx = _ctx()
    apply_event_policy(ctx, event)
    assert ctx.active_model == "vertex-gemini-3.8-flash"


def test_empty_model_leaves_the_inherited_model_alone():
    event = _event()
    ctx = _ctx()
    ctx.active_model = "inherit-me"
    apply_event_policy(ctx, event)
    assert ctx.active_model == "inherit-me"


# -- new fields round-trip through the sidecar ------------------------------


def test_policy_fields_survive_a_save_and_load(tmp_path, config):
    from decafclaw.calendar_events import load_calendar_events, save_calendar_events

    config.agent.data_home = str(tmp_path / "data-home")
    event = _event(
        allowed_tools=["vault_read"],
        disallowed_tools=["shell"],
        shell_patterns=["git *"],
        required_skills=["vault"],
        policy_granted=True,
        model="vertex-gemini-3.8-flash",
    )
    save_calendar_events(config, "conv-a", [event])
    loaded = load_calendar_events(config, "conv-a")
    assert len(loaded) == 1
    got = loaded[0]
    assert got.allowed_tools == ["vault_read"]
    assert got.disallowed_tools == ["shell"]
    assert got.shell_patterns == ["git *"]
    assert got.required_skills == ["vault"]
    assert got.policy_granted is True
    assert got.model == "vertex-gemini-3.8-flash"


def test_an_old_sidecar_without_policy_fields_still_loads(tmp_path, config):
    """Backward compatibility: events written before #1971 have none of these keys."""
    import json

    from decafclaw.calendar_events import load_calendar_events
    from decafclaw.conversation_paths import calendar_path

    config.agent.data_home = str(tmp_path / "data-home")
    path = calendar_path(config, "conv-legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "event_id": "cal_old",
                        "conv_id": "conv-legacy",
                        "target_conv_id": "conv-legacy",
                        "session_mode": "current",
                        "title": "Old event",
                        "reason": "Written before policy existed",
                        "fire_at": "2027-03-15T09:00:00+00:00",
                        "fire_at_ts": 1_800_000_000.0,
                        "created_at": "2027-03-01T00:00:00+00:00",
                        "updated_at": "2027-03-01T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = load_calendar_events(config, "conv-legacy")
    assert len(loaded) == 1
    assert loaded[0].allowed_tools == []
    assert loaded[0].policy_granted is False
    assert loaded[0].model == ""


# -- required_skills body delivery (#2006) ----------------------------------
#
# These are the tests the field was missing. `required_skills` activates, but the
# *body* is delivered by context_composer.render_activated_skills, which skips
# anything in ctx.skills.body_in_prompt. activate_required_skills used to add to
# that set — on a path where nothing puts the body in the prompt — so the field
# suppressed exactly the delivery it named. Asserting "activation reported
# success" would not have caught it.


def _skill(name, body="INSTRUCTIONS", tier="admin", always_loaded=False):
    """A discovered-skill stand-in. Only the fields the paths read."""
    return SimpleNamespace(
        name=name,
        body=body,
        trust_tier=tier,
        always_loaded=always_loaded,
        has_native_tools=False,
        tools_location=None,
        tools_trust_tier=None,
        requires_env=[],
        location=SimpleNamespace(resolve=lambda: f"/skills/{name}"),
    )


def _activation_ctx(skills):
    """A ctx with the skill state activate_required_skills touches."""
    ctx = SimpleNamespace(
        config=SimpleNamespace(discovered_skills=skills),
        skills=SimpleNamespace(
            activated={},
            body_in_prompt=set(),
            activation_denials=[],
            data={},
        ),
        tools=SimpleNamespace(allowed=None, disallowed=None, preapproved=set(), preapproved_shell_patterns=[]),
        extra={},
    )
    return ctx


async def test_required_skills_do_not_claim_the_body_is_already_in_prompt():
    """The regression. body_in_prompt must stay unset so the composer renders it."""
    skills = [_skill("weeknotes-composer", body="Compose the weeknotes.")]
    ctx = _activation_ctx(skills)
    event = _event(required_skills=["weeknotes-composer"])

    await activate_required_skills(ctx, event)

    assert "weeknotes-composer" in ctx.skills.activated, "activation should still happen"
    assert ctx.skills.body_in_prompt == set(), (
        "marking body_in_prompt makes render_activated_skills skip the body, and a "
        "calendar wake prompt carries no <loaded_skills> block to replace it (#2006)"
    )


async def test_required_skill_body_reaches_the_composed_block():
    """End to end: activation alone must make the body renderable."""
    from decafclaw.context_composer import render_activated_skills

    marker = "WEEKNOTES-BODY-MARKER"
    skills = [_skill("weeknotes-composer", body=f"Compose: {marker}")]
    ctx = _activation_ctx(skills)
    event = _event(required_skills=["weeknotes-composer"])

    await activate_required_skills(ctx, event)
    rendered = render_activated_skills(ctx)

    assert rendered is not None, "no <activated_skills> block was produced"
    message, _tokens, count = rendered
    assert count == 1
    assert marker in message["content"]


async def test_ungrantable_skill_tier_is_denied_and_renders_nothing():
    """Workspace tier is agent-writable and must not be activated unattended."""
    from decafclaw.context_composer import render_activated_skills

    skills = [_skill("sneaky", body="do evil", tier="workspace")]
    ctx = _activation_ctx(skills)
    event = _event(required_skills=["sneaky"])

    await activate_required_skills(ctx, event)

    assert ctx.skills.activation_denials == ["sneaky"]
    assert "sneaky" not in ctx.skills.activated
    assert render_activated_skills(ctx) is None


async def test_an_always_loaded_skill_is_not_duplicated_in_the_block():
    """always_loaded bodies live in the system prompt; the block must skip them."""
    from decafclaw.context_composer import render_activated_skills

    skills = [
        _skill("vault", body="vault body", always_loaded=True),
        _skill("weeknotes-composer", body="compose it"),
    ]
    ctx = _activation_ctx(skills)
    event = _event(required_skills=["vault", "weeknotes-composer"])

    await activate_required_skills(ctx, event)
    rendered = render_activated_skills(ctx)

    assert rendered is not None
    message, _tokens, count = rendered
    assert count == 1, "only the non-always-loaded skill should render"
    assert "vault body" not in message["content"]
