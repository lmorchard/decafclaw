"""OpenCode Scavenger Runner — transparent proxy to remote Scavenger containers."""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

DEFAULT_ENV_ID = "ghflow-worker"


def get_token() -> Optional[str]:
    """Retrieve GitHub token from environment or gh auth token."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        try:
            res = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
            token = res.stdout.strip()
        except Exception:
            token = None
    return token


def collect_env_vars(config_path: Optional[Path] = None) -> Dict[str, str]:
    """Collect runtime credentials to forward in-memory into the remote container."""
    env_vars: Dict[str, str] = {}
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "GEMINI_API_KEY",
        "OPENCODE_MODEL",
    ):
        val = os.environ.get(key)
        if val:
            env_vars[key] = val

    cfg_file = config_path or (Path.home() / ".decafclaw/decafclaw/config.json")
    if cfg_file.is_file():
        try:
            cfg_data = json.loads(cfg_file.read_text(encoding="utf-8"))
            providers = cfg_data.get("providers", {})
            if "OPENAI_API_KEY" not in env_vars:
                openai_cfg = providers.get("openai", {})
                openai_key = openai_cfg.get("api_key")
                if openai_key:
                    env_vars["OPENAI_API_KEY"] = openai_key
                    if openai_cfg.get("url") and "OPENAI_BASE_URL" not in env_vars:
                        # Normalize OpenAI responses URL down to standard v1 base
                        url = str(openai_cfg["url"]).replace("/responses", "")
                        env_vars["OPENAI_BASE_URL"] = url
                    elif "OPENAI_BASE_URL" not in env_vars:
                        env_vars["OPENAI_BASE_URL"] = "https://us.api.openai.com/v1"
            if "ANTHROPIC_API_KEY" not in env_vars:
                anthropic_cfg = providers.get("anthropic", {})
                anthropic_key = anthropic_cfg.get("api_key")
                if anthropic_key:
                    env_vars["ANTHROPIC_API_KEY"] = anthropic_key
                    if anthropic_cfg.get("url") and "ANTHROPIC_BASE_URL" not in env_vars:
                        env_vars["ANTHROPIC_BASE_URL"] = str(anthropic_cfg["url"])
        except Exception as e:
            log.warning(f"Failed to read provider keys from {cfg_file}: {e}")

    gh_tok = get_token()
    if gh_tok:
        env_vars["GH_TOKEN"] = gh_tok
        env_vars["GITHUB_TOKEN"] = gh_tok

    return env_vars


def build_exec_command(
    engine: str,
    container_name: str,
    args: List[str],
    env_vars: Dict[str, str],
) -> List[str]:
    """Build container exec command line with direct environment arguments."""
    cmd: List[str] = [engine, "exec", "-i"]
    for k, v in env_vars.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.extend([container_name, "opencode", *args])
    return cmd


def run_remote_opencode(
    args: List[str],
    env_id: Optional[str] = None,
    leases_file: Optional[Path] = None,
    fleet_file: Optional[Path] = None,
    config_path: Optional[Path] = None,
    stdin: Any = subprocess.DEVNULL,
) -> int:
    """Execute opencode command inside remote scavenger container over SSH."""
    target_env = env_id or os.environ.get("SCAVENGER_ENV_ID") or DEFAULT_ENV_ID

    l_file = leases_file or (Path.home() / ".config/scavenger/leases.json")
    if not l_file.is_file():
        l_file = Path.home() / ".local/state/scavenger/leases.json"

    active_res = None
    if l_file.is_file():
        try:
            leases = json.loads(l_file.read_text(encoding="utf-8"))
            active_res = leases.get(target_env)
        except Exception as e:
            log.warning(f"Could not load leases from {l_file}: {e}")

    if not active_res:
        sys.stderr.write(
            f"Error: Scavenger environment '{target_env}' is not active.\n"
            f"Create it first with: scavenger_env_create or ./scripts/scavenger.py env-create\n"
        )
        return 2

    host_name = active_res.get("host")
    container_name = active_res.get("container_name")
    if not host_name or not container_name:
        sys.stderr.write(f"Error: Incomplete lease reservation for '{target_env}' (missing host/container).\n")
        return 2

    f_file = fleet_file or (Path.home() / ".config/scavenger/fleet.json")
    ssh_alias = host_name
    engine = "podman"
    if f_file.is_file():
        try:
            fleet_data = json.loads(f_file.read_text(encoding="utf-8"))
            for h in fleet_data.get("hosts", []):
                if h.get("name") == host_name:
                    ssh_alias = h.get("ssh_alias") or host_name
                    engine = h.get("container", {}).get("engine", "podman")
                    break
        except Exception as e:
            log.warning(f"Could not load fleet from {f_file}: {e}")

    env_vars = collect_env_vars(config_path)

    # Fast-path for session cleanup: opencode session delete <id>
    if len(args) >= 3 and args[0] == "session" and args[1] == "delete":
        sess_id = args[2]
        remote_cmd = f"{engine} exec {container_name} opencode session delete {sess_id}"
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", ssh_alias, remote_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return 0

    container_cmd = build_exec_command(engine, container_name, args, env_vars)
    remote_script = " ".join(shlex.quote(c) for c in container_cmd)
    ssh_cmd = ["ssh", "-o", "BatchMode=yes", ssh_alias, remote_script]

    proc = subprocess.Popen(
        ssh_cmd,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        if proc.stdout:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
        proc.wait()
        return proc.returncode
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 130


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entrypoint for standalone opencode runner adapter."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        sys.stderr.write("Usage: opencode-scavenger-runner [opencode args...]\n")
        sys.exit(1)

    code = run_remote_opencode(args)
    sys.exit(code)


if __name__ == "__main__":
    main()
