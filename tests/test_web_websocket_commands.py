"""WebSocket command-dispatch tests: verify cmd_ctx carries the manager,
and that each transport keeps its own command-prefix set.

Regression test for #361 — without the manager attached, bundled skills
with context: fork (dream, garden) fail their !command invocation with
'delegate_task requires a ConversationManager; no manager on parent ctx'.

The prefix tests were added for #139. The web autocomplete menu opens on
either `/` or `!` because the web path accepts both, while Mattermost is
`!`-only. That split lived in one default and one literal
(`commands.py` `prefixes = ["!", "/"]`; `mattermost.py` `prefixes=["!"]`)
with no test on it: narrowing the web call site to `prefixes=["!"]` would
have killed every `/command` in the browser with the suite still green.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from decafclaw.commands import CommandResult


@pytest.mark.asyncio
async def test_handle_send_attaches_manager_to_cmd_ctx(monkeypatch, config):
    """When a user sends a message that triggers command dispatch,
    the cmd_ctx passed to dispatch_command MUST have ctx.manager set
    to the conversation manager from state."""
    from decafclaw.web import websocket

    # Capture the ctx passed into dispatch_command so we can assert on it.
    captured = {}

    async def fake_dispatch(ctx, text, **kwargs):
        captured["ctx"] = ctx
        captured["kwargs"] = kwargs
        return CommandResult(
            mode="unknown", text="", display_text=text,
            skill=None,
        )

    monkeypatch.setattr(
        "decafclaw.commands.dispatch_command", fake_dispatch,
    )

    # Minimal state: real config + event_bus, sentinel manager.
    from decafclaw.events import EventBus
    bus = EventBus()
    sentinel_manager = MagicMock()
    state = {
        "config": config,
        "event_bus": bus,
        "manager": sentinel_manager,
    }

    # Minimal conversation index with a conv owned by "testuser".
    index = MagicMock()
    conv = MagicMock()
    conv.user_id = "testuser"
    index.get.return_value = conv

    # ws_send must be an awaitable; the test doesn't assert on outbound traffic.
    async def ws_send(_msg):
        pass

    msg = {"conv_id": "conv-1", "text": "!dream"}

    await websocket._handle_send(
        ws_send, index, "testuser", msg, state,
    )

    assert "ctx" in captured, "dispatch_command was not invoked"
    assert captured["ctx"].manager is sentinel_manager

    # The web path must not narrow the prefix set: either it passes nothing
    # (inheriting dispatch_command's ["!", "/"] default) or it passes both
    # explicitly. Anything else silently kills `/command` in the browser.
    prefixes = captured["kwargs"].get("prefixes")
    assert prefixes is None or sorted(prefixes) == ["!", "/"], (
        f"web dispatch_command narrowed the prefix set to {prefixes!r}; "
        "the web UI accepts both ! and /"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["!help", "/help"])
async def test_dispatch_command_accepts_both_prefixes_by_default(config, text):
    """The default the web path relies on recognises both `!` and `/`.

    Behavioural, not a signature read: the default is resolved inside the
    function body (the parameter default is None), so inspecting the
    signature would assert nothing.
    """
    from decafclaw.commands import dispatch_command
    from decafclaw.context import Context
    from decafclaw.events import EventBus

    ctx = Context(config=config, event_bus=EventBus())
    result = await dispatch_command(ctx, text)
    assert result.mode == "help", (
        f"{text!r} was not recognised as a command with the default prefix "
        f"set (got mode={result.mode!r}); the web UI accepts both ! and /"
    )


@pytest.mark.asyncio
async def test_dispatch_command_honours_a_narrowed_prefix_set(config):
    """`prefixes=["!"]` — what Mattermost passes — must reject `/help`."""
    from decafclaw.commands import dispatch_command
    from decafclaw.context import Context
    from decafclaw.events import EventBus

    ctx = Context(config=config, event_bus=EventBus())
    result = await dispatch_command(ctx, "/help", prefixes=["!"])
    assert result.mode == "not_command"


def test_mattermost_restricts_commands_to_the_bang_prefix():
    """Mattermost stays `!`-only — `/` there is Mattermost's own namespace.

    TEXT-BASED assertion, paired with the behavioural pair above: the call
    site sits deep inside the message handler and driving it would need the
    whole Mattermost client mocked. A rename or reflow can flip this
    independently of behaviour — read it as "the literal is still pinned",
    not as behavioural coverage.
    """
    from decafclaw import mattermost

    source = inspect.getsource(mattermost)
    assert 'prefixes=["!"]' in source, (
        "mattermost.py no longer pins dispatch_command to the ! prefix; a "
        "Mattermost `/command` would collide with Mattermost's own slash "
        "commands"
    )
