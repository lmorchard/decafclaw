import asyncio
import dataclasses
import os

import pytest

from decafclaw.config import load_config
from decafclaw.terminals import TerminalRegistry, TerminalSession


def _session(**kw) -> TerminalSession:
    base = dict(
        conv_id="c1", tab_id="canvas_1", session_id="s1",
        cwd="/tmp", shell="/bin/sh", pid=123, fd=9,
        buffer=bytearray(), attached=set(), viewports={},
    )
    base.update(kw)
    return TerminalSession(**base)


def test_ring_buffer_caps_from_front():
    cfg = dataclasses.replace(load_config())
    cfg = dataclasses.replace(cfg, terminal=dataclasses.replace(cfg.terminal, buffer_bytes=8))
    reg = TerminalRegistry(cfg)
    s = _session()
    reg._handle_output(s, b"12345")
    reg._handle_output(s, b"6789")   # total 9 bytes → cap 8 → drop 1 from front
    assert bytes(s.buffer) == b"23456789"


@pytest.mark.asyncio
async def test_broadcast_fans_out_and_drops_failing_sink():
    reg = TerminalRegistry(load_config())
    s = _session()
    good = []

    async def good_sink(chunk): good.append(chunk)
    async def bad_sink(chunk): raise RuntimeError("client gone")

    s.attached.add(good_sink)
    s.attached.add(bad_sink)
    reg._handle_output(s, b"hello")
    await asyncio.sleep(0)  # let broadcast task run
    assert good == [b"hello"]
    assert bad_sink not in s.attached   # failing sink removed


@pytest.mark.asyncio
async def test_eof_notify_reports_exited_reason():
    """EOF on the PTY means the shell itself exited — `reason: "exited"`.

    The client keys off `reason` to decide whether to auto-close the canvas
    tab: an `exited` session leaves its final output on screen (you may want
    to read it), while a `no_session` tombstone from the WS route closes
    itself. Without the discriminator both arrive as `session_ended` with a
    null exit status and are indistinguishable.
    """
    reg = TerminalRegistry(load_config())
    # A real fd we own: `_on_eof` closes session.fd, and the shared helper's
    # placeholder fd=9 is a live descriptor belonging to the test runner.
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    s = _session(fd=read_fd)
    seen = []

    async def sink(obj):
        seen.append(obj)

    reg._sessions[(s.conv_id, s.tab_id)] = s
    reg._json_sinks[id(s)] = {"conn": sink}
    # pid 123 is not our child, so waitpid raises and exit_status falls to -1;
    # the reason field is what this test is about.
    await reg._on_eof(s)

    assert seen == [{"type": "session_ended", "reason": "exited", "exit_status": -1}]


def test_viewport_min_computation():
    reg = TerminalRegistry(load_config())
    s = _session(viewports={"a": (120, 40), "b": (80, 24)})
    assert reg._min_viewport(s) == (80, 24)


def test_count_for_conv():
    reg = TerminalRegistry(load_config())
    reg._sessions[("c1", "canvas_1")] = _session()
    reg._sessions[("c1", "canvas_2")] = _session(tab_id="canvas_2")
    reg._sessions[("c2", "canvas_1")] = _session(conv_id="c2")
    assert reg.count_for_conv("c1") == 2
    assert reg.count_for_conv("c2") == 1


# The spawn path deliberately keeps Python 3.13's forkpty DeprecationWarning
# visible in production (terminals.py:71-73) — this fork is safe (chdir+execvpe
# only between fork and exec). Exempt it per-site rather than suite-wide so an
# unaudited forkpty elsewhere still dirties the suite. #638
@pytest.mark.filterwarnings("ignore:.*use of forkpty.*:DeprecationWarning")
@pytest.mark.asyncio
async def test_real_pty_echo_and_cleanup():
    reg = TerminalRegistry(load_config())
    out = bytearray()
    async def sink(chunk): out.extend(chunk)
    # /bin/echo (not $SHELL) — fast, no rc-file noise
    s = await reg.spawn("c1", "canvas_1", "s1", cwd="/tmp", shell="/bin/echo")
    await reg.attach(s, sink, lambda m: asyncio.sleep(0))
    # echo with no args prints a newline then exits → reader hits EOF
    for _ in range(200):
        if reg.get("c1", "canvas_1") is None:
            break
        await asyncio.sleep(0.01)
    assert reg.get("c1", "canvas_1") is None      # cleaned up on EOF
    assert s.exit_status == 0


@pytest.mark.asyncio
async def test_shutdown_all_kills_and_clears(monkeypatch):
    reg = TerminalRegistry(load_config())
    killed = []

    async def fake_kill(session, grace=1.0): killed.append(session.tab_id)
    monkeypatch.setattr(reg, "kill", fake_kill)
    reg._sessions[("c1", "canvas_1")] = _session()
    reg._sessions[("c1", "canvas_2")] = _session(tab_id="canvas_2")
    await reg.shutdown_all()
    assert sorted(killed) == ["canvas_1", "canvas_2"]
    assert reg._sessions == {}


@pytest.mark.asyncio
async def test_kill_sessions_for_conv(monkeypatch):
    reg = TerminalRegistry(load_config())
    killed = []

    async def fake_kill(session, grace=1.0): killed.append((session.conv_id, session.tab_id))
    monkeypatch.setattr(reg, "kill", fake_kill)
    reg._sessions[("c1", "canvas_1")] = _session()
    reg._sessions[("c1", "canvas_2")] = _session(tab_id="canvas_2")
    reg._sessions[("c2", "canvas_1")] = _session(conv_id="c2")
    await reg.kill_sessions_for_conv("c1")
    assert sorted(killed) == [("c1", "canvas_1"), ("c1", "canvas_2")]
    assert list(reg._sessions.keys()) == [("c2", "canvas_1")]


def test_no_agent_side_imports():
    """terminals.py must not be reachable from tools/ or skills/ — the
    load-bearing 'agent cannot touch terminals' guarantee."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "decafclaw"
    # Catches every realistic import spelling: "import decafclaw.terminals",
    # "from decafclaw.terminals import X", "from decafclaw import terminals",
    # "from .terminals import X", "from ..terminals import X".
    import_line_re = re.compile(r"^\s*(import|from)\s+.*\bterminals\b", re.MULTILINE)
    offenders = []
    for sub in ("tools", "skills"):
        for py in (root / sub).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if import_line_re.search(text):
                offenders.append(str(py))
    assert not offenders, f"terminals.py imported by agent-side code: {offenders}"


# ---------------------------------------------------------------------------
# C4 — the object reachable from agent-side code cannot touch a PTY
# ---------------------------------------------------------------------------

def test_agent_terminal_handle_exposes_no_pty_access():
    """C4: Agent-facing terminal handle SHALL expose ONLY get/kill, NOT pty access."""
    # The criterion specifies that the SYSTEM (not a specific import path) SHALL
    # expose to agent tools an object with get/kill but no PTY-access methods.
    # This test verifies that whatever handle is made available to tools (e.g.,
    # via canvas_tools when it needs to kill sessions on close_tab/clear) satisfies
    # the contract.
    #
    # The implementation may choose to:
    # - Provide a restricted façade class (AgentTerminalHandle)
    # - Pass the registry but document/enforce that tools only use get/kill
    # - Create a protocol/interface that tools import
    #
    # This test will look for the expected interface where tools would find it.

    # Try to import what should be the agent-facing handle.
    # The exact import path will be determined during implementation, but the
    # test expects SOME importable object that tools can use.
    try:
        # Attempt 1: Look for an explicit AgentTerminalHandle class
        from decafclaw.terminals import AgentTerminalHandle
        handle = AgentTerminalHandle()
    except (ImportError, AttributeError):
        # Attempt 2: Maybe there's a factory function
        try:
            from decafclaw.terminals import get_agent_terminal_handle
            handle = get_agent_terminal_handle()
        except (ImportError, AttributeError):
            # Implementation doesn't exist yet - fail with clear guidance
            pytest.fail(
                "C4 criterion not met: No agent-facing terminal handle found. "
                "Expected one of:\n"
                "  - from decafclaw.terminals import AgentTerminalHandle\n"
                "  - from decafclaw.terminals import get_agent_terminal_handle\n"
                "This test verifies the handle exposes ONLY get/kill, NOT PTY methods."
            )

    # Verify the handle HAS the required safe methods
    assert hasattr(handle, "get") and callable(handle.get), \
        "Agent terminal handle must provide callable 'get' method"
    assert hasattr(handle, "kill") and callable(handle.kill), \
        "Agent terminal handle must provide callable 'kill' method"

    # Verify the handle does NOT expose forbidden PTY-access methods
    forbidden = ["spawn", "attach", "detach", "write_input", "set_viewport", "shutdown_all"]
    exposed = []
    for method_name in forbidden:
        if hasattr(handle, method_name):
            exposed.append(method_name)

    assert not exposed, \
        f"Agent terminal handle must NOT expose PTY-access methods, but found: {exposed}"
