"""Dispatch validation boundary (#1147).

Issue #1147 requires that description text be treated as guidance, not
enforcement, and asks where canonical input validation happens before
dispatch. The answer this file pins: **there is no JSON-Schema gate at tool
dispatch.** ``execute_tool`` checks the allowlist/blocklist and then calls the
tool function with whatever arguments the model produced; enforcement lives in
each tool's own body. The only dispatch-level failure mode is Python's own
signature binding (a ``TypeError`` from a missing or misspelled parameter),
which surfaces as a ``ToolResult`` error.

Pinning this boundary keeps "add schema validation at dispatch" an explicit,
tested decision rather than a silent behavior change. The test tool is
registered via ``ctx.tools.extra`` (the skill-tool slot), which
``execute_tool`` consults first.
"""

import pytest

from decafclaw.config import Config
from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.tools import execute_tool


@pytest.fixture
def ctx(tmp_path):
    config = Config()
    config.agent.data_home = str(tmp_path)
    bus = EventBus()
    c = Context(config=config, event_bus=bus)
    c.conv_id = "test-conv-dispatch"
    return c


async def _echo(ctx, n):
    """Test tool: reports exactly what it received."""
    return f"echo {n!r}"


@pytest.mark.asyncio
async def test_invalid_typed_argument_reaches_tool_function(ctx):
    """A value that violates the schema's type constraint is not rejected at dispatch.

    The tool's canonical schema would say ``n`` must be a number; the model
    sends a string. Dispatch has no schema gate, so the function runs and
    receives the value verbatim — enforcement is the tool body's job.
    """
    ctx.tools.extra["echo"] = _echo

    result = await execute_tool(ctx, "echo", {"n": "not-a-number"})

    assert result.text == "echo 'not-a-number'"


@pytest.mark.asyncio
async def test_missing_required_argument_fails_on_signature_not_schema(ctx):
    """A missing required argument fails on Python's signature binding, not a schema check.

    The error names the expected parameters (so the model can self-correct);
    it is not a JSON-Schema validation verdict, and no validator ran.
    """
    ctx.tools.extra["echo"] = _echo

    result = await execute_tool(ctx, "echo", {})

    assert "[error executing echo" in result.text
    assert "Expected parameters: n" in result.text
