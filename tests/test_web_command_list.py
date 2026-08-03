"""Frozen acceptance check for issue #139 C2 — the invokable-command list.

The web UI's command autocomplete needs the full set of things `/name` can
invoke: every user-invokable skill plus every MCP prompt, exposed as
``mcp__<server>__<prompt>``, each with its description and argument hint.
That list travels over the EXISTING chat WebSocket as a new message type —
not a new HTTP endpoint — so the check drives the client->server handler
straight out of ``websocket._HANDLERS``.

Contract this file defines (no implementation existed when it was written):

  - client->server wire type ``list_commands`` (no required fields)
  - server->client wire type ``command_list`` with
    ``{"type": "command_list", "commands": [{"name", "description",
    "argument_hint"}, ...]}``
  - handler ``_handle_list_commands`` in ``src/decafclaw/web/websocket.py``,
    registered in ``_HANDLERS`` under ``WSMessageType.LIST_COMMANDS``

``_get_mcp_prompt_commands`` returns ``[]`` whenever ``get_registry()`` is
falsy, so a check that leaned on the developer's live MCP config would answer
differently in CI. The registry is faked here instead. Note the consequence
for the implementation: ``get_registry`` must be resolved at call time (the
call-time ``from .mcp_client import get_registry`` that ``commands.py``
already uses), not bound into ``websocket.py`` at import time.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from decafclaw.events import EventBus
from decafclaw.skills import SkillInfo
from decafclaw.web import websocket
from decafclaw.web.message_types import WSMessageType

REQUEST_TYPE = "list_commands"
RESPONSE_TYPE = "command_list"


# -- Fake MCP registry ---------------------------------------------------------


@dataclass
class _FakePromptArg:
    name: str
    required: bool = False
    description: str = ""


@dataclass
class _FakePrompt:
    name: str
    description: str = ""
    arguments: list = field(default_factory=list)


class _FakeRegistry:
    """Stands in for the live MCP registry: one server, one prompt."""

    def __init__(self, prompts: list[tuple[str, _FakePrompt]]):
        self._prompts = prompts

    def get_prompts(self):
        return list(self._prompts)


DEMO_PROMPT = _FakePrompt(
    name="summarize",
    description="Summarize a block of text",
    arguments=[
        _FakePromptArg(name="text", required=True),
        _FakePromptArg(name="language", required=False),
    ],
)

# A second prompt on a DIFFERENT server. Every value the checks below assert is
# otherwise a literal sitting in this file, so a handler that returns a
# hardcoded reply would pass. Namespacing has to be computed to produce this one.
OTHER_PROMPT = _FakePrompt(
    name="ping",
    description="Ping the other server",
    arguments=[],
)

DREAM_SKILL = SkillInfo(
    name="dream",
    description="Distill the journal into vault pages",
    location=Path("/nonexistent/skills/dream"),
    user_invocable=True,
    argument_hint="[topic]",
    trust_tier="bundled",
)

# Discovered but NOT user-invokable — most skills are. Returning every
# discovered skill would green a lookup-only check, so the reply is asserted to
# exclude this one.
HIDDEN_SKILL = dataclasses.replace(
    DREAM_SKILL, name="hidden", user_invocable=False,
)


@pytest.fixture
def command_state(monkeypatch, config):
    """Server state with one skill command and two MCP prompts available."""
    monkeypatch.setattr(
        "decafclaw.mcp_client.get_registry",
        lambda: _FakeRegistry([("demo", DEMO_PROMPT), ("other", OTHER_PROMPT)]),
    )
    config.discovered_skills = [DREAM_SKILL, HIDDEN_SKILL]
    return {
        "config": config,
        "event_bus": EventBus(),
        "manager": MagicMock(),
    }


async def _request_commands(state) -> dict:
    """Drive the client->server handler and return the single reply."""
    sent: list[dict] = []

    async def ws_send(msg):
        sent.append(msg)

    handler = websocket._HANDLERS[REQUEST_TYPE]
    await handler(ws_send, MagicMock(), "testuser", {"type": REQUEST_TYPE}, state)

    assert len(sent) == 1, f"expected exactly one reply, got {sent!r}"
    return sent[0]


def _by_name(reply: dict) -> dict[str, dict]:
    commands = reply["commands"]
    assert isinstance(commands, list), f"commands must be a list, got {commands!r}"
    return {entry["name"]: entry for entry in commands}


# -- Checks --------------------------------------------------------------------


def test_both_wire_types_are_declared_in_the_manifest():
    """The two types must go through `message_types.json` + `make gen-message-types`.

    `make check-message-types` only detects hand-edits to the *generated*
    files; a type that never entered the manifest produces no diff on
    regeneration, so nothing else in the build would notice bare string
    literals on the wire. These members not existing yet is the correct
    failure at freeze time.
    """
    assert WSMessageType.LIST_COMMANDS == REQUEST_TYPE
    assert WSMessageType.COMMAND_LIST == RESPONSE_TYPE


def test_list_commands_is_dispatchable_from_the_client():
    """A client can actually reach the handler over the existing socket."""
    assert REQUEST_TYPE in websocket._HANDLERS


@pytest.mark.asyncio
async def test_reply_lists_skill_commands(command_state):
    reply = await _request_commands(command_state)

    assert reply["type"] == RESPONSE_TYPE
    entry = _by_name(reply)["dream"]
    assert entry["description"] == "Distill the journal into vault pages"
    assert entry["argument_hint"] == "[topic]"


@pytest.mark.asyncio
async def test_reply_omits_skills_that_are_not_user_invokable(command_state):
    reply = await _request_commands(command_state)

    assert "hidden" not in _by_name(reply)


@pytest.mark.asyncio
async def test_reply_lists_mcp_prompts_with_namespaced_names_and_hints(command_state):
    reply = await _request_commands(command_state)

    entry = _by_name(reply)["mcp__demo__summarize"]
    assert entry["description"] == "Summarize a block of text"
    # Required args render as <angle>, optional as [square] — the same hint
    # shape `!help` already prints for MCP prompts.
    assert entry["argument_hint"] == "<text> [language]"


@pytest.mark.asyncio
async def test_reply_namespaces_every_server_not_just_one(command_state):
    """Deliberately not set-equality on the whole reply: a synthetic `help`
    entry (or any other addition) is a legitimate design and must not
    false-fail this check."""
    names = _by_name(reply := await _request_commands(command_state))

    assert "mcp__other__ping" in names, f"got {sorted(names)}"
    assert names["mcp__other__ping"]["description"] == "Ping the other server"
    assert reply["type"] == RESPONSE_TYPE
