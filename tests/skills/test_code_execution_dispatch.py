"""Phase 3 dispatch tests for the `code_execution` skill.

Exercises the integration path end-to-end:

    subprocess script
      -> dc.<tool>(...)
        -> RPC line
          -> _serve_rpc (allowlist + counter)
            -> _make_tool_handler closure
              -> execute_tool
                -> real tool function
              -> ToolResult
            -> {text, data, error} dict
          -> RPC line
        -> ToolResultProxy
      -> .text / .error / .data
    -> assertions on sandbox.stdout / sandbox.tool_calls

No mocking of `execute_tool` or the tool registry — real ctx, real workspace,
real vault.
"""

from __future__ import annotations

import pytest

from decafclaw.notes import append_note
from decafclaw.skills.code_execution import _sandbox
from decafclaw.skills.code_execution.tools import (
    SANDBOX_ALLOWED_TOOLS,
    _make_tool_handler,
)
from decafclaw.skills.code_execution.tools import SkillConfig as CodeExecSettings
from decafclaw.skills.vault.tools import TOOLS as VAULT_TOOLS

# A fast, generous timeout for the sandbox — these tests do real IPC but no
# network calls. 10s is far more than needed; lets the kernel hand out tempdir
# resources without flaking on busy parallel workers.
_SETTINGS = CodeExecSettings(timeout_seconds=10.0)


@pytest.fixture
def dispatch_ctx(ctx, tmp_path):
    """A ctx with a real workspace + vault layout and vault/notes tools wired
    into ctx.tools.extra.

    Mimics what the skill loader does at runtime: writes the vault skill's
    TOOLS dict into ctx.tools.extra so `execute_tool` resolves
    `vault_read` / `vault_write` / etc. Notes are in the global TOOLS
    registry already, so they don't need to be added here.
    """
    # workspace + vault directories on disk
    ctx.config.workspace_path.mkdir(parents=True, exist_ok=True)
    (ctx.config.workspace_path / "conversations").mkdir(
        parents=True, exist_ok=True
    )
    ctx.config.vault_root.mkdir(parents=True, exist_ok=True)
    ctx.config.vault_agent_dir.mkdir(parents=True, exist_ok=True)

    # Inject vault tools into ctx.tools.extra exactly as skill activation does.
    ctx.tools.extra.update(VAULT_TOOLS)
    return ctx


async def _run(ctx, code: str) -> _sandbox.SandboxResult:
    """Spin the sandbox with the real Phase 3 handler + allowlist."""
    return await _sandbox.run_script(
        ctx=ctx,
        code=code,
        settings=_SETTINGS,
        handler=_make_tool_handler(ctx),
        allowed=SANDBOX_ALLOWED_TOOLS,
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dc_notes_read_round_trip(dispatch_ctx):
    """Seed a per-conversation note, then read it back through the sandbox.

    Exercises: notes_read tool -> ToolResult.text -> handler -> RPC ->
    ToolResultProxy.text -> script stdout.
    """
    append_note(dispatch_ctx.config, dispatch_ctx.conv_id, "phase3 marker entry")

    script = (
        "from decafclaw_tools import dc\n"
        "r = dc.notes_read(limit=10)\n"
        "print(r.text)\n"
    )
    result = await _run(dispatch_ctx, script)

    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert "phase3 marker entry" in result.stdout, (
        f"seeded note not in stdout: {result.stdout!r}"
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "notes_read"
    assert result.tool_calls[0]["ok"] is True


@pytest.mark.asyncio
async def test_dc_workspace_list_returns_data(dispatch_ctx):
    """Write a file in workspace, list it via dc.workspace_list, verify the
    filename rides back through the RPC."""
    (dispatch_ctx.config.workspace_path / "marker.txt").write_text("hello")

    script = (
        "from decafclaw_tools import dc\n"
        "r = dc.workspace_list(path='.')\n"
        "print(r.text)\n"
    )
    result = await _run(dispatch_ctx, script)

    assert result.status == "success", (
        f"expected success, got {result.status!r}; stderr={result.stderr!r}"
    )
    assert "marker.txt" in result.stdout, (
        f"marker.txt not in workspace_list output: {result.stdout!r}"
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "workspace_list"
    assert result.tool_calls[0]["ok"] is True


# ---------------------------------------------------------------------------
# Defense in depth: server-side allowlist rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_allowlisted_tool_rejected(dispatch_ctx):
    """A script that bypasses the generated proxy and submits a raw RPC
    request for a non-allowlisted tool must be rejected at the server, never
    reaching `execute_tool`.

    The stub only generates accessors for allowlisted names, so `dc.shell`
    isn't a defined attribute. We use the internal `_call` helper to
    exercise the server-side check directly. The agent's real `shell` tool
    is not registered in this test's ctx.tools.extra, so even a slip-through
    would surface as "unknown tool" — but the assertion is on the explicit
    allowlist error so we know the rejection happens at the right layer.
    """
    script = (
        "from decafclaw_tools import _call, dc\n"
        # First: confirm the proxy did NOT expose `shell`.
        "assert not hasattr(dc, 'shell'), 'shell should not be on dc'\n"
        # Then: bypass the proxy and ask the server directly.
        "r = _call('shell', {'command': 'ls'})\n"
        "print(r.error)\n"
    )
    result = await _run(dispatch_ctx, script)

    assert result.status == "success", (
        f"script crashed; stderr={result.stderr!r}"
    )
    assert "not in sandbox allowlist" in result.stdout, (
        f"expected allowlist rejection in stdout, got: {result.stdout!r}"
    )
    # `calls_made` increments BEFORE the allowlist check (so rejected calls
    # DO count toward `max_tool_calls`), but `call_log` is only appended
    # after a successful dispatch. So `shell` shows up in the counter but
    # never in `tool_calls`.
    assert all(c["tool"] != "shell" for c in result.tool_calls), (
        f"shell should never appear in tool_calls: {result.tool_calls!r}"
    )


# ---------------------------------------------------------------------------
# Confirmation-gated tools fall through to NON_INTERACTIVE_ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vault_write_outside_agent_returns_non_interactive_error(
    dispatch_ctx,
):
    """`vault_write` to a path outside the agent folder triggers the user-
    confirmation gate. The sandbox handler nulls `request_confirmation`, so
    the gate returns NON_INTERACTIVE_ERROR which becomes a `[error: ...]`
    ToolResult — which the handler surfaces as `.error` on the proxy."""
    # No grants, no allowlist entries — path is outside agent/ so it must
    # gate. With request_confirmation=None in the sandbox ctx, the gate
    # short-circuits to NON_INTERACTIVE_ERROR.
    script = (
        "from decafclaw_tools import dc\n"
        "r = dc.vault_write(page='not-agent/foo', content='x')\n"
        "print(r.error or r.text)\n"
    )
    result = await _run(dispatch_ctx, script)

    assert result.status == "success", (
        f"script crashed; stderr={result.stderr!r}"
    )
    assert "requires interactive confirmation" in result.stdout, (
        f"expected NON_INTERACTIVE_ERROR message, got: {result.stdout!r}"
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "vault_write"
    # The tool returned an error ToolResult, so handler emitted error=...,
    # so the server marked the call as not-ok.
    assert result.tool_calls[0]["ok"] is False


# ---------------------------------------------------------------------------
# Tool exception surfacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tabstack_inactive_surfaces_error(dispatch_ctx):
    """When the tabstack skill is NOT activated, its `_client` singleton is
    None and `_get_client()` raises RuntimeError. `execute_tool` catches the
    exception and wraps it in `[error executing ...]`, which the handler
    surfaces as `.error` on the proxy. The string "not initialized" comes
    from the underlying RuntimeError message."""
    # Tabstack tools must be reachable for execute_tool to dispatch them.
    from decafclaw.skills.tabstack.tools import TOOLS as TABSTACK_TOOLS
    dispatch_ctx.tools.extra.update(TABSTACK_TOOLS)

    script = (
        "from decafclaw_tools import dc\n"
        "r = dc.tabstack_extract_markdown(url='x')\n"
        "print(r.error)\n"
    )
    result = await _run(dispatch_ctx, script)

    assert result.status == "success", (
        f"script crashed; stderr={result.stderr!r}"
    )
    assert "not initialized" in result.stdout, (
        f"expected 'not initialized' in stdout, got: {result.stdout!r}"
    )
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["tool"] == "tabstack_extract_markdown"
    assert result.tool_calls[0]["ok"] is False


# ---------------------------------------------------------------------------
# dc-proxy import auto-inject
# ---------------------------------------------------------------------------


def test_ensure_dc_import_prepends_when_missing():
    from decafclaw.skills.code_execution.tools import _ensure_dc_import
    out = _ensure_dc_import("print(dc.ping().text)\n")
    assert out.startswith("from decafclaw_tools import dc\n")
    assert "print(dc.ping().text)" in out


def test_ensure_dc_import_skips_when_already_present():
    from decafclaw.skills.code_execution.tools import _ensure_dc_import
    src = "from decafclaw_tools import dc\nprint(dc.ping().text)\n"
    assert _ensure_dc_import(src) == src
    # also skips when imported under an alias / non-`dc` name
    src2 = "from decafclaw_tools import _call\n_call('vault_read', {})\n"
    assert _ensure_dc_import(src2) == src2


@pytest.mark.asyncio
async def test_tool_code_execution_auto_injects_import(dispatch_ctx):
    """End-to-end: a script that forgot `from decafclaw_tools import dc`
    still runs, and the injected line appears in the rendered tool result
    so the LLM sees the actual code that executed."""
    from decafclaw.skills.code_execution import tools as code_exec_tools

    code_exec_tools._settings = _SETTINGS

    code_no_import = (
        "r = dc.notes_read()\n"
        "print(r.error or 'ok')\n"
    )
    result = await code_exec_tools.tool_code_execution(dispatch_ctx, code_no_import)
    assert result.data is not None
    assert result.data["status"] == "success", (
        f"auto-injected script should run cleanly; stderr={result.data['stderr']!r}"
    )
    # The rendered text MUST show the injected import so the LLM learns
    # the boilerplate by example from its own past turns.
    assert "from decafclaw_tools import dc" in result.text
