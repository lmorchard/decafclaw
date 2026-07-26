"""Display-layer tests for the interactive terminal transport.

`run_interactive`'s `on_event` closure is the terminal's whole rendering
surface, so these drive it directly: run the loop with `input` answering
"quit", capture the callback it registered with the manager, then feed it
events.
"""

import asyncio

import pytest

from decafclaw.conversation_manager import ConversationManager
from decafclaw.interactive_terminal import run_interactive


async def _capture_on_event(ctx, monkeypatch) -> object:
    """Run `run_interactive` to immediate exit and return its event callback."""
    captured = {}
    real_subscribe = ConversationManager.subscribe

    def capturing_subscribe(self, conv_id, callback):
        captured["cb"] = callback
        return real_subscribe(self, conv_id, callback)

    async def never_returns(*args, **kwargs):
        # A real signal to wait on, not a sleep — the caller cancels this task.
        await asyncio.Event().wait()

    monkeypatch.setattr(ConversationManager, "subscribe", capturing_subscribe)
    monkeypatch.setattr("decafclaw.mcp_client.init_mcp", _noop_async)
    monkeypatch.setattr("decafclaw.mcp_client.shutdown_mcp", _noop_async)
    monkeypatch.setattr("decafclaw.heartbeat.run_heartbeat_timer", never_returns)
    monkeypatch.setattr("builtins.input", lambda *a, **kw: "quit")

    await run_interactive(ctx)
    return captured["cb"]


async def _noop_async(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_text_before_tools_is_rendered_when_not_streaming(
        ctx, monkeypatch, capsys):
    """#707: the loop-breaker docs claimed every transport rendered each
    iteration's preamble live, and the terminal rendered it nowhere — so
    `_finalize_with_note` was dropping text no one had ever seen."""
    ctx.config.llm.streaming = False
    on_event = await _capture_on_event(ctx, monkeypatch)
    capsys.readouterr()  # discard the banner / prompt noise

    await on_event({"type": "text_before_tools", "text": "Trying the skill again."})

    assert "Trying the skill again." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_text_before_tools_is_suppressed_when_streaming(
        ctx, monkeypatch, capsys):
    """With streaming on, the same text already arrived as `chunk` events —
    printing it again would duplicate every preamble."""
    ctx.config.llm.streaming = True
    on_event = await _capture_on_event(ctx, monkeypatch)
    capsys.readouterr()

    await on_event({"type": "chunk", "text": "Trying the skill again."})
    await on_event({"type": "text_before_tools", "text": "Trying the skill again."})

    assert capsys.readouterr().out.count("Trying the skill again.") == 1
