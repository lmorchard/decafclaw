# Headless WebSocket Smoke-Test Client — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A headless Python CLI (`decafclaw-ws-smoke`) that drives a conversation in a running decafclaw instance over the existing `/ws/chat` WebSocket gateway and emits machine-readable results, so coding agents can smoke-test agent features without Playwright.

**Architecture:** A new `decafclaw.ws_smoke` subpackage with four focused modules — a pure event **recorder** (reducer over WS event dicts → `TurnSummary`), a pure **cli** arg parser, a thin **transport** (websockets + httpx, the only part touching the network), and a **run** orchestrator that wires transport→recorder, drives the turn loop with a timeout, and maps results to output + exit codes. The recorder and orchestrator are fully unit-tested (the latter via an injected fake transport); the real transport is validated by a documented manual smoke against a running server.

**Tech Stack:** Python 3.13, `asyncio` (`asyncio.timeout`), `websockets` (already a dependency), `httpx` (already a dependency), `argparse`, `dataclasses`. Tests: `pytest` + `pytest-asyncio`.

---

## File Structure

- Create `src/decafclaw/ws_smoke/__init__.py` — exports `main`.
- Create `src/decafclaw/ws_smoke/__main__.py` — `python -m decafclaw.ws_smoke` entry.
- Create `src/decafclaw/ws_smoke/recorder.py` — `ToolCallRecord`, `ConfirmationRecord`, `TurnSummary`, `TurnRecorder`. Pure; no I/O.
- Create `src/decafclaw/ws_smoke/cli.py` — `SmokeArgs` dataclass, `build_parser()`, `parse_args()`. Pure.
- Create `src/decafclaw/ws_smoke/transport.py` — `WSTransport` (real websockets + httpx). Network only.
- Create `src/decafclaw/ws_smoke/run.py` — `drive_turn`, `run_send`, `run_respond`, `emit`, `exit_code_for`, `main`. Orchestration.
- Create `tests/test_ws_smoke_recorder.py` — recorder unit tests.
- Create `tests/test_ws_smoke_cli.py` — arg-parsing unit tests.
- Create `tests/test_ws_smoke_run.py` — orchestration tests via a fake transport.
- Modify `pyproject.toml:34-39` — add console script `decafclaw-ws-smoke`.
- Create `docs/ws-smoke.md` — usage doc.
- Modify `docs/index.md` — link the new doc.

---

## Task 1: Recorder data types + accumulation

**Files:**
- Create: `src/decafclaw/ws_smoke/__init__.py`
- Create: `src/decafclaw/ws_smoke/recorder.py`
- Test: `tests/test_ws_smoke_recorder.py`

- [ ] **Step 1: Create the empty package marker**

`src/decafclaw/ws_smoke/__init__.py`:
```python
"""Headless WebSocket smoke-test client for a running decafclaw instance."""
```

- [ ] **Step 2: Write failing tests for accumulation**

`tests/test_ws_smoke_recorder.py`:
```python
"""Unit tests for the ws_smoke TurnRecorder (pure event reducer)."""

from decafclaw.ws_smoke.recorder import TurnRecorder


def _drive(events):
    rec = TurnRecorder(conv_id="web-test")
    for e in events:
        rec.record(e)
    return rec


def test_assistant_text_joined_from_message_complete():
    rec = _drive([
        {"type": "turn_start", "conv_id": "web-test"},
        {"type": "chunk", "conv_id": "web-test", "text": "ignored "},
        {"type": "message_complete", "conv_id": "web-test", "role": "assistant",
         "text": "Hello there.", "usage": {"input_tokens": 5}},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.conv_id == "web-test"
    assert s.assistant_text == "Hello there."
    assert s.usage == {"input_tokens": 5}
    assert s.status == "complete"
    assert s.raw_event_count == 4


def test_tool_calls_ordered_and_completed():
    rec = _drive([
        {"type": "tool_start", "conv_id": "web-test", "tool": "vault_read",
         "tool_call_id": "a"},
        {"type": "tool_start", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "b"},
        {"type": "tool_status", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "b", "message": "fetching"},
        {"type": "tool_end", "conv_id": "web-test", "tool": "vault_read",
         "tool_call_id": "a", "result_text": "page body"},
        {"type": "tool_end", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "b", "result_text": "<html>"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert [t.name for t in s.tool_calls] == ["vault_read", "http_get"]
    assert [t.tool_call_id for t in s.tool_calls] == ["a", "b"]
    assert all(t.status == "done" for t in s.tool_calls)
    assert s.tool_calls[0].result_text == "page body"
    assert s.tool_calls[1].result_text == "<html>"


def test_tool_end_without_prior_start_is_recorded():
    rec = _drive([
        {"type": "tool_end", "conv_id": "web-test", "tool": "orphan",
         "tool_call_id": "z", "result_text": "late"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert len(s.tool_calls) == 1
    assert s.tool_calls[0].name == "orphan"
    assert s.tool_calls[0].status == "done"


def test_reflection_recorded():
    rec = _drive([
        {"type": "reflection_result", "conv_id": "web-test", "passed": False,
         "critique": "missed a step", "retry_number": 1},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.reflection == {"passed": False, "critique": "missed a step",
                            "retry_number": 1}


def test_model_changed_recorded():
    rec = _drive([
        {"type": "model_changed", "conv_id": "web-test", "model": "gemini-pro"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.model == "gemini-pro"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ws_smoke_recorder.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'decafclaw.ws_smoke.recorder'`.

- [ ] **Step 4: Implement `recorder.py` (accumulation only)**

`src/decafclaw/ws_smoke/recorder.py`:
```python
"""Pure reducer that turns a stream of WebSocket event dicts into a TurnSummary.

No I/O. Feed each event dict to `record()`, then call `finalize(stop_reason)`
to compute the terminal status and assistant text. Kept lossless on purpose:
unlike the interactive TUI dispatcher, this captures every tool call, every
confirmation, and every error so an agent can assert on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    tool_call_id: str
    name: str
    status: str = "started"  # "started" | "done"
    result_text: str = ""


@dataclass
class ConfirmationRecord:
    confirmation_id: str
    action_type: str = ""
    tool: str = ""
    command: str = ""
    message: str = ""


@dataclass
class TurnSummary:
    conv_id: str = ""
    status: str = "incomplete"  # complete|halted_confirmation|error|timeout
    assistant_text: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    confirmations: list[ConfirmationRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reflection: dict | None = None
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw_event_count: int = 0


class TurnRecorder:
    def __init__(self, conv_id: str = "") -> None:
        self.summary = TurnSummary(conv_id=conv_id)
        self._assistant_parts: list[str] = []
        self._tool_index: dict[str, ToolCallRecord] = {}

    def record(self, event: dict) -> None:
        self.summary.raw_event_count += 1
        etype = event.get("type", "")

        if etype == "message_complete":
            role = event.get("role") or "assistant"
            text = event.get("text") or ""
            if role == "assistant" and text:
                self._assistant_parts.append(text)
            usage = event.get("usage")
            if isinstance(usage, dict) and usage:
                self.summary.usage = usage

        elif etype == "tool_start":
            tcid = event.get("tool_call_id", "")
            rec = ToolCallRecord(tool_call_id=tcid, name=event.get("tool", ""))
            self._tool_index[tcid] = rec
            self.summary.tool_calls.append(rec)

        elif etype == "tool_status":
            rec = self._tool_index.get(event.get("tool_call_id", ""))
            if rec is not None:
                rec.status = event.get("message", "") or rec.status

        elif etype == "tool_end":
            tcid = event.get("tool_call_id", "")
            rec = self._tool_index.get(tcid)
            if rec is None:
                rec = ToolCallRecord(tool_call_id=tcid, name=event.get("tool", ""))
                self._tool_index[tcid] = rec
                self.summary.tool_calls.append(rec)
            rec.status = "done"
            rec.result_text = event.get("result_text", "") or rec.result_text

        elif etype == "confirm_request":
            self.summary.confirmations.append(ConfirmationRecord(
                confirmation_id=event.get("confirmation_id", ""),
                action_type=event.get("action_type", ""),
                tool=event.get("tool", ""),
                command=event.get("command", ""),
                message=event.get("message", ""),
            ))

        elif etype == "error":
            msg = event.get("message", "")
            if msg:
                self.summary.errors.append(msg)

        elif etype == "reflection_result":
            self.summary.reflection = {
                "passed": event.get("passed"),
                "critique": event.get("critique", ""),
                "retry_number": event.get("retry_number"),
            }

        elif etype == "model_changed":
            self.summary.model = event.get("model", "") or self.summary.model

    def finalize(self, stop_reason: str) -> TurnSummary:
        self.summary.assistant_text = "\n\n".join(
            p for p in self._assistant_parts if p)
        self.summary.status = _status_for(stop_reason, self.summary.errors)
        return self.summary


def _status_for(stop_reason: str, errors: list[str]) -> str:
    if stop_reason == "confirmation":
        return "halted_confirmation"
    if stop_reason == "timeout":
        return "timeout"
    if stop_reason in ("turn_complete", "disconnect"):
        return "error" if errors else "complete"
    return "error"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ws_smoke_recorder.py -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/ws_smoke/__init__.py src/decafclaw/ws_smoke/recorder.py tests/test_ws_smoke_recorder.py
git commit -m "feat(ws-smoke): pure TurnRecorder reducer for WS event streams"
```

---

## Task 2: Status mapping edge cases

**Files:**
- Modify: `tests/test_ws_smoke_recorder.py`
- (No source changes expected — this hardens `_status_for` / `finalize`.)

- [ ] **Step 1: Add failing tests for status edge cases**

Append to `tests/test_ws_smoke_recorder.py`:
```python
def test_status_confirmation_overrides_complete():
    rec = _drive([
        {"type": "confirm_request", "conv_id": "web-test",
         "confirmation_id": "c1", "action_type": "shell",
         "tool": "shell_exec", "command": "rm -rf /tmp/x", "message": "OK?"},
    ])
    s = rec.finalize("confirmation")
    assert s.status == "halted_confirmation"
    assert s.confirmations[0].confirmation_id == "c1"
    assert s.confirmations[0].command == "rm -rf /tmp/x"


def test_status_error_when_error_event_seen():
    rec = _drive([
        {"type": "error", "conv_id": "web-test", "message": "boom"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.status == "error"
    assert s.errors == ["boom"]


def test_status_timeout():
    rec = _drive([{"type": "turn_start", "conv_id": "web-test"}])
    s = rec.finalize("timeout")
    assert s.status == "timeout"


def test_status_disconnect_without_error_is_complete():
    rec = _drive([{"type": "turn_start", "conv_id": "web-test"}])
    s = rec.finalize("disconnect")
    assert s.status == "complete"
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_ws_smoke_recorder.py -q`
Expected: PASS (9 tests total). If `test_status_confirmation_overrides_complete` fails, confirm `record` appends the `ConfirmationRecord` before `finalize` is called — no source change should be needed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ws_smoke_recorder.py
git commit -m "test(ws-smoke): status mapping edge cases for finalize"
```

---

## Task 3: CLI argument parsing

**Files:**
- Create: `src/decafclaw/ws_smoke/cli.py`
- Test: `tests/test_ws_smoke_cli.py`

- [ ] **Step 1: Write failing tests**

`tests/test_ws_smoke_cli.py`:
```python
"""Unit tests for ws_smoke CLI argument parsing."""

import pytest

from decafclaw.ws_smoke.cli import parse_args


def test_send_minimal(monkeypatch):
    monkeypatch.delenv("DECAFCLAW_TOKEN", raising=False)
    monkeypatch.delenv("DECAFCLAW_HOST", raising=False)
    args = parse_args(["send", "--token", "dfc_x", "--prompt", "hello"])
    assert args.action == "send"
    assert args.token == "dfc_x"
    assert args.host == "http://localhost:8088"
    assert args.prompts == ["hello"]
    assert args.conv is None
    assert args.timeout == 180.0
    assert args.fmt == "summary"


def test_token_and_host_from_env(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_env")
    monkeypatch.setenv("DECAFCLAW_HOST", "https://example.com")
    args = parse_args(["send", "--prompt", "hi"])
    assert args.token == "dfc_env"
    assert args.host == "https://example.com"


def test_explicit_token_overrides_env(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_env")
    args = parse_args(["send", "--token", "dfc_flag", "--prompt", "hi"])
    assert args.token == "dfc_flag"


def test_multiple_prompts_preserve_order(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["send", "--prompt", "one", "--prompt", "two"])
    assert args.prompts == ["one", "two"]


def test_script_file_lines_become_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    script = tmp_path / "s.txt"
    script.write_text("first line\n\nsecond line\n")  # blank lines skipped
    args = parse_args(["send", "--script", str(script)])
    assert args.prompts == ["first line", "second line"]


def test_missing_token_errors(monkeypatch):
    monkeypatch.delenv("DECAFCLAW_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        parse_args(["send", "--prompt", "hi"])


def test_send_requires_a_prompt(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    with pytest.raises(SystemExit):
        parse_args(["send"])


def test_respond_defaults_to_approve(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["respond", "--conv", "web-1", "--confirmation-id", "c1"])
    assert args.action == "respond"
    assert args.conv == "web-1"
    assert args.confirmation_id == "c1"
    assert args.approved is True


def test_respond_deny(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["respond", "--conv", "web-1", "--deny"])
    assert args.approved is False


def test_respond_requires_conv(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    with pytest.raises(SystemExit):
        parse_args(["respond", "--confirmation-id", "c1"])


def test_format_jsonl(monkeypatch):
    monkeypatch.setenv("DECAFCLAW_TOKEN", "dfc_x")
    args = parse_args(["send", "--prompt", "hi", "--format", "jsonl"])
    assert args.fmt == "jsonl"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ws_smoke_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'decafclaw.ws_smoke.cli'`.

- [ ] **Step 3: Implement `cli.py`**

`src/decafclaw/ws_smoke/cli.py`:
```python
"""Argument parsing for the ws_smoke CLI. Pure: no network, no event loop."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass


@dataclass
class SmokeArgs:
    action: str  # "send" | "respond"
    token: str
    host: str
    timeout: float
    fmt: str  # "summary" | "jsonl"
    conv: str | None = None
    model: str | None = None
    prompts: list[str] | None = None
    confirmation_id: str | None = None
    approved: bool = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decafclaw-ws-smoke",
        description="Drive a conversation in a running decafclaw instance over "
                    "the /ws/chat WebSocket gateway and emit machine-readable "
                    "results for smoke testing.",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--token", default=None,
                       help="Web token (or env DECAFCLAW_TOKEN).")
        p.add_argument("--host", default=None,
                       help="Base URL (or env DECAFCLAW_HOST; "
                            "default http://localhost:8088).")
        p.add_argument("--timeout", type=float, default=180.0,
                       help="Per-turn timeout in seconds (default 180).")
        p.add_argument("--format", dest="fmt", choices=("summary", "jsonl"),
                       default="summary", help="Output format (default summary).")

    p_send = sub.add_parser("send", help="Send prompt(s) and record the turn(s).")
    add_common(p_send)
    p_send.add_argument("--conv", default=None,
                        help="Existing conversation id; omit to create a new one.")
    p_send.add_argument("--model", default=None,
                        help="Set the conversation model before sending.")
    p_send.add_argument("--prompt", action="append", default=[],
                        help="Prompt text. Repeatable; runs sequentially.")
    p_send.add_argument("--script", default=None,
                        help="File of prompts, one per line (blank lines skipped).")

    p_resp = sub.add_parser("respond", help="Respond to a pending confirmation.")
    add_common(p_resp)
    p_resp.add_argument("--conv", required=True, help="Conversation id.")
    p_resp.add_argument("--confirmation-id", dest="confirmation_id", default=None,
                        help="Confirmation id; defaults to the pending one.")
    decision = p_resp.add_mutually_exclusive_group()
    decision.add_argument("--approve", dest="approved", action="store_true",
                          default=True, help="Approve (default).")
    decision.add_argument("--deny", dest="approved", action="store_false",
                          help="Deny.")

    return parser


def parse_args(argv: list[str] | None = None) -> SmokeArgs:
    parser = build_parser()
    ns = parser.parse_args(argv)

    token = ns.token or os.environ.get("DECAFCLAW_TOKEN", "")
    if not token:
        parser.error("a web token is required (--token or DECAFCLAW_TOKEN)")
    host = ns.host or os.environ.get("DECAFCLAW_HOST", "http://localhost:8088")

    if ns.action == "send":
        prompts = list(ns.prompt)
        if ns.script:
            with open(ns.script, encoding="utf-8") as fh:
                prompts.extend(line.strip() for line in fh if line.strip())
        if not prompts:
            parser.error("send requires at least one --prompt or --script")
        return SmokeArgs(
            action="send", token=token, host=host, timeout=ns.timeout,
            fmt=ns.fmt, conv=ns.conv, model=ns.model, prompts=prompts,
        )

    return SmokeArgs(
        action="respond", token=token, host=host, timeout=ns.timeout,
        fmt=ns.fmt, conv=ns.conv, confirmation_id=ns.confirmation_id,
        approved=ns.approved,
    )
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/test_ws_smoke_cli.py -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/ws_smoke/cli.py tests/test_ws_smoke_cli.py
git commit -m "feat(ws-smoke): CLI argument parsing for send/respond"
```

---

## Task 4: WebSocket + REST transport

**Files:**
- Create: `src/decafclaw/ws_smoke/transport.py`

This module is the only network-touching code. It is validated by the manual
smoke in Task 7 (not unit-tested — a unit test would just re-mock the libraries).
Keep it thin.

- [ ] **Step 1: Implement `transport.py`**

`src/decafclaw/ws_smoke/transport.py`:
```python
"""Thin network transport for the ws_smoke client.

Wraps the websockets client and an httpx call to POST /api/conversations,
using the same cookie auth the browser and TUI use
(`Cookie: decafclaw_session=<token>`). Exposes a minimal interface that the
orchestrator in run.py drives: `connect()`, `create_conversation()`, `send()`,
`events()` (async iterator of parsed dicts), and `close()`.

Network failures are surfaced as TransportError so the orchestrator can map
them to exit code 4.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import websockets


class TransportError(Exception):
    """Connection / auth / protocol failure talking to the server."""


class WSTransport:
    def __init__(self, host: str, token: str) -> None:
        self._host = host.rstrip("/")
        self._token = token
        self._ws_url = self._host.replace("http", "ws", 1) + "/ws/chat"
        self._ws: websockets.ClientConnection | None = None

    async def connect(self) -> None:
        try:
            self._ws = await websockets.connect(
                self._ws_url,
                additional_headers={
                    "Cookie": f"decafclaw_session={self._token}"},
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as TransportError
            raise TransportError(f"WebSocket connect failed: {exc}") from exc

    async def create_conversation(self, title: str = "ws-smoke") -> str:
        url = f"{self._host}/api/conversations"
        cookies = {"decafclaw_session": self._token}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={"title": title},
                                         cookies=cookies, timeout=30.0)
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"create conversation failed: {exc}") from exc
        if resp.status_code != 201:
            raise TransportError(
                f"create conversation: HTTP {resp.status_code} {resp.text}")
        conv_id = resp.json().get("conv_id", "")
        if not conv_id:
            raise TransportError("create conversation: no conv_id in response")
        return conv_id

    async def send(self, msg: dict) -> None:
        if self._ws is None:
            raise TransportError("send before connect")
        await self._ws.send(json.dumps(msg))

    async def events(self) -> AsyncIterator[dict]:
        if self._ws is None:
            raise TransportError("events before connect")
        async for raw in self._ws:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("type"), str):
                yield parsed

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
```

- [ ] **Step 2: Sanity-check imports compile**

Run: `uv run python -c "import decafclaw.ws_smoke.transport as t; print(t.WSTransport)"`
Expected: prints `<class 'decafclaw.ws_smoke.transport.WSTransport'>` with no import error.

- [ ] **Step 3: Verify the `websockets` connect API**

Confirm the installed `websockets` major version supports
`websockets.connect(..., additional_headers=...)` and async iteration yielding
`str`/`bytes`. Run: `uv run python -c "import websockets; print(websockets.__version__)"`.
If the version is `< 14`, replace `additional_headers=` with `extra_headers=`
and `websockets.ClientConnection` with `websockets.WebSocketClientProtocol` in
the type hint. (As of this plan the lockfile pins `websockets==16.0`, which uses
`additional_headers`.)

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/ws_smoke/transport.py
git commit -m "feat(ws-smoke): thin websockets + httpx transport"
```

---

## Task 5: Orchestration (drive_turn, run_send, run_respond, output, exit codes)

**Files:**
- Create: `src/decafclaw/ws_smoke/run.py`
- Test: `tests/test_ws_smoke_run.py`

The orchestrator is tested with a `FakeTransport` that yields scripted events,
so the turn loop / timeout / halt / exit-code logic is covered without a network.

- [ ] **Step 1: Write failing tests**

`tests/test_ws_smoke_run.py`:
```python
"""Orchestration tests for ws_smoke using an in-memory fake transport."""

import asyncio

import pytest

from decafclaw.ws_smoke.cli import SmokeArgs
from decafclaw.ws_smoke.recorder import TurnRecorder
from decafclaw.ws_smoke.run import (
    drive_turn,
    exit_code_for,
    run_respond,
    run_send,
)


class FakeTransport:
    """Scripts a sequence of event batches, one batch consumed per send()."""

    def __init__(self, batches, conv_id="web-fake"):
        self._batches = list(batches)
        self._conv_id = conv_id
        self.sent: list[dict] = []
        self.created = False
        self.closed = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def connect(self):
        return None

    async def create_conversation(self, title="ws-smoke"):
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
            yield ev

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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ws_smoke_run.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'decafclaw.ws_smoke.run'`.

- [ ] **Step 3: Implement `run.py`**

`src/decafclaw/ws_smoke/run.py`:
```python
"""Orchestration for the ws_smoke client: drive turns, emit output, exit codes.

`drive_turn` is transport-agnostic (any object with `events()`), which is what
makes the loop unit-testable with a fake transport.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from collections.abc import Callable

from .cli import SmokeArgs, parse_args
from .recorder import TurnRecorder, TurnSummary
from .transport import TransportError, WSTransport

_EXIT_BY_STATUS = {
    "complete": 0,
    "error": 1,
    "halted_confirmation": 2,
    "timeout": 3,
}


async def drive_turn(transport, recorder: TurnRecorder, *, timeout: float,
                     sink: Callable[[dict], None] | None) -> str:
    """Consume events until a stop condition. Returns the stop reason:
    "turn_complete" | "confirmation" | "timeout" | "disconnect".
    """
    try:
        async with asyncio.timeout(timeout):
            async for event in transport.events():
                if sink is not None:
                    sink(event)
                recorder.record(event)
                etype = event.get("type")
                if etype == "confirm_request":
                    return "confirmation"
                if etype == "turn_complete":
                    return "turn_complete"
    except TimeoutError:
        return "timeout"
    return "disconnect"


def _sink_for(fmt: str) -> Callable[[dict], None] | None:
    if fmt == "jsonl":
        return lambda ev: print(json.dumps(ev), flush=True)
    return None


async def _select(transport, conv_id: str) -> None:
    await transport.send({"type": "select_conv", "conv_id": conv_id})


async def run_send(transport, args: SmokeArgs) -> list[TurnSummary]:
    conv_id = args.conv or await transport.create_conversation()
    await _select(transport, conv_id)
    if args.model:
        await transport.send({"type": "set_model", "conv_id": conv_id,
                              "model": args.model})
    sink = _sink_for(args.fmt)
    summaries: list[TurnSummary] = []
    for prompt in args.prompts or []:
        recorder = TurnRecorder(conv_id)
        await transport.send({"type": "send", "conv_id": conv_id, "text": prompt})
        reason = await drive_turn(transport, recorder, timeout=args.timeout,
                                  sink=sink)
        summaries.append(recorder.finalize(reason))
        if reason in ("confirmation", "timeout"):
            break  # don't fire later prompts past a halt/timeout
    return summaries


async def run_respond(transport, args: SmokeArgs) -> list[TurnSummary]:
    conv_id = args.conv or ""
    await _select(transport, conv_id)
    recorder = TurnRecorder(conv_id)
    await transport.send({
        "type": "confirm_response", "conv_id": conv_id,
        "confirmation_id": args.confirmation_id or "",
        "approved": args.approved, "always": False, "add_pattern": False,
    })
    reason = await drive_turn(transport, recorder, timeout=args.timeout,
                              sink=_sink_for(args.fmt))
    return [recorder.finalize(reason)]


def exit_code_for(statuses: list[str]) -> int:
    for status in statuses:
        if status != "complete":
            return _EXIT_BY_STATUS.get(status, 1)
    return 0


def emit(summaries: list[TurnSummary], fmt: str) -> None:
    if fmt == "jsonl":
        return  # events already streamed by the sink
    payload = [dataclasses.asdict(s) for s in summaries]
    print(json.dumps(payload[0] if len(payload) == 1 else payload, indent=2))


async def _amain(args: SmokeArgs) -> int:
    transport = WSTransport(args.host, args.token)
    try:
        await transport.connect()
        if args.action == "send":
            summaries = await run_send(transport, args)
        else:
            summaries = await run_respond(transport, args)
    except TransportError as exc:
        print(json.dumps({"status": "error", "errors": [str(exc)]}),
              file=sys.stderr)
        return 4
    finally:
        await transport.close()
    emit(summaries, args.fmt)
    return exit_code_for([s.status for s in summaries])


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(_amain(args))
```

- [ ] **Step 4: Run to verify passing**

Run: `uv run pytest tests/test_ws_smoke_run.py -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/ws_smoke/run.py tests/test_ws_smoke_run.py
git commit -m "feat(ws-smoke): turn-loop orchestration, output, and exit codes"
```

---

## Task 6: Entry points (`__main__`, `__init__`, console script)

**Files:**
- Create: `src/decafclaw/ws_smoke/__main__.py`
- Modify: `src/decafclaw/ws_smoke/__init__.py`
- Modify: `pyproject.toml:34-39`

- [ ] **Step 1: Export `main` from the package**

Replace `src/decafclaw/ws_smoke/__init__.py` with:
```python
"""Headless WebSocket smoke-test client for a running decafclaw instance."""

from .run import main

__all__ = ["main"]
```

- [ ] **Step 2: Add the module entry point**

`src/decafclaw/ws_smoke/__main__.py`:
```python
"""`python -m decafclaw.ws_smoke` entry point."""

import sys

from .run import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Register the console script**

In `pyproject.toml`, under `[project.scripts]` (currently lines 34-39), add:
```toml
decafclaw-ws-smoke = "decafclaw.ws_smoke:main"
```

- [ ] **Step 4: Re-sync so the console script installs**

Run: `uv sync`
Then verify help works end-to-end:
Run: `uv run decafclaw-ws-smoke --help`
Expected: argparse usage listing `send` and `respond` subcommands, exit 0.
Run: `uv run python -m decafclaw.ws_smoke send --help`
Expected: `send` usage with `--prompt`, `--conv`, `--model`, etc.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/ws_smoke/__init__.py src/decafclaw/ws_smoke/__main__.py pyproject.toml uv.lock
git commit -m "feat(ws-smoke): console-script and module entry points"
```

---

## Task 7: Manual smoke against a running server + docs

**Files:**
- Create: `docs/ws-smoke.md`
- Modify: `docs/index.md`

> NOTE: Les usually has `make dev` running and only one bot may hold the
> Mattermost token — but this client only needs the **web gateway**, which runs
> alongside it. Do not start a second `make run`/`make dev`. Use the already-
> running instance. If none is running, ask Les to start one rather than starting
> it yourself.

- [ ] **Step 1: Obtain a token and confirm the host/port**

Find a `dfc_` token: it is a key in `data/<agent_id>/web_tokens.json` in the main
clone. Confirm the web port (`make config` or the `make dev` logs; default 8088,
but Les's local instance may differ — the TUI README notes 18880 in one example).

- [ ] **Step 2: Smoke a simple turn (happy path)**

Run (substitute token/host):
```bash
uv run decafclaw-ws-smoke send \
  --token "$DECAFCLAW_TOKEN" --host http://localhost:8088 \
  --prompt "Reply with exactly the word PONG and nothing else."
```
Expected: a JSON summary with `"status": "complete"`, `assistant_text`
containing `PONG`, exit code 0 (`echo $?`).

- [ ] **Step 3: Smoke a tool-using turn**

Run:
```bash
uv run decafclaw-ws-smoke send --token "$DECAFCLAW_TOKEN" \
  --prompt "Use your vault tools to list what you know, then summarize in one line."
```
Expected: `tool_calls` non-empty with `status: "done"` entries; exit 0.

- [ ] **Step 4: Smoke the confirmation halt + respond flow**

Trigger a confirmation gate (e.g. a shell command that requires approval):
```bash
uv run decafclaw-ws-smoke send --token "$DECAFCLAW_TOKEN" \
  --prompt "Run the shell command: echo hello-from-smoke"
```
Expected: `"status": "halted_confirmation"`, a `confirmations[0].confirmation_id`,
exit code 2. Then resume it:
```bash
uv run decafclaw-ws-smoke respond --token "$DECAFCLAW_TOKEN" \
  --conv <conv_id from above> --confirmation-id <id from above> --approve
```
Expected: `"status": "complete"`, the tool result reflects `hello-from-smoke`,
exit 0.

- [ ] **Step 5: Smoke the jsonl format**

Run:
```bash
uv run decafclaw-ws-smoke send --token "$DECAFCLAW_TOKEN" \
  --prompt "say hi" --format jsonl
```
Expected: one JSON object per line (raw events: `turn_start`, `message_complete`,
`turn_complete`, …), no summary block.

- [ ] **Step 6: Write `docs/ws-smoke.md`**

Document: purpose, install (`uv sync` makes `decafclaw-ws-smoke` available),
auth/token location, the `send` and `respond` actions, the summary JSON shape,
exit-code table, the always-halt confirmation model, flags table, and the v1
out-of-scope list. Pull the canonical wording from
`docs/dev-sessions/2026-05-31-1724-ws-smoke-client/spec.md` so the two agree.

- [ ] **Step 7: Link it from the doc index**

Add a bullet to `docs/index.md` under the appropriate section (developer
tooling / testing) pointing at `ws-smoke.md` with a one-line description.

- [ ] **Step 8: Commit**

```bash
git add docs/ws-smoke.md docs/index.md
git commit -m "docs(ws-smoke): usage guide + index link"
```

Record the manual smoke results (commands run, observed status/exit codes) in
`docs/dev-sessions/2026-05-31-1724-ws-smoke-client/notes.md`.

---

## Task 8: Full check + baseline

**Files:** none (verification gate).

- [ ] **Step 1: Lint + typecheck**

Run: `make check`
Expected: 0 errors. Fix any pyright complaints in the new modules (e.g. the
`websockets` connection type hint — see Task 4 Step 3).

- [ ] **Step 2: Full test suite**

Run: `make test`
Expected: all prior tests still pass plus the 3 new ws_smoke test files.

- [ ] **Step 3: Duration check for the timeout test**

Run: `uv run pytest tests/test_ws_smoke_run.py --durations=10`
Expected: `test_drive_turn_times_out` is ~0.05s, not multiple seconds — confirms
the timeout path uses `asyncio.timeout`, not a real long wait. If it is slow, the
test's `timeout=0.05` argument is not being honored; investigate `drive_turn`.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "chore(ws-smoke): satisfy lint/typecheck"
```

---

## Notes for the implementer

- **No evals.** This is non-LLM-visible dev tooling; per project conventions,
  skip evals.
- **Wire types as source of truth.** `src/decafclaw/web/message_types.json` is
  the contract. If a field name in the recorder disagrees with the manifest,
  the manifest wins — fix the recorder.
- **DRY auth.** Both `connect()` and `create_conversation()` send the same
  `decafclaw_session` cookie; that's the single trust boundary. Don't add a
  second auth path.
- **Keep transport thin.** Resist adding reconnect/backoff (the TUI has it for a
  long-lived interactive session; a smoke run is short-lived and a dropped
  socket should fail loudly — `disconnect`).
