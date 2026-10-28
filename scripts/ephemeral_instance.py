#!/usr/bin/env python3
"""Ephemeral DecafClaw instance for scripted end-to-end tests (#957).

One command starts a throwaway decafclaw server from *this checkout's* code
in a child process, runs scripted prompts against it with
``decafclaw-client``, prints the client's JSON summary, and tears everything
down — child process and temporary data directory — on every exit path
(success, failure, timeout, Ctrl-C).

Safety properties:

- Temporary ``DATA_HOME`` (fresh temp dir); the live daemon's data is never
  read or written.
- No model credentials: the child gets an explicit minimal environment with
  ``DECACLAW_LOAD_DOTENV=0`` so the checkout's ``.env`` is never loaded, and
  only the scripted LLM provider (#923) is registered.
- No external network, no Mattermost (``mattermost.enabled: false``), no
  heartbeat (empty interval), no schedules (``schedules.enabled: false``),
  no reflection, no vault retrieval (no embedding calls).
- Binds to ``127.0.0.1`` on a free port.

The child is started with the checkout's own interpreter via
``uv run --project <checkout>``, so the code under test is this checkout,
not whatever is installed elsewhere.

Usage (see docs/ephemeral-instance.md):

    uv run python scripts/ephemeral_instance.py \
        --responses tests/fixtures/ephemeral/tool-roundtrip.responses.json \
        --prompt "Write the exact text hello to the file notes.txt in the workspace."

Exit codes: 0-3 pass through from ``decafclaw-client`` (0 complete, 1 error,
2 halted on a confirmation, 3 timeout); 4 is a launcher-level failure
(missing uv, unreadable responses file, child died, server never healthy)
or a client transport/auth failure (the client's own code 4, passed through
unchanged) — see stderr / the client JSON summary for which.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]
AGENT_ID = "decafclaw"
CHILD_MODULE = "decafclaw.testing.ephemeral_server"

# Lines from the child log the launcher always relays to its own stderr so
# callers (and tests) can verify isolation without keeping the temp dir.
_CHILD_LOG_MARKERS = ("ephemeral server:",)


class LauncherError(RuntimeError):
    """Launcher-level failure (exit code 4)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ephemeral_instance",
        description=(
            "Run a scripted conversation against a throwaway decafclaw server "
            "started from this checkout (#957). Full teardown on every exit path."
        ),
    )
    parser.add_argument(
        "--responses",
        required=True,
        help="JSON file of scripted LLM responses (docs/testing.md, scripted provider).",
    )
    parser.add_argument(
        "--prompt",
        action="append",
        default=[],
        help="Prompt text. Repeatable; runs sequentially. (Or --script.)",
    )
    parser.add_argument(
        "--script",
        default=None,
        help="File of prompts, one per line (blank lines skipped).",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-turn client timeout (default 180).")
    parser.add_argument(
        "--health-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for the child's /health endpoint (default 60).",
    )
    parser.add_argument(
        "--stop-timeout",
        type=float,
        default=10.0,
        help="Seconds of SIGTERM grace before SIGKILL on teardown (default 10).",
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=("summary", "jsonl"),
        default="summary",
        help="Client output format (default summary).",
    )
    parser.add_argument("--log-level", default="INFO", help="Child LOG_LEVEL (default INFO).")
    return parser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _collect_prompts(args: argparse.Namespace) -> list[str]:
    prompts = list(args.prompt)
    if args.script:
        script_path = Path(args.script)
        if not script_path.is_file():
            raise LauncherError(f"script file not found: {script_path}")
        prompts += [line.strip() for line in script_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return prompts


def _write_config(temp: Path, port: int) -> Path:
    """Write the ephemeral config.json under the temp DATA_HOME."""
    agent_dir = temp / AGENT_ID
    agent_dir.mkdir(parents=True, exist_ok=True)
    config = {
        # The scripted provider is registered in-process by the child entry
        # point after init_providers (which skips this unknown type); the
        # entry exists so model resolution finds the provider name.
        "providers": {"scripted": {"type": "scripted"}},
        "model_configs": {"default": {"provider": "scripted", "model": "scripted-model"}},
        "default_model": "default",
        "mattermost": {"enabled": False},
        "heartbeat": {"interval": ""},
        "schedules": {"enabled": False},
        "reflection": {"enabled": False},
        # No embedder exists in the child; keep the agent off the embedding
        # path so no turn can attempt an external embedding call.
        "vault_retrieval": {"enabled": False},
        "http": {"enabled": True, "host": "127.0.0.1", "port": port},
    }
    config_path = agent_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def _create_token(temp: Path) -> str:
    """Create a web token in the temp DATA_HOME via the real create_token."""
    from decafclaw.config import Config
    from decafclaw.web.auth import create_token

    cfg = Config()
    cfg.agent.data_home = str(temp)
    cfg.agent.id = AGENT_ID
    return create_token(cfg, "ephemeral")


def _child_env(temp: Path, port: int, log_level: str) -> dict[str, str]:
    """Explicit minimal child environment — no inherited credentials."""
    (temp / "home").mkdir(exist_ok=True)
    (temp / "tmp").mkdir(exist_ok=True)
    (temp / "uv-cache").mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(temp / "home"),
        "TMPDIR": str(temp / "tmp"),
        "UV_CACHE_DIR": str(temp / "uv-cache"),
        "DATA_HOME": str(temp),
        "HTTP_PORT": str(port),
        # Never load the checkout's .env (real credentials, real DATA_HOME).
        "DECACLAW_LOAD_DOTENV": "0",
        "LOG_LEVEL": log_level,
        "PYTHONUNBUFFERED": "1",
    }


def _start_child(
    uv: str, temp: Path, port: int, responses: Path, log_level: str, env: dict[str, str]
) -> subprocess.Popen:
    cmd = [
        uv,
        "run",
        "--project",
        str(CHECKOUT),
        "python",
        "-m",
        CHILD_MODULE,
        "--responses",
        str(responses),
    ]
    log_path = temp / "child.log"
    log_file = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        # Own session so teardown can kill the whole group (uv + server).
        start_new_session=True,
    )


def _wait_healthy(port: int, timeout: float, child: subprocess.Popen) -> None:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rc = child.poll()
        if rc is not None:
            raise LauncherError(f"child process exited early (code {rc}) before becoming healthy")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(0.2)
    raise LauncherError(f"server did not become healthy within {timeout:.0f}s")


def _run_client(
    uv: str, port: int, token: str, prompts: list[str], timeout: float, fmt: str, env: dict[str, str]
) -> int:
    cmd = [
        uv,
        "run",
        "--project",
        str(CHECKOUT),
        "decafclaw-client",
        "send",
        "--token",
        token,
        "--host",
        f"http://127.0.0.1:{port}",
        "--timeout",
        str(timeout),
        "--format",
        fmt,
    ]
    for prompt in prompts:
        cmd += ["--prompt", prompt]
    # stdout/stderr inherited: the client's JSON summary lands on our stdout.
    # Same minimal environment as the child: no inherited credentials.
    return subprocess.run(cmd, env=env).returncode


def _stop_child(child: subprocess.Popen | None, stop_timeout: float) -> None:
    """SIGTERM the child's process group, then SIGKILL after the grace period."""
    if child is None or child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=stop_timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        print(f"ephemeral-test: WARNING: child process {child.pid} survived SIGKILL", file=sys.stderr)
    # Confirm the whole group (uv wrapper + server) is gone.
    try:
        os.killpg(child.pid, 0)
        print(f"ephemeral-test: WARNING: child process group {child.pid} still alive", file=sys.stderr)
    except (ProcessLookupError, PermissionError):
        pass


def _relay_child_log(temp: Path | None, failed: bool) -> None:
    """Relay the child's diagnostic lines (and a tail on failure) to stderr."""
    if temp is None:
        return
    log_path = temp / "child.log"
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        if any(marker in line for marker in _CHILD_LOG_MARKERS):
            print(f"ephemeral-test: child: {line}", file=sys.stderr)
    if failed and lines:
        print("ephemeral-test: child log (last 30 lines):", file=sys.stderr)
        for line in lines[-30:]:
            print(f"ephemeral-test:   {line}", file=sys.stderr)


def _remove_temp(temp: Path | None) -> None:
    if temp is None:
        return
    shutil.rmtree(temp, ignore_errors=True)
    if temp.exists():
        print(f"ephemeral-test: WARNING: could not fully remove {temp}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    uv = shutil.which("uv")
    if uv is None:
        print("ephemeral-test: error: uv not found on PATH", file=sys.stderr)
        return 4

    temp: Path | None = None
    child: subprocess.Popen | None = None
    port = 0
    env: dict[str, str] = {}
    failed = False
    code = 4
    try:
        prompts = _collect_prompts(args)
        if not prompts:
            raise LauncherError("no prompts given (use --prompt or --script)")
        responses = Path(args.responses)
        if not responses.is_file():
            raise LauncherError(f"responses file not found: {responses}")

        port = _free_port()
        temp = Path(tempfile.mkdtemp(prefix="decafclaw-ephemeral-"))
        _write_config(temp, port)
        token = _create_token(temp)
        env = _child_env(temp, port, args.log_level)
        print(
            f"ephemeral-test: data home: {temp}\nephemeral-test: server: http://127.0.0.1:{port}",
            file=sys.stderr,
        )

        child = _start_child(uv, temp, port, responses, args.log_level, env=env)
        print(f"ephemeral-test: child pid: {child.pid}", file=sys.stderr)
        _wait_healthy(port, args.health_timeout, child)
        code = _run_client(uv, port, token, prompts, args.timeout, args.fmt, env=env)
        failed = code not in (0, 2, 3)  # client error (1) or a crash
    except LauncherError as exc:
        print(f"ephemeral-test: error: {exc}", file=sys.stderr)
        code = 4
        failed = True
    except KeyboardInterrupt:
        print("ephemeral-test: interrupted", file=sys.stderr)
        failed = True
        raise
    finally:
        _stop_child(child, args.stop_timeout)
        _relay_child_log(temp, failed=failed or code == 4)
        _remove_temp(temp)
    return code


if __name__ == "__main__":
    sys.exit(main())
