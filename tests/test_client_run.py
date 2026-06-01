"""Orchestration tests for the decafclaw client using an in-memory fake transport."""

import pytest

from decafclaw.client.cli import SmokeArgs
from decafclaw.client.recorder import TurnRecorder
from decafclaw.client.run import (
    drive_turn,
    exit_code_for,
    run_respond,
    run_send,
)


class FakeTransport:
    """Scripts a sequence of event batches, one batch consumed per send()."""

    def __init__(self, batches, conv_id="web-fake"):
        import asyncio
        self._batches = list(batches)
        self._conv_id = conv_id
        self.sent: list[dict] = []
        self.created = False
        self.closed = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        return None

    async def create_conversation(self, title="decafclaw-client"):
        self.created = True
        return self._conv_id

    async def send(self, msg):
        self.sent.append(msg)
        # When a prompt or confirm is sent, enqueue the next scripted batch.
        if msg.get("type") in ("send", "confirm_response") and self._batches:
            for ev in self._batches.pop(0):
                await self._queue.put(ev)

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:        # sentinel: stream closed
                return
            yield ev

    async def disconnect(self):
        """Test helper: end the event stream as if the socket dropped."""
        await self._queue.put(None)

    async def close(self):
        self.closed = True


def _args(**kw):
    base = dict(action="send", token="dfc_x", host="http://h",
                timeout=5.0, fmt="summary", prompts=["hi"])
    base.update(kw)
    return SmokeArgs(**base)


@pytest.mark.asyncio
async def test_drive_turn_stops_on_turn_complete():
    t = FakeTransport([[
        {"type": "turn_start", "conv_id": "web-fake"},
        {"type": "message_complete", "conv_id": "web-fake",
         "role": "assistant", "text": "done"},
        {"type": "turn_complete", "conv_id": "web-fake"},
    ]])
    await t.send({"type": "send", "conv_id": "web-fake", "text": "hi"})
    rec = TurnRecorder("web-fake")
    reason = await drive_turn(t, rec, timeout=5.0, sink=None)
    assert reason == "turn_complete"
    assert rec.finalize(reason).assistant_text == "done"


@pytest.mark.asyncio
async def test_drive_turn_halts_on_confirmation():
    t = FakeTransport([[
        {"type": "turn_start", "conv_id": "web-fake"},
        {"type": "confirm_request", "conv_id": "web-fake",
         "confirmation_id": "c1", "action_type": "shell",
         "tool": "shell_exec", "command": "ls", "message": "ok?"},
        {"type": "turn_complete", "conv_id": "web-fake"},  # must NOT be reached
    ]])
    await t.send({"type": "send", "conv_id": "web-fake", "text": "hi"})
    rec = TurnRecorder("web-fake")
    reason = await drive_turn(t, rec, timeout=5.0, sink=None)
    assert reason == "confirmation"
    s = rec.finalize(reason)
    assert s.status == "halted_confirmation"
    assert s.confirmations[0].confirmation_id == "c1"


@pytest.mark.asyncio
async def test_drive_turn_times_out():
    t = FakeTransport([])  # no events ever enqueued
    rec = TurnRecorder("web-fake")
    reason = await drive_turn(t, rec, timeout=0.05, sink=None)
    assert reason == "timeout"


@pytest.mark.asyncio
async def test_run_send_creates_conv_when_absent():
    t = FakeTransport([[
        {"type": "turn_complete", "conv_id": "web-fake"},
    ]])
    summaries = await run_send(t, _args(conv=None))
    assert t.created is True
    assert summaries[0].conv_id == "web-fake"
    assert any(m["type"] == "select_conv" for m in t.sent)
    assert any(m["type"] == "send" for m in t.sent)


@pytest.mark.asyncio
async def test_run_send_sets_model_when_given():
    t = FakeTransport([[{"type": "turn_complete", "conv_id": "web-1"}]])
    await run_send(t, _args(conv="web-1", model="gemini-pro"))
    set_model = [m for m in t.sent if m["type"] == "set_model"]
    assert set_model and set_model[0]["model"] == "gemini-pro"


@pytest.mark.asyncio
async def test_run_send_multi_prompt_runs_sequentially():
    t = FakeTransport([
        [{"type": "message_complete", "conv_id": "web-1", "role": "assistant",
          "text": "a"}, {"type": "turn_complete", "conv_id": "web-1"}],
        [{"type": "message_complete", "conv_id": "web-1", "role": "assistant",
          "text": "b"}, {"type": "turn_complete", "conv_id": "web-1"}],
    ])
    summaries = await run_send(t, _args(conv="web-1", prompts=["one", "two"]))
    assert [s.assistant_text for s in summaries] == ["a", "b"]
    sends = [m for m in t.sent if m["type"] == "send"]
    assert [m["text"] for m in sends] == ["one", "two"]


@pytest.mark.asyncio
async def test_run_send_stops_after_disconnect_mid_sequence():
    # The first prompt's turn drops mid-run (the None sentinel ends its event
    # stream). The loop must stop and NOT send the second prompt — on a real
    # transport that send would hit a closed socket and raise ConnectionClosed
    # past _amain's TransportError handler.
    t = FakeTransport([
        [{"type": "turn_start", "conv_id": "web-1"}, None],
        [{"type": "message_complete", "conv_id": "web-1", "role": "assistant",
          "text": "b"}, {"type": "turn_complete", "conv_id": "web-1"}],
    ])
    summaries = await run_send(t, _args(conv="web-1", prompts=["one", "two"]))
    assert len(summaries) == 1
    assert summaries[0].status == "error"
    sends = [m for m in t.sent if m["type"] == "send"]
    assert [m["text"] for m in sends] == ["one"]  # "two" never sent


@pytest.mark.asyncio
async def test_run_respond_sends_confirm_response():
    t = FakeTransport([[{"type": "turn_complete", "conv_id": "web-1"}]])
    args = SmokeArgs(action="respond", token="dfc_x", host="http://h",
                     timeout=5.0, fmt="summary", conv="web-1",
                     confirmation_id="c1", approved=True)
    summaries = await run_respond(t, args)
    cr = [m for m in t.sent if m["type"] == "confirm_response"]
    assert cr and cr[0]["confirmation_id"] == "c1" and cr[0]["approved"] is True
    assert summaries[0].status == "complete"


def test_exit_code_mapping():
    assert exit_code_for(["complete"]) == 0
    assert exit_code_for(["complete", "halted_confirmation"]) == 2
    assert exit_code_for(["timeout"]) == 3
    assert exit_code_for(["error"]) == 1
    # first non-complete wins
    assert exit_code_for(["complete", "timeout", "error"]) == 3


@pytest.mark.asyncio
async def test_drive_turn_disconnects_when_stream_ends():
    t = FakeTransport([])
    await t.disconnect()  # stream ends immediately, no turn_complete
    rec = TurnRecorder("web-fake")
    reason = await drive_turn(t, rec, timeout=5.0, sink=None)
    assert reason == "disconnect"
    assert rec.finalize(reason).status == "error"


@pytest.mark.asyncio
async def test_run_send_jsonl_streams_raw_events(capsys):
    import json
    t = FakeTransport([[
        {"type": "turn_start", "conv_id": "web-1"},
        {"type": "message_complete", "conv_id": "web-1", "role": "assistant",
         "text": "hi"},
        {"type": "turn_complete", "conv_id": "web-1"},
    ]])
    await run_send(t, _args(conv="web-1", fmt="jsonl"))
    out_lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    types = [json.loads(line)["type"] for line in out_lines]
    assert types == ["turn_start", "message_complete", "turn_complete"]
