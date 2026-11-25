"""Shadow mode for direction inference (#1661).

Shadow mode is observational: it must log what the direction tier would have
decided and leave the real decision byte-for-byte unchanged.
"""

import json

import pytest

from decafclaw.context import TaskMode
from decafclaw.tools.shell_tools import check_shell_approval


def _seed_activity(ctx, command: str = "touch prior-marker", cwd: str | None = None) -> None:
    ctx.history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "prior-call",
                    "type": "function",
                    "function": {
                        "name": "shell",
                        "arguments": json.dumps({"command": command, "cwd": cwd}),
                    },
                }
            ],
        }
    ]


def _collect(ctx) -> list[dict]:
    events: list[dict] = []

    def collect(event: dict) -> None:
        events.append(event)

    ctx.event_bus.subscribe(collect)
    return events


@pytest.mark.asyncio
async def test_shadow_mode_does_not_change_the_decision(ctx):
    ctx.config.shell.direction_shadow_mode = False
    baseline = await check_shell_approval(ctx, "ls -la")

    ctx.config.shell.direction_shadow_mode = True
    events = _collect(ctx)
    shadowed = await check_shell_approval(ctx, "ls -la")

    assert shadowed["approved"] is True
    assert baseline["approved"] is True
    shadows = [event for event in events if event.get("type") == "direction_shadow"]
    assert len(shadows) == 1
    assert shadows[0]["would_decide"] == "prompt"
    assert shadows[0]["activity_established"] is False
    assert shadows[0]["tier"] == "activity_new"


@pytest.mark.asyncio
async def test_shadow_mode_reports_pass_in_an_established_activity(ctx):
    ctx.config.shell.direction_shadow_mode = True
    _seed_activity(ctx)
    events = _collect(ctx)

    await check_shell_approval(ctx, "ls -la")

    shadows = [event for event in events if event.get("type") == "direction_shadow"]
    assert len(shadows) == 1
    assert shadows[0]["would_decide"] == "pass"
    assert shadows[0]["tier"] == "aux_judge"


@pytest.mark.asyncio
async def test_shadow_mode_reports_defer_on_an_unattended_turn(ctx):
    ctx.config.shell.direction_shadow_mode = True
    ctx.task_mode = TaskMode.SCHEDULED
    events = _collect(ctx)

    await check_shell_approval(ctx, "ls -la")

    shadows = [event for event in events if event.get("type") == "direction_shadow"]
    assert len(shadows) == 1
    assert shadows[0]["would_decide"] == "defer"
