"""End-to-end tests for the ephemeral instance launcher (#957).

Each test here spawns a real child process (a decafclaw server built from
the current checkout, started with ``uv run --project``) and drives it with
``decafclaw-client``. Readiness is detected by polling ``/health`` — no
fixed sleeps — and every test asserts full teardown: no remaining child
process and no remaining temp directory.

The #851 unstubbed-LLM-call guard in ``tests/conftest.py`` covers only the
test process, so the tests additionally verify — from the child's own
diagnostic log lines, which the launcher relays — that the child registered
no provider except the scripted one and never imported a planted ``.env``.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "scripts" / "ephemeral_instance.py"
TOOL_RESPONSES = REPO_ROOT / "tests" / "fixtures" / "ephemeral" / "tool-roundtrip.responses.json"
CONFIRM_RESPONSES = REPO_ROOT / "tests" / "fixtures" / "ephemeral" / "confirmation-halt.responses.json"

TOOL_PROMPT = "Write the exact text hello to the file notes.txt in the workspace."
SHELL_PROMPT = "Run the shell command: echo hello"


def _run_launcher(responses: Path, prompts: list[str], *extra: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(LAUNCHER), "--responses", str(responses)]
    for prompt in prompts:
        cmd += ["--prompt", prompt]
    cmd += list(extra)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300)


def _child_log_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if "ephemeral-test: child:" in line]


def _child_pid_gone(proc: subprocess.CompletedProcess) -> None:
    """The child pid the launcher reported must be dead (its process group was killed)."""
    m = re.search(r"ephemeral-test: child pid: (\d+)", proc.stderr)
    assert m, f"launcher did not report the child pid:\n{proc.stderr}"
    pid = int(m.group(1))
    assert "survived SIGKILL" not in proc.stderr
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return
    pytest.fail(f"child process {pid} still running after launcher exit")


def _temp_dir_gone(proc: subprocess.CompletedProcess) -> None:
    m = re.search(r"ephemeral-test: data home: (\S+)", proc.stderr)
    assert m, f"launcher did not report a temp data home:\n{proc.stderr}"
    assert not Path(m.group(1)).exists(), f"temp dir left behind: {m.group(1)}"


def _assert_scripted_only(proc: subprocess.CompletedProcess) -> None:
    """The child's provider registry contained only the scripted provider."""
    lines = [line for line in _child_log_lines(proc.stderr) if "registered LLM providers" in line]
    assert lines, f"child did not log its provider registry:\n{proc.stderr}"
    assert "['scripted']" in lines[0], lines


def test_ephemeral_tool_roundtrip():
    """Success condition 1: scripted tool round-trip, exit 0, full teardown."""
    proc = _run_launcher(TOOL_RESPONSES, [TOOL_PROMPT])
    assert proc.returncode == 0, proc.stderr

    summary = json.loads(proc.stdout)
    assert summary["status"] == "complete"
    assert summary["errors"] == []
    calls = {t["name"]: t for t in summary["tool_calls"]}
    assert "workspace_write" in calls
    assert calls["workspace_write"]["status"] == "done"

    _child_pid_gone(proc)
    _temp_dir_gone(proc)
    _assert_scripted_only(proc)


def test_ephemeral_confirmation_halt():
    """Success condition 2: the turn halts on a shell confirmation, exit 2."""
    proc = _run_launcher(CONFIRM_RESPONSES, [SHELL_PROMPT])
    assert proc.returncode == 2, proc.stderr

    summary = json.loads(proc.stdout)
    assert summary["status"] == "halted_confirmation"
    assert summary["confirmations"], "expected a recorded confirmation gate"
    conf = summary["confirmations"][0]
    assert conf["tool"] == "shell"
    assert conf["command"] == "echo hello"

    _child_pid_gone(proc)
    _temp_dir_gone(proc)
    _assert_scripted_only(proc)


def test_ephemeral_child_never_healthy(tmp_path):
    """A child that dies at startup (bad responses file): clear error, non-zero
    exit, and the same full teardown."""
    bad = tmp_path / "bad.responses.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    proc = _run_launcher(bad, ["hello"], "--health-timeout", "20")
    assert proc.returncode == 4
    assert "exited early" in proc.stderr or "did not become healthy" in proc.stderr

    _temp_dir_gone(proc)
    assert "survived SIGKILL" not in proc.stderr


@pytest.fixture()
def planted_env():
    """Plant a .env with a fake credential and a fake DATA_HOME in the
    checkout root (where load_dotenv would find it), restoring whatever was
    there afterwards."""
    env_file = REPO_ROOT / ".env"
    saved = env_file.read_bytes() if env_file.exists() else None
    env_file.write_text(
        "DATA_HOME=/fake/planted-data-home\nFAKE_EPHEMERAL_CREDENTIAL=planted-not-real\n",
        encoding="utf-8",
    )
    try:
        yield
    finally:
        if saved is None:
            env_file.unlink(missing_ok=True)
        else:
            env_file.write_bytes(saved)


def test_ephemeral_child_ignores_planted_env(planted_env):
    """The child never loads the checkout .env: the planted credential and
    fake DATA_HOME must not reach it (DECACLAW_LOAD_DOTENV=0)."""
    proc = _run_launcher(TOOL_RESPONSES, [TOOL_PROMPT])
    assert proc.returncode == 0, proc.stderr

    env_lines = [line for line in _child_log_lines(proc.stderr) if "environment variable names" in line]
    assert env_lines, "child did not log its environment variable names"
    assert "FAKE_EPHEMERAL_CREDENTIAL" not in env_lines[0]

    dh_lines = [line for line in _child_log_lines(proc.stderr) if "data home" in line]
    assert dh_lines, "child did not log its data home"
    assert "/fake/planted-data-home" not in dh_lines[0]

    _child_pid_gone(proc)
    _temp_dir_gone(proc)
    _assert_scripted_only(proc)


def test_main_and_child_use_same_startup_function(tmp_path, monkeypatch):
    """`decafclaw.main()` and the child entry point both call decafclaw.start_app."""
    import decafclaw
    from decafclaw.testing import ephemeral_server

    calls: list = []

    def fake_start_app(extra_provider_hook=None):
        calls.append(extra_provider_hook)

    monkeypatch.setattr(decafclaw, "start_app", fake_start_app)

    old_argv = sys.argv
    try:
        sys.argv = ["decafclaw"]
        decafclaw.main()
    finally:
        sys.argv = old_argv

    assert len(calls) == 1
    assert calls[0] is None  # main() registers no extra provider

    responses = tmp_path / "responses.json"
    responses.write_text('[{"content": "hi"}]', encoding="utf-8")
    rc = ephemeral_server.main(["--responses", str(responses)])
    assert rc == 0
    assert len(calls) == 2
    assert callable(calls[1])  # the child registers the scripted provider via the hook
