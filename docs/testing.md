# Testing

How to write deterministic runtime tests for the agent: the scripted LLM
provider, the integration harness, and the network guard.

## Unstubbed-LLM-call guard (`tests/conftest.py`)

By default, every test runs with the LLM seams patched to fail loudly
(#851): an unstubbed completion raises `RuntimeError("Unstubbed LLM
call...")` instead of silently hitting a real provider. Opt out only for
tests that intentionally run real provider code with HTTP mocked below
the guard (`@pytest.mark.live_llm`).

## Scripted provider (`decafclaw.testing`)

A test-only in-memory `Provider` that replays queued assistant responses
through the *normal* completion/streaming contract — the same
`complete()` entry point and `on_chunk` event ordering as a real
provider — so tests exercise complete orchestration (agent loop, tool
execution, confirmations, stream interruption, cancellation) without
network access or model credentials. It ships inside the `decafclaw`
package (not just `tests/`) so ephemeral test servers that run their own
process can register it there (#957's child runner).

```python
from decafclaw.testing import (
    ScriptedProvider,
    ScriptedResponse,
    register_scripted_provider,
    unregister_scripted_provider,
)
```

- `ScriptedResponse.text("...")` / `ScriptedResponse.tool_call(name, args)`
  build plain responses; the dataclass also carries `thinking`, `usage`,
  `finish_reason`, `error`, `stream_chunks`, and `hold` (a barrier event
  raced against `cancel_event`).
- `ScriptedProvider(responses)` queues entries in order; each
  `complete()` call consumes one. A callable entry is a *request-aware
  factory*: it receives the captured `ScriptedRequest` and returns the
  `ScriptedResponse` to replay. A bare callable (not wrapped in a list)
  is a persistent factory that answers every call.
- `provider.requests` captures every request exactly as the runtime sent
  it (messages, tool list, streaming mode) — assert on it to verify what
  the agent actually saw.
- Exhaustion raises `ScriptExhaustedError` with an actionable message
  (which call ran out, plus a digest of the calls already made).
- `error` simulates a stream dying (exception instance/class, or a plain
  message string); an `("error", msg)` pair in `stream_chunks` kills the
  stream at that point. Partial delivery becomes
  `finish_reason="error"` with the partial content; nothing delivered
  raises.
- `register_scripted_provider(name="scripted", responses=[...])` puts the
  provider in the global provider registry under `name` and returns it;
  point the config's `providers`/`model_configs` at the same name.
  `unregister_scripted_provider(name)` drops the registration.

## Integration harness (`tests/harness.py`)

`AgentHarness` drives the real `ConversationManager` + `TurnRunner`
stack against the scripted provider with an ephemeral config/data
directory:

```python
@pytest.mark.scripted_provider
async def test_tool_roundtrip(tmp_path):
    async with AgentHarness(tmp_path) as h:
        h.tool("echo", echo_tool, description="Echoes its input.")
        h.script(
            ScriptedResponse.tool_call("echo", {"text": "hi"}, call_id="call_1"),
            ScriptedResponse.text("Echoed: hi"),
        )
        result = await (await h.enqueue("Please echo hi."))
        assert result.text == "Echoed: hi"
        # h.events — event stream in order; h.archive() — persisted
        # messages; h.provider.requests — captured LLM requests.
```

- `enqueue()` submits a turn and returns the manager's future; await it
  for the `ToolResult`. Interact (confirmation responses, cancellation)
  before awaiting to test in-flight turns.
- `wait_for_event("turn_start", ...)` blocks for a typed/matching event
  (no polling, no sleeps).
- `AgentHarness(tmp_path, streaming=False)` exercises the non-streaming
  path with the same script.
- Closing the harness shuts the manager down and drops the scripted
  provider registration.

## `@pytest.mark.scripted_provider`

Marks a test that drives the normal LLM path through the scripted
provider. Under the marker, the agent entry seams (`call_llm` /
`call_llm_streaming`) stay live so the registered provider is reachable,
while the real network providers' `complete()` methods stay guarded — a
config that resolves anywhere but the scripted provider still fails
loudly.
