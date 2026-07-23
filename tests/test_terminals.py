import asyncio
import dataclasses

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
