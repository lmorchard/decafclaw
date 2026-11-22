"""Does a cheap silent read-only command establish an activity? (#1641 review)

The PR's own "Residual risk" note says a silent read-only command still counts
toward establishing activity. This test pins that behavior so it is a
documented property rather than a surprise: the drift prompt can be pre-paid
with one harmless command.
"""

import json

from decafclaw.activity import derive_activity


def _ctx(config):
    from decafclaw.context import Context
    from decafclaw.events import EventBus

    ctx = Context(config=config, event_bus=EventBus())
    ctx.conv_id = "forgery-test"
    return ctx


def _shell(call_id, command, cwd=None):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "shell", "arguments": json.dumps({"command": command, "cwd": cwd})},
    }


def _history(*calls):
    return [{"role": "assistant", "content": None, "tool_calls": [c]} for c in calls]


def test_one_read_only_command_establishes_the_domain(config):
    """A single silent `ls decafclaw` makes decafclaw 'established'.

    This is the disclosed residual risk: the drift prompt can be pre-paid with
    one harmless command, after which a genuinely novel mutating command in
    that domain no longer prompts for direction. It still reaches the aux
    judge, so risk review is not skipped — only the direction prompt is.
    """
    ctx = _ctx(config)
    repo = config.workspace_path / "decafclaw"
    repo.mkdir(parents=True, exist_ok=True)
    ctx.history = _history(_shell("c0", "ls decafclaw"))
    activity = derive_activity(ctx, "make deploy", str(repo))
    assert activity.domain == "decafclaw"
    assert activity.established is True


def test_unknown_establishes_unknown(config):
    """Unattributed commands establish the `unknown` activity similarly."""
    ctx = _ctx(config)
    ctx.history = _history(_shell("c0", "ls -la"))
    activity = derive_activity(ctx, "python3 -c 'print(1)'", str(config.workspace_path))
    assert activity.domain == "unknown"
    assert activity.established is True
