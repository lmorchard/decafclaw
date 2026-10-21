"""Tests for scripts/run_dev.py — the `make run-dev` worktree launcher (#1096).

The script is exercised end-to-end as a subprocess against throwaway git
checkouts in tmp_path (the same pattern as test_npm_policy.py). The app
launch is intercepted with a fake `uv` on PATH that prints the arguments
and environment it received and exits, so no real app process is started.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_DEV = REPO_ROOT / "scripts" / "run_dev.py"

# Fake `uv`: prints its args and the env run-dev must have set, then exits.
FAKE_UV = """#!/bin/bash
echo "STUB_UV_CALLED $*"
env | grep -E '^(DATA_HOME|MATTERMOST_ENABLED|EMAIL_ENABLED|NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED|HEARTBEAT_INTERVAL|HTTP_ENABLED|HTTP_PORT)=' | sort
"""


def git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def run_script(cwd: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(RUN_DEV)],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


@pytest.fixture
def main_clone(tmp_path: Path) -> Path:
    """A throwaway primary git checkout (no worktrees)."""
    root = tmp_path / "main"
    root.mkdir()
    git(["-c", "init.defaultBranch=main", "init"], root)
    (root / "README.md").write_text("probe", encoding="utf-8")
    git(["add", "."], root)
    git(["-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-m", "init"], root)
    return root


def add_worktree(main_root: Path, tmp_path: Path) -> Path:
    """Add a detached linked worktree of the throwaway checkout."""
    worktree = tmp_path / "worktree"
    git(["worktree", "add", "--detach", str(worktree)], main_root)
    return worktree


def add_fake_uv(tmp_path: Path) -> dict[str, str]:
    """Put a fake `uv` executable first on PATH; return the env override."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    stub = stub_dir / "uv"
    stub.write_text(FAKE_UV, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return {"PATH": f"{stub_dir}{os.pathsep}{os.environ['PATH']}"}


def seed_main_clone_config(main_root: Path) -> Path:
    """Create the main clone's data/decafclaw/config.json seed source."""
    agent_dir = main_root / "data" / "decafclaw"
    agent_dir.mkdir(parents=True)
    config = agent_dir / "config.json"
    config.write_text('{"default_model": "probe-model"}', encoding="utf-8")
    return config


def test_refuses_to_run_from_main_clone(main_clone: Path):
    result = run_script(main_clone)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "refusing" in combined
    assert "main clone" in combined
    assert not (main_clone / "data-dev").exists()


def test_not_a_git_checkout(tmp_path: Path):
    bare = tmp_path / "notgit"
    bare.mkdir()
    result = run_script(bare)
    assert result.returncode == 1
    assert "not a git checkout" in result.stdout + result.stderr


def test_seeds_config_and_launches_with_disabled_transports(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    source = seed_main_clone_config(main_clone)
    # A second file in the main clone's agent dir must NOT be copied.
    (source.parent / "web_tokens.json").write_text('{"t": "x"}', encoding="utf-8")
    # A stale DATA_HOME in the worktree .env must not win over the seeded one.
    (worktree / ".env").write_text("DATA_HOME=/stale/should/not/win\nHTTP_PORT=18881\n", encoding="utf-8")

    result = run_script(worktree, add_fake_uv(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    seeded = worktree / "data-dev" / "decafclaw" / "config.json"
    assert seeded.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert not (worktree / "data-dev" / "decafclaw" / "web_tokens.json").exists()

    out = result.stdout
    assert "STUB_UV_CALLED run decafclaw" in out
    assert f"DATA_HOME={worktree / 'data-dev'}" in out
    assert "MATTERMOST_ENABLED=false" in out
    assert "EMAIL_ENABLED=false" in out
    assert "NOTIFICATIONS_CHANNELS_MATTERMOST_DM_ENABLED=false" in out
    assert "HEARTBEAT_INTERVAL=0" in out
    assert "HTTP_ENABLED=true" in out
    assert "HTTP_PORT=18881" in out


def test_seed_runs_once_and_does_not_overwrite(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    seed_main_clone_config(main_clone)
    (worktree / ".env").write_text("HTTP_PORT=18882\n", encoding="utf-8")
    extra = add_fake_uv(tmp_path)

    first = run_script(worktree, extra)
    assert first.returncode == 0, first.stdout + first.stderr

    seeded = worktree / "data-dev" / "decafclaw" / "config.json"
    seeded.write_text("user-modified", encoding="utf-8")

    second = run_script(worktree, extra)
    assert second.returncode == 0, second.stdout + second.stderr
    assert seeded.read_text(encoding="utf-8") == "user-modified"
    assert "already seeded" in second.stdout


def test_missing_seed_source_fails_cleanly(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    (worktree / ".env").write_text("HTTP_PORT=18883\n", encoding="utf-8")
    result = run_script(worktree, add_fake_uv(tmp_path))
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "seed source not found" in combined
    assert not (worktree / "data-dev").exists()


def test_port_falls_back_to_default_without_env_file(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    seed_main_clone_config(main_clone)
    result = run_script(worktree, add_fake_uv(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP_PORT=18880" in result.stdout
    assert "no .env in the worktree root" in result.stderr


def test_port_from_quoted_env_value(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    seed_main_clone_config(main_clone)
    (worktree / ".env").write_text('HTTP_PORT="18900"\n', encoding="utf-8")
    result = run_script(worktree, add_fake_uv(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP_PORT=18900" in result.stdout


def test_invalid_port_falls_back_to_default(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    seed_main_clone_config(main_clone)
    (worktree / ".env").write_text("HTTP_PORT=not-a-port\n", encoding="utf-8")
    result = run_script(worktree, add_fake_uv(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HTTP_PORT=18880" in result.stdout


def test_agent_id_follows_bootstrap_env(main_clone: Path, tmp_path: Path):
    worktree = add_worktree(main_clone, tmp_path)
    agent_dir = main_clone / "data" / "custom-agent"
    agent_dir.mkdir(parents=True)
    config = agent_dir / "config.json"
    config.write_text('{"agent": {"id": "custom-agent"}}', encoding="utf-8")
    (worktree / ".env").write_text("HTTP_PORT=18884\n", encoding="utf-8")

    result = run_script(worktree, {**add_fake_uv(tmp_path), "AGENT_ID": "custom-agent"})

    assert result.returncode == 0, result.stdout + result.stderr
    seeded = worktree / "data-dev" / "custom-agent" / "config.json"
    assert seeded.read_text(encoding="utf-8") == config.read_text(encoding="utf-8")
    assert f"DATA_HOME={worktree / 'data-dev'}" in result.stdout


def test_agent_id_from_worktree_env_file(main_clone: Path, tmp_path: Path):
    # The app reads AGENT_ID after load_dotenv(), so a .env value must steer the seed too.
    worktree = add_worktree(main_clone, tmp_path)
    agent_dir = main_clone / "data" / "dotenv-agent"
    agent_dir.mkdir(parents=True)
    config = agent_dir / "config.json"
    config.write_text('{"agent": {"id": "dotenv-agent"}}', encoding="utf-8")
    (worktree / ".env").write_text("AGENT_ID=dotenv-agent\nHTTP_PORT=18885\n", encoding="utf-8")

    env = add_fake_uv(tmp_path)
    result = run_script(worktree, env)

    assert result.returncode == 0, result.stdout + result.stderr
    seeded = worktree / "data-dev" / "dotenv-agent" / "config.json"
    assert seeded.read_text(encoding="utf-8") == config.read_text(encoding="utf-8")
