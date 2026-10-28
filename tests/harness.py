"""Reusable agent-runtime integration harness (issue #923).

Drives the real ``ConversationManager`` + ``TurnRunner`` stack against the
in-memory scripted LLM provider (``decafclaw.testing.ScriptedProvider``)
with an ephemeral config/data directory, so runtime integration tests can
exercise full orchestration — tool round-trips, confirmation pause/resume,
stream interruption, cancellation — deterministically, without network
access or model credentials.

The LLM path is the *normal* one: ``call_llm`` / ``call_llm_streaming`` →
provider registry → ``ScriptedProvider.complete``. Tests that use the
harness are marked ``@pytest.mark.scripted_provider`` so the #851 guard
opens the agent entry seams while the real network providers stay guarded
(a config that resolves anywhere but the scripted provider still fails
loudly).

Usage::

    @pytest.mark.scripted_provider
    async def test_tool_roundtrip(tmp_path):
        async with AgentHarness(tmp_path) as harness:
            harness.tool("echo", echo_tool, description="Echoes its input.")
            harness.script(
                ScriptedResponse.tool_call("echo", {"text": "hi"}, call_id="call_1"),
                ScriptedResponse.text("Echoed: hi"),
            )
            result = await (await harness.enqueue("Please echo hi."))
            assert result.text == "Echoed: hi"
            # assert on harness.events, harness.provider.requests, harness.archive()
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from decafclaw.archive import read_archive
from decafclaw.config import Config
from decafclaw.config_types import (
    AgentConfig,
    ModelConfig,
    ProviderConfig,
    ReflectionConfig,
    VaultRetrievalConfig,
)
from decafclaw.conversation_manager import ConversationManager, TurnKind
from decafclaw.events import EventBus
from decafclaw.llm.registry import get_provider
from decafclaw.testing import (
    PROVIDER_SCRIPTED,
    ScriptedProvider,
    ScriptedResponse,
    register_scripted_provider,
    unregister_scripted_provider,
)

MODEL_NAME = "harness-model"


class _Waiter:
    __slots__ = ("event", "found", "predicate")

    def __init__(self) -> None:
        self.event = asyncio.Event()
        self.found: list[dict] = []
        self.predicate: Any = None


class AgentHarness:
    """Ephemeral ConversationManager + scripted provider for one test.

    - ``streaming=True`` (default) configures the model for the streaming
      path; pass ``False`` to exercise the non-streaming path with the
      same script.
    - ``reflection=True`` enables self-reflection (issue #1000): the
      judge runs on the same scripted provider, so the script must
      alternate agent responses with judge verdicts
      (``{"pass": true|false, "critique": "..."}`` text responses).
    - ``tools`` / ``tool()``: callables executed by the real
      ``execute_tool`` lookup (``ctx.tools.extra``) and advertised to the
      LLM via ``ctx.tools.extra_definitions``.
    - ``script()``: queue the ScriptedProvider's responses.
    - ``enqueue()``: submit a USER turn; returns the manager's future.
    - ``events``: every event emitted for the harness conversation, in
      order. ``wait_for_event()`` blocks for a typed/matching event.
    - ``archive()``: the persisted conversation messages (JSONL order).
    - ``provider``: the ScriptedProvider instance — inspect
      ``provider.requests`` to assert what the runtime actually sent.

    Close the harness (``async with`` or explicit ``close()``) to shut
    the manager down and drop the provider registration.
    """

    def __init__(
        self,
        tmp_path: Path | str,
        *,
        streaming: bool = True,
        reflection: bool = False,
        conv_id: str = "harness-conv",
    ) -> None:
        self.tmp_path = Path(tmp_path)
        self.stream = streaming
        self.conv_id = conv_id
        self.config = Config(
            agent=AgentConfig(
                data_home=str(self.tmp_path),
                id="harness-agent",
                user_id="harness-tester",
            ),
            reflection=ReflectionConfig(enabled=reflection),
            # The harness data dir has no vault content to retrieve and
            # the scripted provider has no embedder; with the default
            # vault_retrieval enabled, every turn would attempt an embed
            # and the fail-open path would log a WARNING traceback.
            vault_retrieval=VaultRetrievalConfig(enabled=False),
            providers={PROVIDER_SCRIPTED: ProviderConfig(type=PROVIDER_SCRIPTED)},
            model_configs={
                MODEL_NAME: ModelConfig(
                    provider=PROVIDER_SCRIPTED,
                    model=MODEL_NAME,
                    streaming=streaming,
                    # Avoid a get_model_info round-trip; the scripted
                    # provider has no real token limits.
                    context_window_size=200_000,
                )
            },
            default_model=MODEL_NAME,
        )
        self.tools: dict[str, Any] = {}
        self.tool_definitions: list[dict[str, Any]] = []
        self.manager = ConversationManager(self.config, EventBus())
        self.events: list[dict[str, Any]] = []
        self._waiters: list[_Waiter] = []
        self._sub_id = self.manager.subscribe(self.conv_id, self._on_event)
        self.provider: ScriptedProvider | None = None

    # -- setup ---------------------------------------------------------------

    def tool(
        self,
        name: str,
        fn: Any,
        *,
        description: str = "",
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Register a callable as a tool the agent may call.

        ``fn`` takes the turn's Context plus the call arguments
        (``async def fn(ctx, **args) -> ToolResult | str``) and is
        executed by the real tool-dispatch path.
        """
        self.tools[name] = fn
        self.tool_definitions.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters or {"type": "object", "properties": {}},
                },
            }
        )

    def script(self, *responses: ScriptedResponse | Any) -> ScriptedProvider:
        """Queue responses on the ScriptedProvider (created on first use)."""
        if self.provider is None:
            self.provider = register_scripted_provider(PROVIDER_SCRIPTED)
        self.provider.extend(responses)
        return self.provider

    # -- turn driver ----------------------------------------------------------

    def _inject_tools(self, ctx: Any) -> None:
        """context_setup: make harness tools visible to this turn's ctx."""
        if self.tools:
            ctx.tools.extra.update(self.tools)
        for td in self.tool_definitions:
            if td not in ctx.tools.extra_definitions:
                ctx.tools.extra_definitions.append(td)

    async def enqueue(
        self,
        prompt: str,
        *,
        kind: TurnKind = TurnKind.USER,
        **enqueue_kwargs: Any,
    ) -> asyncio.Future:
        """Submit a turn (USER by default). Returns the manager's future —
        await it to get the turn's ToolResult, or interact (respond to a
        confirmation, cancel) before awaiting."""
        return await self.manager.enqueue_turn(
            self.conv_id,
            kind=kind,
            prompt=prompt,
            context_setup=self._inject_tools,
            user_id="harness-tester",
            **enqueue_kwargs,
        )

    async def cancel(self) -> None:
        """Cancel the in-flight turn (manager.cancel_turn)."""
        await self.manager.cancel_turn(self.conv_id)

    async def respond_confirmation(self, confirmation_id: str, approved: bool = True, **kwargs: Any) -> None:
        """Resolve a pending confirmation request."""
        await self.manager.respond_to_confirmation(self.conv_id, confirmation_id, approved, **kwargs)

    # -- observation -----------------------------------------------------------

    @property
    def state(self) -> Any:
        """The ConversationState for this conversation (busy, pending_confirmation, ...)."""
        return self.manager.get_state(self.conv_id)

    def archive(self) -> list[dict[str, Any]]:
        """Persisted conversation messages, in archive order."""
        return read_archive(self.config, self.conv_id)

    async def _on_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        for waiter in self._waiters:
            if not waiter.event.is_set() and waiter.predicate(event):
                waiter.found.append(event)
                waiter.event.set()

    async def wait_for_event(
        self,
        event_type: str | None = None,
        *,
        timeout: float = 15.0,
        **match: Any,
    ) -> dict[str, Any]:
        """Wait for the next event of ``event_type`` whose fields match
        ``**match`` (equality), and return it. Already-observed events
        satisfy the wait. Raises TimeoutError with the recent event types
        on timeout — no polling, no sleeps."""

        def predicate(ev: dict[str, Any]) -> bool:
            if event_type is not None and ev.get("type") != event_type:
                return False
            return all(ev.get(key) == value for key, value in match.items())

        for ev in self.events:
            if predicate(ev):
                return ev
        waiter = _Waiter()
        waiter.predicate = predicate
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            tail = [str(ev.get("type")) for ev in self.events[-10:]]
            raise TimeoutError(
                f"Timed out after {timeout}s waiting for event {event_type!r} {match}; recent events: {tail}"
            ) from None
        finally:
            if waiter in self._waiters:
                self._waiters.remove(waiter)
        return waiter.found[0]

    # -- teardown ---------------------------------------------------------------

    async def close(self) -> None:
        """Shut the manager down and drop the scripted provider registration."""
        try:
            await self.manager.shutdown(timeout=10)
        finally:
            self.manager.unsubscribe(self.conv_id, self._sub_id)
            unregister_scripted_provider(PROVIDER_SCRIPTED)
            self._waiters.clear()

    async def __aenter__(self) -> "AgentHarness":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()


def provider_is_registered(name: str = PROVIDER_SCRIPTED) -> bool:
    """True when a provider is registered under ``name`` (test assertion)."""
    try:
        get_provider(name)
        return True
    except KeyError:
        return False


__all__ = ["AgentHarness", "MODEL_NAME", "provider_is_registered"]
