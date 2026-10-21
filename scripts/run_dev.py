#!/usr/bin/env python3
"""Run the app from a worktree with no external side effects (#1096).

Backs the `make run-dev` target. It:

1. Refuses to run from the primary checkout (the "main clone"): running
   there would serve the app against the live agent's setup. Only linked
   git worktrees are accepted.
2. Seeds the worktree's gitignored `data-dev/` directory if missing, with
   a copy of the main clone's `data/{agent_id}/config.json` only (no vault,
   no conversations, no secrets) — enough for the app to find its
   providers/model config.
3. Launches `uv run decafclaw` with:
   - DATA_HOME pointed at the seeded data dir (this real env var wins over
     the .env file, since dotenv loads without overriding existing vars),
   - MATTERMOST_ENABLED=false, EMAIL_ENABLED=false,
     NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED=false (no outbound
     transports),
   - HEARTBEAT_INTERVAL=0 (an interval of 0 disables the heartbeat timer),
   - HTTP_ENABLED=true, with HTTP_PORT taken from the worktree's .env
     (falling back to 18880, the HttpConfig default, when the .env has
     none or the value is unusable).

The worktree's .env (the standard copy from the main clone) still supplies
provider credentials to the app via dotenv. The agent id follows the
app's own bootstrap (AGENT_ID env, default "decafclaw") so the seed lands
where load_config() looks for config.json.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_AGENT_ID = "decafclaw"
DEFAULT_HTTP_PORT = 18880  # matches HttpConfig.port
DATA_DIR_NAME = "data-dev"  # gitignored; the worktree-local agent data home

# Env applied to the launched app. Each key maps 1:1 to a config field
# (see config_types.py), so the app's own resolution order does the rest.
APP_ENV = {
    "MATTERMOST_ENABLED": "false",
    "EMAIL_ENABLED": "false",
    "NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED": "false",
    "HEARTBEAT_INTERVAL": "0",
    "HTTP_ENABLED": "true",
}


def git_output(*args: str) -> str | None:
    """Run a git command in the current directory; return stripped stdout, or None on failure."""
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def locate_checkouts() -> tuple[Path, Path] | None:
    """Return (checkout_root, main_clone_root) for the checkout containing the CWD.

    For a linked worktree the main clone root is the parent of the shared
    `.git` directory; for the primary checkout both are the checkout root.
    Returns None when the CWD is outside a git work tree.

    Note: `--git-common-dir` prints an absolute path from a worktree but a
    CWD-relative one from the primary checkout, so it is resolved before use
    (`--absolute-git-common-dir` does not exist; rev-parse echoes it).
    """
    top = git_output("rev-parse", "--show-toplevel")
    git_dir = git_output("rev-parse", "--absolute-git-dir")
    common_dir = git_output("rev-parse", "--git-common-dir")
    if not (top and git_dir and common_dir):
        return None
    checkout = Path(top)
    common = Path(common_dir).resolve()
    if Path(git_dir) == common:
        return checkout, checkout
    return checkout, common.parent


def seed_data_dir(checkout_root: Path, main_root: Path, agent_id: str) -> tuple[Path, bool]:
    """Seed checkout_root/data-dev/{agent_id}/config.json from the main clone.

    Copies config.json only, and only when the target is missing (a user
    may have edited the seeded copy). Returns (target, created). Raises
    FileNotFoundError when the seed source is missing.
    """
    source = main_root / "data" / agent_id / "config.json"
    if not source.is_file():
        raise FileNotFoundError(f"seed source not found: {source}")
    target = checkout_root / DATA_DIR_NAME / agent_id / "config.json"
    if target.exists():
        return target, False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target, True


def http_port_from_env_file(env_file: Path, default: int = DEFAULT_HTTP_PORT) -> int:
    """Read HTTP_PORT from a dotenv-style file; fall back to the default.

    Handles the plain `KEY=value` lines the worktree .env uses; quoted and
    non-numeric values fall back rather than crash a launch.
    """
    if not env_file.is_file():
        return default
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "HTTP_PORT":
            continue
        port_text = value.strip().strip("\"'")
        try:
            port = int(port_text)
        except ValueError:
            return default
        return port if 0 < port < 65536 else default
    return default


def launch_app(env: dict[str, str]) -> None:
    """Replace this process with `uv run decafclaw` under the given environment.

    Flushes stdio first: exec keeps the file descriptors but drops whatever
    Python has not yet written to them (block-buffered when piped, so the
    seed/status lines above would vanish from `make run-dev | tee log`).
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvpe("uv", ["uv", "run", "decafclaw"], env)


def main() -> int:
    located = locate_checkouts()
    if located is None:
        print("run-dev: not a git checkout; nothing to run.", file=sys.stderr)
        return 1
    checkout_root, main_root = located
    if checkout_root == main_root:
        print(
            "run-dev: refusing to run from the main clone (primary checkout).\n"
            "  Run it from a linked worktree instead (e.g. .claude/worktrees/<branch>/):\n"
            "  it seeds a worktree-local data dir and must not touch the live agent's setup.\n"
            "  From the main clone, use `make dev` (attended, live transports) as usual."
        )
        return 1

    agent_id = os.environ.get("AGENT_ID", DEFAULT_AGENT_ID)
    try:
        target, created = seed_data_dir(checkout_root, main_root, agent_id)
    except FileNotFoundError as exc:
        print(f"run-dev: {exc}", file=sys.stderr)
        return 1
    if created:
        print(f"run-dev: seeded {target} (config.json only, from {main_root})")
    else:
        print(f"run-dev: data dir already seeded; leaving {target} as-is")

    port = http_port_from_env_file(checkout_root / ".env")
    if not (checkout_root / ".env").is_file():
        print(
            "run-dev: warning: no .env in the worktree root (expected the standard copy "
            "from the main clone); provider credentials will be missing",
            file=sys.stderr,
        )
    data_home = checkout_root / DATA_DIR_NAME
    print(f"run-dev: web UI on http://localhost:{port} (DATA_HOME={data_home}, Mattermost/email/DMs/heartbeat off)")

    env = dict(os.environ)
    env["DATA_HOME"] = str(data_home)
    env.update(APP_ENV)
    env["HTTP_PORT"] = str(port)
    try:
        launch_app(env)
    except FileNotFoundError:
        print("run-dev: uv not found on PATH; cannot launch the app.", file=sys.stderr)
        return 1
    return 1  # unreachable: launch_app replaces the process


if __name__ == "__main__":
    sys.exit(main())
