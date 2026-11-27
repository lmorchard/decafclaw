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
